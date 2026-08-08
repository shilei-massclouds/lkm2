"""Strict JSON loading and canonical JSON output for Model IR v4."""

from __future__ import annotations

import json
from typing import Any, Callable, TextIO, TypeVar

from .model import (
    ModelAction,
    ModelDeferred,
    ModelEntry,
    ModelExpression,
    ModelExternal,
    ModelField,
    ModelHandlerBlock,
    ModelIR,
    ModelIRValidationError,
    ModelModule,
    ModelObject,
    ModelParameter,
    ModelPredicate,
    ModelReference,
    ModelReferenceAssignment,
    ModelSignal,
    ModelState,
    ModelTransition,
    ModelType,
    ModelTypeExpression,
)


T = TypeVar("T")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelIRValidationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _require_object(
    value: object, expected_fields: frozenset[str], path: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ModelIRValidationError(f"{path} must be an object")
    fields = set(value)
    missing = sorted(expected_fields - fields)
    if missing:
        raise ModelIRValidationError(f"{path} is missing field {missing[0]!r}")
    unknown = sorted(fields - expected_fields)
    if unknown:
        raise ModelIRValidationError(
            f"{path} contains unknown field {unknown[0]!r}"
        )
    return value


def _array(value: object, path: str, loader: Callable[[object, str], T]) -> tuple[T, ...]:
    if type(value) is not list:
        raise ModelIRValidationError(f"{path} must be an array")
    return tuple(loader(item, f"{path}[{index}]") for index, item in enumerate(value))


def _string(value: object, path: str) -> str:
    if type(value) is not str:
        raise ModelIRValidationError(f"{path} must be a string")
    return value


def _qualified_name(value: object, path: str) -> tuple[str, ...]:
    return _array(value, path, _string)


def _expression(value: object, path: str) -> ModelExpression:
    data = _require_object(value, frozenset({"kind", "value", "children"}), path)
    raw_value = data["value"]
    if raw_value is not None and type(raw_value) not in {str, int}:
        raise ModelIRValidationError(f"{path}.value must be a string, integer, or null")
    return ModelExpression(
        kind=_string(data["kind"], f"{path}.kind"),
        value=raw_value,
        children=_array(data["children"], f"{path}.children", _expression),
    )


def _type_expression(value: object, path: str) -> ModelTypeExpression:
    data = _require_object(value, frozenset({"name", "arguments"}), path)
    return ModelTypeExpression(
        name=_qualified_name(data["name"], f"{path}.name"),
        arguments=_array(data["arguments"], f"{path}.arguments", _type_expression),
    )


def _field(value: object, path: str) -> ModelField:
    data = _require_object(value, frozenset({"name", "type"}), path)
    return ModelField(
        name=_string(data["name"], f"{path}.name"),
        type=_type_expression(data["type"], f"{path}.type"),
    )


def _parameter(value: object, path: str) -> ModelParameter:
    data = _require_object(value, frozenset({"name", "type"}), path)
    return ModelParameter(
        name=_string(data["name"], f"{path}.name"),
        type=_type_expression(data["type"], f"{path}.type"),
    )


def _predicate(value: object, path: str) -> ModelPredicate:
    data = _require_object(
        value,
        frozenset({"name", "generic_parameters", "parameters", "return_type", "body"}),
        path,
    )
    body = data["body"]
    return ModelPredicate(
        name=_qualified_name(data["name"], f"{path}.name"),
        generic_parameters=_array(
            data["generic_parameters"], f"{path}.generic_parameters", _string
        ),
        parameters=_array(data["parameters"], f"{path}.parameters", _parameter),
        return_type=_type_expression(data["return_type"], f"{path}.return_type"),
        body=None if body is None else _array(body, f"{path}.body", _expression),
    )


def _type(value: object, path: str) -> ModelType:
    data = _require_object(value, frozenset({"name", "fields"}), path)
    fields = data["fields"]
    return ModelType(
        name=_qualified_name(data["name"], f"{path}.name"),
        fields=None if fields is None else _array(fields, f"{path}.fields", _field),
    )


def _signal(value: object, path: str) -> ModelSignal:
    data = _require_object(
        value, frozenset({"source", "target", "signal", "mode"}), path
    )
    return ModelSignal(
        source=_qualified_name(data["source"], f"{path}.source"),
        target=_qualified_name(data["target"], f"{path}.target"),
        signal=_qualified_name(data["signal"], f"{path}.signal"),
        mode=_string(data["mode"], f"{path}.mode"),
    )


def _deferred(value: object, path: str) -> ModelDeferred:
    data = _require_object(
        value,
        frozenset({"name", "number", "category", "summary", "evidence", "close_when"}),
        path,
    )
    return ModelDeferred(
        name=_string(data["name"], f"{path}.name"),
        number=_string(data["number"], f"{path}.number"),
        category=_expression(data["category"], f"{path}.category"),
        summary=_string(data["summary"], f"{path}.summary"),
        evidence=_array(data["evidence"], f"{path}.evidence", _expression),
        close_when=_string(data["close_when"], f"{path}.close_when"),
    )


def _handler_block(value: object, path: str) -> ModelHandlerBlock:
    data = _require_object(
        value, frozenset({"kind", "expressions", "signals", "deferred"}), path
    )
    deferred = data["deferred"]
    return ModelHandlerBlock(
        kind=_string(data["kind"], f"{path}.kind"),
        expressions=_array(data["expressions"], f"{path}.expressions", _expression),
        signals=_array(data["signals"], f"{path}.signals", _signal),
        deferred=None if deferred is None else _deferred(deferred, f"{path}.deferred"),
    )


def _transition(value: object, path: str) -> ModelTransition:
    data = _require_object(value, frozenset({"signal", "target_state", "blocks"}), path)
    return ModelTransition(
        signal=_qualified_name(data["signal"], f"{path}.signal"),
        target_state=_qualified_name(data["target_state"], f"{path}.target_state"),
        blocks=_array(data["blocks"], f"{path}.blocks", _handler_block),
    )


def _action(value: object, path: str) -> ModelAction:
    data = _require_object(value, frozenset({"signal", "blocks"}), path)
    return ModelAction(
        signal=_qualified_name(data["signal"], f"{path}.signal"),
        blocks=_array(data["blocks"], f"{path}.blocks", _handler_block),
    )


def _expression_block(value: object, path: str) -> tuple[ModelExpression, ...]:
    return _array(value, path, _expression)


def _state(value: object, path: str) -> ModelState:
    data = _require_object(
        value, frozenset({"name", "invariants", "transitions", "actions"}), path
    )
    return ModelState(
        name=_qualified_name(data["name"], f"{path}.name"),
        invariants=_array(data["invariants"], f"{path}.invariants", _expression_block),
        transitions=_array(data["transitions"], f"{path}.transitions", _transition),
        actions=_array(data["actions"], f"{path}.actions", _action),
    )


def _reference_assignment(value: object, path: str) -> ModelReferenceAssignment:
    data = _require_object(value, frozenset({"target", "value"}), path)
    return ModelReferenceAssignment(
        target=_expression(data["target"], f"{path}.target"),
        value=_expression(data["value"], f"{path}.value"),
    )


def _reference(value: object, path: str) -> ModelReference:
    data = _require_object(value, frozenset({"name", "assignments"}), path)
    return ModelReference(
        name=_string(data["name"], f"{path}.name"),
        assignments=_array(
            data["assignments"], f"{path}.assignments", _reference_assignment
        ),
    )


def _object(value: object, path: str) -> ModelObject:
    data = _require_object(
        value,
        frozenset(
            {"name", "base_type", "initial_state", "parent", "source", "attrs", "states", "references"}
        ),
        path,
    )
    initial_state = data["initial_state"]
    parent = data["parent"]
    source = data["source"]
    return ModelObject(
        name=_qualified_name(data["name"], f"{path}.name"),
        base_type=_type_expression(data["base_type"], f"{path}.base_type"),
        initial_state=None if initial_state is None else _qualified_name(initial_state, f"{path}.initial_state"),
        parent=None if parent is None else _expression(parent, f"{path}.parent"),
        source=None if source is None else _expression(source, f"{path}.source"),
        attrs=None if data["attrs"] is None else _array(data["attrs"], f"{path}.attrs", _field),
        states=_array(data["states"], f"{path}.states", _state),
        references=_array(data["references"], f"{path}.references", _reference),
    )


def _external(value: object, path: str) -> ModelExternal:
    data = _require_object(value, frozenset({"name", "signals"}), path)
    return ModelExternal(
        name=_qualified_name(data["name"], f"{path}.name"),
        signals=_array(data["signals"], f"{path}.signals", _signal),
    )


def _module(value: object, path: str) -> ModelModule:
    data = _require_object(
        value, frozenset({"name", "predicates", "types", "objects", "externals"}), path
    )
    return ModelModule(
        name=_qualified_name(data["name"], f"{path}.name"),
        predicates=_array(data["predicates"], f"{path}.predicates", _predicate),
        types=_array(data["types"], f"{path}.types", _type),
        objects=_array(data["objects"], f"{path}.objects", _object),
        externals=_array(data["externals"], f"{path}.externals", _external),
    )


def _reject_constant(value: str) -> None:
    raise ModelIRValidationError(f"invalid JSON constant {value!r}")


def load_model_ir(stream: TextIO) -> ModelIR:
    """Load and strictly validate one Model IR schema-v4 JSON document."""

    try:
        raw = json.load(
            stream, parse_constant=_reject_constant, object_pairs_hook=_object_pairs
        )
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
    return ModelIR(
        schema_version=schema_version,
        entry=ModelEntry(
            origin=_qualified_name(entry_data["origin"], "entry.origin"),
            spec=_qualified_name(entry_data["spec"], "entry.spec"),
        ),
        modules=_array(document["modules"], "modules", _module),
    )


def _expr_data(expression: ModelExpression) -> dict[str, Any]:
    return {
        "kind": expression.kind,
        "value": expression.value,
        "children": [_expr_data(child) for child in expression.children],
    }


def _type_expr_data(type_expression: ModelTypeExpression) -> dict[str, Any]:
    return {
        "name": list(type_expression.name),
        "arguments": [_type_expr_data(item) for item in type_expression.arguments],
    }


def _field_data(field: ModelField) -> dict[str, Any]:
    return {"name": field.name, "type": _type_expr_data(field.type)}


def _signal_data(signal: ModelSignal) -> dict[str, Any]:
    return {
        "source": list(signal.source),
        "target": list(signal.target),
        "signal": list(signal.signal),
        "mode": signal.mode,
    }


def _deferred_data(deferred: ModelDeferred) -> dict[str, Any]:
    return {
        "name": deferred.name,
        "number": deferred.number,
        "category": _expr_data(deferred.category),
        "summary": deferred.summary,
        "evidence": [_expr_data(item) for item in deferred.evidence],
        "close_when": deferred.close_when,
    }


def _block_data(block: ModelHandlerBlock) -> dict[str, Any]:
    return {
        "kind": block.kind,
        "expressions": [_expr_data(item) for item in block.expressions],
        "signals": [_signal_data(item) for item in block.signals],
        "deferred": None if block.deferred is None else _deferred_data(block.deferred),
    }


def _state_data(state: ModelState) -> dict[str, Any]:
    return {
        "name": list(state.name),
        "invariants": [[_expr_data(item) for item in block] for block in state.invariants],
        "transitions": [
            {
                "signal": list(handler.signal),
                "target_state": list(handler.target_state),
                "blocks": [_block_data(block) for block in handler.blocks],
            }
            for handler in state.transitions
        ],
        "actions": [
            {
                "signal": list(handler.signal),
                "blocks": [_block_data(block) for block in handler.blocks],
            }
            for handler in state.actions
        ],
    }


def _module_data(module: ModelModule) -> dict[str, Any]:
    return {
        "name": list(module.name),
        "predicates": [
            {
                "name": list(item.name),
                "generic_parameters": list(item.generic_parameters),
                "parameters": [
                    {"name": parameter.name, "type": _type_expr_data(parameter.type)}
                    for parameter in item.parameters
                ],
                "return_type": _type_expr_data(item.return_type),
                "body": None if item.body is None else [_expr_data(expr) for expr in item.body],
            }
            for item in module.predicates
        ],
        "types": [
            {
                "name": list(item.name),
                "fields": None if item.fields is None else [_field_data(field) for field in item.fields],
            }
            for item in module.types
        ],
        "objects": [
            {
                "name": list(item.name),
                "base_type": _type_expr_data(item.base_type),
                "initial_state": None if item.initial_state is None else list(item.initial_state),
                "parent": None if item.parent is None else _expr_data(item.parent),
                "source": None if item.source is None else _expr_data(item.source),
                "attrs": None if item.attrs is None else [_field_data(field) for field in item.attrs],
                "states": [_state_data(state) for state in item.states],
                "references": [
                    {
                        "name": reference.name,
                        "assignments": [
                            {"target": _expr_data(assignment.target), "value": _expr_data(assignment.value)}
                            for assignment in reference.assignments
                        ],
                    }
                    for reference in item.references
                ],
            }
            for item in module.objects
        ],
        "externals": [
            {"name": list(item.name), "signals": [_signal_data(signal) for signal in item.signals]}
            for item in module.externals
        ],
    }


def dump_model_ir(model: ModelIR, stream: TextIO) -> None:
    """Write canonical, deterministic JSON followed by one newline."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    data = {
        "schema_version": model.schema_version,
        "entry": {"origin": list(model.entry.origin), "spec": list(model.entry.spec)},
        "modules": [_module_data(module) for module in model.modules],
    }
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")
