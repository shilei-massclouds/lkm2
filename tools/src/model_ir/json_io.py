"""Strict JSON loading and canonical JSON output for Model IR."""

from __future__ import annotations

import json
from typing import Any, TextIO

from .model import ModelEntry, ModelIR, ModelIRValidationError, ModelModule


def _require_object(
    value: object, expected_fields: frozenset[str], path: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ModelIRValidationError(f"{path} must be an object")
    fields = set(value)
    missing = sorted(expected_fields - fields)
    if missing:
        raise ModelIRValidationError(
            f"{path} is missing field {missing[0]!r}"
        )
    unknown = sorted(fields - expected_fields)
    if unknown:
        raise ModelIRValidationError(
            f"{path} contains unknown field {unknown[0]!r}"
        )
    return value


def _load_qualified_name(value: object, path: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise ModelIRValidationError(f"{path} must be an array of identifiers")
    if not value:
        raise ModelIRValidationError(f"{path} must not be empty")
    result: list[str] = []
    for index, part in enumerate(value):
        if type(part) is not str:
            raise ModelIRValidationError(f"{path}[{index}] must be a string")
        result.append(part)
    return tuple(result)


def _reject_constant(value: str) -> None:
    raise ModelIRValidationError(f"invalid JSON constant {value!r}")


def load_model_ir(stream: TextIO) -> ModelIR:
    """Load and strictly validate one version-two Model IR JSON document."""

    try:
        raw = json.load(stream, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ModelIRValidationError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    document = _require_object(
        raw, frozenset({"schema_version", "entry", "modules"}), "document"
    )
    schema_version = document["schema_version"]
    if type(schema_version) is not int:
        raise ModelIRValidationError("schema_version must be an integer")

    entry_data = _require_object(
        document["entry"], frozenset({"origin", "spec"}), "entry"
    )
    modules_data = document["modules"]
    if type(modules_data) is not list:
        raise ModelIRValidationError("modules must be an array")
    modules: list[ModelModule] = []
    for index, module_value in enumerate(modules_data):
        module_data = _require_object(
            module_value, frozenset({"name"}), f"modules[{index}]"
        )
        modules.append(
            ModelModule(
                name=_load_qualified_name(
                    module_data["name"], f"modules[{index}].name"
                )
            )
        )

    return ModelIR(
        schema_version=schema_version,
        entry=ModelEntry(
            origin=_load_qualified_name(entry_data["origin"], "entry.origin"),
            spec=_load_qualified_name(entry_data["spec"], "entry.spec"),
        ),
        modules=tuple(modules),
    )


def dump_model_ir(model: ModelIR, stream: TextIO) -> None:
    """Write canonical, deterministic JSON followed by one newline."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    data = {
        "schema_version": model.schema_version,
        "entry": {
            "origin": list(model.entry.origin),
            "spec": list(model.entry.spec),
        },
        "modules": [{"name": list(module.name)} for module in model.modules],
    }
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")
