"""Offline structural validation using the packaged OpenAPI schema."""
from __future__ import annotations

import json
from importlib.resources import files

from jsonschema import Draft4Validator, FormatChecker
from jsonschema.exceptions import best_match

from .targets import TargetError


def validate_schema(document: dict, kind: str):
    schema = json.loads(files("mcp_openapi_creator_kit").joinpath(
        "schemas", f"{kind}.json").read_text("utf-8"))
    try:
        # Validate the JSON representation, including supported integer YAML
        # response status keys, without modifying the caller's contract/examples.
        normalized = json.loads(json.dumps(document, allow_nan=False))
        error = best_match(Draft4Validator(
            schema, format_checker=FormatChecker()).iter_errors(normalized))
    except (TypeError, ValueError, RecursionError):
        raise TargetError(f"{kind}: invalid YAML/JSON structure or cyclic object") from None
    if error is not None:
        path = "/".join(str(part).replace("~", "~0").replace("/", "~1")
                        for part in error.absolute_path)
        hint = ""
        if error.path and error.path[-1] in ("exclusiveMinimum", "exclusiveMaximum"):
            bound = "minimum" if error.path[-1] == "exclusiveMinimum" else "maximum"
            hint = (f"; OpenAPI 3.0 requires a boolean here; put the numeric bound in "
                    f"'{bound}' and use '{error.path[-1]}: true' for an exclusive bound")
        raise TargetError(
            f"{kind} schema: /{path}: violates {error.validator}{hint}; "
            "check the official schema (payload omitted)")
