"""Bounded request witnesses for runtime-v2 branch checks, never a satisfiability proof."""
from __future__ import annotations

import copy
import itertools
import json
import re

from jsonschema import Draft4Validator

from .rest_inputs import MISSING, body_schema, condition_matches, pointer_parts, pointer_value
from .rest_runtime import UUID_PATTERN, TargetError, _resolve

LIMIT = 128


def _update(sample, node, value):
    parameters, body = copy.deepcopy(sample)
    if "param" in node:
        if value is MISSING:
            parameters.pop(node["param"], None)
        else:
            parameters[node["param"]] = value
        return parameters, body
    parts = pointer_parts(node["body"])
    parent = body
    for part in parts[:-1]:
        if isinstance(parent, dict):
            parent = parent.setdefault(part, {})
        elif isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
            parent = parent[int(part)]
        else:
            return None
    last = parts[-1]
    if isinstance(parent, dict):
        if value is MISSING:
            parent.pop(last, None)
        else:
            parent[last] = value
    elif isinstance(parent, list) and last.isdigit() and int(last) < len(parent) and value is not MISSING:
        parent[int(last)] = value
    else:
        return None
    return parameters, body


def _alternatives(node, sample, matching):
    for group in ("all", "any"):
        if group in node:
            sequential = (group == "all") == matching
            candidates = [sample]
            if sequential:
                for child in node[group]:
                    candidates = list(itertools.islice(
                        (out for candidate in candidates for out in _alternatives(child, candidate, matching)), LIMIT))
                yield from candidates
            else:
                yield from itertools.islice(
                    (out for child in node[group] for out in _alternatives(child, sample, matching)), LIMIT)
            return
    current = (sample[0].get(node["param"], MISSING) if "param" in node
               else pointer_value(sample[1], node["body"]))
    if "missing" in node:
        choices = [MISSING] if matching else [current, "witness", 1, False]
    else:
        expected = next(node[k] for k in ("equals", "contains", "startsWith") if k in node)
        if matching:
            choices = [expected] if "equals" in node or not isinstance(expected, str) else [
                expected, expected + "test", "test-" + expected]
        elif isinstance(expected, str):
            choices = [current, MISSING, "unmatched-value", "z", "0", "branch-fallback"]
        elif isinstance(expected, bool):
            choices = [not expected, MISSING]
        else:
            choices = [current, expected + 1, 0, MISSING]
    for value in choices:
        candidate = _update(sample, node, value)
        if candidate is not None and condition_matches(node, *candidate) == matching:
            yield candidate


def request_sample(spec, op, parameters, values, body, selected, prior):
    schema = body_schema(spec, op)

    def valid(sample):
        params, value = sample
        for name, parameter in parameters.items():
            if name not in params:
                if parameter.get("required") or parameter["in"] == "path":
                    return False
                continue
            param_schema = _resolve(spec, parameter["schema"])
            if not Draft4Validator(param_schema).is_valid(params[name]):
                return False
            if param_schema.get("format") == "uuid" and re.fullmatch(UUID_PATTERN, params[name]) is None:
                return False
        return schema is None or Draft4Validator(schema).is_valid(value) or (
            value is None and not _resolve(spec, op["requestBody"]).get("required", False))

    seeds = [(values, body)]
    if schema is not None:
        media = _resolve(spec, op["requestBody"])["content"]["application/json"]
        seeds.extend((values, _resolve(spec, e)["value"]) for e in media.get("examples", {}).values())
    wanted = selected.get("when")
    candidates = (out for seed in seeds for out in (
        _alternatives(wanted, seed, True) if wanted else [seed]))
    queue = list(itertools.islice(candidates, LIMIT))
    seen = set()
    while queue and len(seen) < LIMIT:
        sample = queue.pop(0)
        fingerprint = json.dumps(sample, sort_keys=True, ensure_ascii=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if wanted and not condition_matches(wanted, *sample):
            continue
        earlier = next((rule["when"] for rule in prior if "when" in rule
                        and condition_matches(rule["when"], *sample)), None)
        if earlier is None and valid(sample):
            return sample
        if earlier:
            queue.extend(itertools.islice(_alternatives(earlier, sample, False), LIMIT - len(queue)))
    raise TargetError("Runtime v2 verifier could not derive a valid request for this branch within 128 witnesses. "
                      "Add representative JSON/parameter examples or review overlapping rules. "
                      "No call was sent; this is not proof that the branch is unreachable.")
