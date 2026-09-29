"""Bounded runtime-v2 JSON schemas and mock predicates, shared with verification."""
from __future__ import annotations

import math
import re

from .rest_runtime import TargetError, _cs, _resolve, _string_schema

MAX_BODY_BYTES = 102400
MAX_DEPTH = 8
MAX_SCHEMA_NODES = 128
MAX_CONDITION_NODES = 16
MISSING = object()
ANNOTATIONS = {"description", "title", "example", "default"}


def body_schema(spec, op):
    body = _resolve(spec, op.get("requestBody"))
    if body is None:
        return None
    if not isinstance(body, dict) or set(body) - {"description", "required", "content"}:
        raise TargetError("runtime v2 requestBody supports required and application/json content only")
    if set(body.get("content", {})) != {"application/json"}:
        raise TargetError("runtime v2 requestBody requires exactly application/json")
    media = body["content"]["application/json"]
    if set(media) - {"schema", "example", "examples"}:
        raise TargetError("runtime v2 JSON body encoding/extensions are unsupported")
    count = 0

    def walk(raw, depth):
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > MAX_SCHEMA_NODES:
            raise TargetError("runtime v2 body schema exceeds 8 levels/128 nodes or contains recursive references")
        schema = _resolve(spec, raw)
        if not isinstance(schema, dict):
            raise TargetError("runtime v2 requires explicitly typed body schemas")
        kind = schema.get("type")
        common = {"type", *ANNOTATIONS}
        fields = {
            "object": {"properties", "required", "additionalProperties"},
            "array": {"items", "minItems", "maxItems"},
            "integer": {"minimum", "maximum", "enum"},
            "number": {"minimum", "maximum", "enum"},
            "boolean": {"enum"},
            "string": {"minLength", "maxLength", "pattern", "format", "enum"},
        }
        if kind not in fields or set(schema) - common - fields[kind]:
            raise TargetError("runtime v2 body supports explicit object/array/string/integer/number/boolean "
                              "schemas only; nullable, composition and unknown constraints cannot be ignored")
        result = {key: value for key, value in schema.items() if key not in ANNOTATIONS}
        if kind == "string":
            _string_schema(spec, schema)
        elif kind == "object":
            props = schema.get("properties", {})
            required = schema.get("required", [])
            if (not isinstance(props, dict) or len(props) > 64
                    or any(not isinstance(key, str) or not 1 <= len(key) <= 128
                           or any(ord(c) < 32 for c in key) for key in props)
                    or not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                    or len(set(required)) != len(required) or set(required) - props.keys()
                    or type(schema.get("additionalProperties", True)) is not bool):
                raise TargetError("runtime v2 object requires declared properties, unique required names "
                                  "and boolean additionalProperties")
            result["properties"] = {key: walk(value, depth + 1) for key, value in props.items()}
        elif kind == "array":
            result["items"] = walk(schema.get("items"), depth + 1)
            for key in ("minItems", "maxItems"):
                if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 1000):
                    raise TargetError("runtime v2 array bounds must be integers in 0..1000")
            if schema.get("minItems", 0) > schema.get("maxItems", 1000):
                raise TargetError("runtime v2 array bounds are reversed")
        elif kind in ("integer", "number"):
            for key in ("minimum", "maximum"):
                if key in schema and (type(schema[key]) not in (int, float)
                                       or not math.isfinite(schema[key])
                                       or abs(schema[key]) > 2**53 - 1):
                    raise TargetError("runtime v2 numeric bounds must be finite safe JSON numbers")
            if schema.get("minimum", float("-inf")) > schema.get("maximum", float("inf")):
                raise TargetError("runtime v2 numeric bounds are reversed")
        if "enum" in schema and kind != "string":
            values = schema["enum"]
            types = {"integer": (int,), "number": (int, float), "boolean": (bool,)}.get(kind, ())
            if (not isinstance(values, list) or not 1 <= len(values) <= 64
                    or any(type(v) not in types or (type(v) in (int, float)
                           and (not math.isfinite(v) or abs(v) > 2**53 - 1)) for v in values)):
                raise TargetError("runtime v2 enums require 1..64 scalar values of their declared type")
        return result

    return walk(media.get("schema"), 0)


def pointer_parts(pointer):
    if (not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 512
            or re.search(r"~(?![01])", pointer) or any(ord(c) < 32 for c in pointer)):
        raise TargetError("runtime v2 body selector must be a bounded non-root JSON Pointer, e.g. /items/0/sku")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]
    if len(parts) > MAX_DEPTH:
        raise TargetError("runtime v2 body selector exceeds 8 levels")
    return parts


def pointer_value(body, pointer):
    value = body
    for part in pointer_parts(pointer):
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
            value = value[int(part)] if int(part) < len(value) else MISSING
        else:
            return MISSING
    return value


def _body_selector(spec, op, pointer):
    schema = body_schema(spec, op)
    if schema is None:
        raise TargetError("runtime v2 body rule needs a declared JSON requestBody")
    expression = "b"
    guards = []
    for part in pointer_parts(pointer):
        kind = schema.get("type")
        if kind == "object" and part in schema.get("properties", {}):
            guards.append(f"{expression} != null")
            expression += f"[{_cs(part)}]"
            schema = schema["properties"][part]
        elif kind == "array" and re.fullmatch(r"0|[1-9][0-9]*", part) and int(part) < 1000:
            guards.extend((f"{expression} != null", f"{expression}.Count() > {int(part)}"))
            expression += f"[{int(part)}]"
            schema = schema["items"]
        else:
            raise TargetError("runtime v2 body selector must follow declared properties and bounded array indexes")
    guards.append(f"{expression} != null")
    return schema, expression, " && ".join(guards)


def condition_expression(spec, path, op, condition, bf):
    nodes = 0
    uses_body = False

    def compile_node(node, depth):
        nonlocal nodes, uses_body
        nodes += 1
        if nodes > MAX_CONDITION_NODES or depth > 3 or not isinstance(node, dict):
            raise TargetError("runtime v2 conditions allow at most 16 nodes and 3 nesting levels")
        for group, join in (("all", " && "), ("any", " || ")):
            if group in node:
                children = node[group]
                if set(node) != {group} or not isinstance(children, list) or not 2 <= len(children) <= 8:
                    raise TargetError("runtime v2 all/any requires 2..8 conditions, without sibling fields")
                return "(" + join.join(compile_node(child, depth + 1) for child in children) + ")"
        if "param" in node:
            return bf.xmock_condition(spec, path, op, node, "runtime v2 x-mock")[2:-1]
        operators = [k for k in ("equals", "contains", "startsWith", "missing") if k in node]
        if len(operators) != 1 or set(node) - {"body", *operators, "caseSensitive"} or "body" not in node:
            raise TargetError("runtime v2 condition requires param or body and one supported operator")
        uses_body = True
        schema, value, exists = _body_selector(spec, op, node["body"])
        operator = operators[0]
        sensitive = node.get("caseSensitive", False)
        if type(sensitive) is not bool:
            raise TargetError("runtime v2 caseSensitive must be boolean")
        if operator == "missing":
            if node[operator] is not True or "caseSensitive" in node:
                raise TargetError("runtime v2 missing requires true without caseSensitive")
            return f"!({exists})"
        literal = node[operator]
        kind = schema["type"]
        if kind == "string":
            if (not isinstance(literal, str) or not 1 <= len(literal) <= 512
                    or any(ord(c) < 32 for c in literal)
                    or any(m in literal for m in ("@(", "@{", "{{"))):
                raise TargetError("runtime v2 string comparisons require bounded nonempty literal strings")
            comparison = "StringComparison.Ordinal" + ("" if sensitive else "IgnoreCase")
            if operator == "equals":
                predicate = f"string.Equals((string){value}, {_cs(literal)}, {comparison})"
            elif operator == "contains":
                predicate = f"((string){value}).IndexOf({_cs(literal)}, {comparison}) >= 0"
            else:
                predicate = f"((string){value}).StartsWith({_cs(literal)}, {comparison})"
        elif kind in ("integer", "number", "boolean"):
            types = {"integer": (int,), "number": (int, float), "boolean": (bool,)}[kind]
            if (operator != "equals" or "caseSensitive" in node or type(literal) not in types
                    or (kind != "boolean" and (not math.isfinite(literal) or abs(literal) > 2**53 - 1))):
                raise TargetError("runtime v2 typed body comparisons support equals with a matching finite scalar")
            predicate = (f"(bool){value} == {'true' if literal else 'false'}" if kind == "boolean"
                         else f"(double){value} == {_cs(literal)}")
        else:
            raise TargetError("runtime v2 comparisons require scalar body values; objects/arrays support missing only")
        return f"({exists} && ({predicate}))"

    expression = compile_node(condition, 0)
    if uses_body:
        return ("@{var b = context.Request.Body == null || "
                "context.Request.Body.As<string>(preserveContent: true).Length == 0 ? null : "
                "context.Request.Body.As<JToken>(preserveContent: true);return " + expression + ";}")
    return "@(" + expression + ")"


def condition_matches(condition, values, body=MISSING):
    if "all" in condition:
        return all(condition_matches(c, values, body) for c in condition["all"])
    if "any" in condition:
        return any(condition_matches(c, values, body) for c in condition["any"])
    value = (pointer_value(body, condition["body"]) if "body" in condition
             else values.get(condition["param"], MISSING))
    if condition.get("missing"):
        return value is MISSING or ("param" in condition and value == "")
    if value is MISSING:
        return False
    operator = next(k for k in ("equals", "contains", "startsWith") if k in condition)
    expected = condition[operator]
    if not isinstance(expected, str):
        return type(value) is type(expected) and value == expected or (
            type(value) in (int, float) and type(expected) in (int, float) and value == expected)
    if not isinstance(value, str):
        return False
    actual = value if condition.get("caseSensitive") else value.lower()
    expected = expected if condition.get("caseSensitive") else expected.lower()
    return (actual == expected if operator == "equals" else
            expected in actual if operator == "contains" else actual.startswith(expected))
