"""Strict JSON boundaries for sequence schema v3 and result schema v12."""

from __future__ import annotations

import json
from typing import Any, Callable, TextIO, TypeVar

from model_ir import ModelExpression, canonicalize_signal_name

from .model import (
    DerivationCheck,
    DerivationBinding,
    DerivationBindingResult,
    DerivationContinuation,
    DerivationDirective,
    DerivationEvent,
    DerivationEventFlow,
    DerivationInterruptControl,
    DerivationFact,
    DerivationFailure,
    DerivationFrame,
    DerivationPath,
    DerivationResult,
    DerivationScheduler,
    DerivationSwitch,
    DerivationTerm,
    DerivationTuple,
    DerivationRelationEffect,
    DerivationSequence,
    DerivationState,
    DerivationUnit,
    DerivationValue,
    DerivationValidationError,
)


T = TypeVar("T")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DerivationValidationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _object(
    value: object,
    required: frozenset[str],
    path: str,
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if type(value) is not dict:
        raise DerivationValidationError(f"{path} must be an object")
    missing = sorted(required - set(value))
    if missing:
        raise DerivationValidationError(f"{path} is missing field {missing[0]!r}")
    unknown = sorted(set(value) - required - optional)
    if unknown:
        raise DerivationValidationError(
            f"{path} contains unknown field {unknown[0]!r}"
        )
    return value


def _string(value: object, path: str) -> str:
    if type(value) is not str:
        raise DerivationValidationError(f"{path} must be a string")
    return value


def _integer(value: object, path: str) -> int:
    if type(value) is not int:
        raise DerivationValidationError(f"{path} must be an integer")
    return value


def _array(
    value: object, path: str, loader: Callable[[object, str], T]
) -> tuple[T, ...]:
    if type(value) is not list:
        raise DerivationValidationError(f"{path} must be an array")
    return tuple(loader(item, f"{path}[{index}]") for index, item in enumerate(value))


def _name(value: object, path: str) -> tuple[str, ...]:
    return _array(value, path, _string)


def _optional_name(value: object, path: str) -> tuple[str, ...] | None:
    return None if value is None else _name(value, path)


def _expression(value: object, path: str) -> ModelExpression:
    data = _object(value, frozenset({"kind", "value", "children"}), path)
    raw_value = data["value"]
    if raw_value is not None and type(raw_value) not in {str, int}:
        raise DerivationValidationError(
            f"{path}.value must be a string, integer, or null"
        )
    return ModelExpression(
        _string(data["kind"], f"{path}.kind"),
        raw_value,
        _array(data["children"], f"{path}.children", _expression),
    )


def _event(
    value: object, path: str, *, accept_compatibility_aliases: bool
) -> DerivationEvent:
    data = _object(
        value,
        frozenset({"source", "target", "signal", "mode", "arguments"}),
        path,
    )
    signal = _name(data["signal"], f"{path}.signal")
    canonical = canonicalize_signal_name(signal)
    if not accept_compatibility_aliases and canonical != signal:
        raise DerivationValidationError(
            f"{path}.signal must use canonical signal {'::'.join(canonical)}"
        )
    return DerivationEvent(
        source=_name(data["source"], f"{path}.source"),
        target=_name(data["target"], f"{path}.target"),
        signal=signal,
        mode=_string(data["mode"], f"{path}.mode"),
        arguments=_array(data["arguments"], f"{path}.arguments", _expression),
    )


def _sequence_event(value: object, path: str) -> DerivationEvent:
    return _event(value, path, accept_compatibility_aliases=True)


def _load_json(stream: TextIO) -> object:
    try:
        return json.load(
            stream,
            parse_constant=lambda value: (_ for _ in ()).throw(
                DerivationValidationError(f"invalid JSON constant {value!r}")
            ),
            object_pairs_hook=_pairs,
        )
    except json.JSONDecodeError as exc:
        raise DerivationValidationError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def load_derivation_sequence(stream: TextIO) -> DerivationSequence:
    """Load one strict derivation sequence document."""

    raw = _load_json(stream)
    document = _object(raw, frozenset({"schema_version", "events"}), "document")
    return DerivationSequence(
        _integer(document["schema_version"], "schema_version"),
        _array(document["events"], "events", _sequence_event),
    )


def _failure(value: object, path: str) -> DerivationFailure:
    data = _object(
        value, frozenset({"code", "path", "message", "features"}), path
    )
    return DerivationFailure(
        _string(data["code"], f"{path}.code"),
        _string(data["path"], f"{path}.path"),
        _string(data["message"], f"{path}.message"),
        _array(data["features"], f"{path}.features", _string),
    )


def _check(value: object, path: str) -> DerivationCheck:
    data = _object(value, frozenset({"expression", "status"}), path)
    return DerivationCheck(
        _string(data["expression"], f"{path}.expression"),
        _string(data["status"], f"{path}.status"),
    )


def _directive(value: object, path: str) -> DerivationDirective:
    data = _object(value, frozenset({"kind", "message"}), path)
    return DerivationDirective(
        _string(data["kind"], f"{path}.kind"),
        _string(data["message"], f"{path}.message"),
    )


def _fact(value: object, path: str) -> DerivationFact:
    data = _object(value, frozenset({"predicate", "arguments"}), path)
    return DerivationFact(
        _name(data["predicate"], f"{path}.predicate"),
        _array(data["arguments"], f"{path}.arguments", _string),
    )


def _state(value: object, path: str) -> DerivationState:
    data = _object(value, frozenset({"object", "state"}), path)
    return DerivationState(
        _name(data["object"], f"{path}.object"),
        _optional_name(data["state"], f"{path}.state"),
    )


def _frame(value: object, path: str) -> DerivationFrame:
    data = _object(
        value,
        frozenset({"object", "handler", "control_index", "bindings"}),
        path,
    )
    return DerivationFrame(
        _name(data["object"], f"{path}.object"),
        _name(data["handler"], f"{path}.handler"),
        _integer(data["control_index"], f"{path}.control_index"),
        _array(data["bindings"], f"{path}.bindings", _binding),
    )


def _binding(value: object, path: str) -> DerivationBinding:
    data = _object(value, frozenset({"name", "term"}), path)
    return DerivationBinding(
        _string(data["name"], f"{path}.name"),
        _term(data["term"], f"{path}.term"),
    )


def _term(value: object, path: str) -> DerivationTerm:
    data = _object(value, frozenset({"kind", "type", "value"}), path)
    kind = _string(data["kind"], f"{path}.kind")
    raw_value = data["value"]
    if kind == "object":
        raw_value = _name(raw_value, f"{path}.value")
    else:
        raw_value = _string(raw_value, f"{path}.value")
    return DerivationTerm(kind, _name(data["type"], f"{path}.type"), raw_value)


def _binding_result(value: object, path: str) -> DerivationBindingResult:
    data = _object(
        value,
        frozenset(
            {
                "name",
                "type",
                "expression",
                "owner",
                "key",
                "value",
                "status",
                "failure_code",
                "candidates",
            }
        ),
        path,
    )
    failure_code = data["failure_code"]
    if failure_code is not None:
        failure_code = _string(failure_code, f"{path}.failure_code")
    return DerivationBindingResult(
        _string(data["name"], f"{path}.name"),
        _name(data["type"], f"{path}.type"),
        _expression(data["expression"], f"{path}.expression"),
        _name(data["owner"], f"{path}.owner"),
        _term(data["key"], f"{path}.key"),
        None if data["value"] is None else _term(data["value"], f"{path}.value"),
        _string(data["status"], f"{path}.status"),
        failure_code,
        _array(data["candidates"], f"{path}.candidates", _term),
    )


def _relation_effect(value: object, path: str) -> DerivationRelationEffect:
    data = _object(
        value,
        frozenset(
            {"owner", "container", "key", "value", "status", "conflict_values"}
        ),
        path,
    )
    return DerivationRelationEffect(
        _name(data["owner"], f"{path}.owner"),
        _string(data["container"], f"{path}.container"),
        _term(data["key"], f"{path}.key"),
        _term(data["value"], f"{path}.value"),
        _string(data["status"], f"{path}.status"),
        _array(data["conflict_values"], f"{path}.conflict_values", _term),
    )


def _tuple(value: object, path: str) -> DerivationTuple:
    data = _object(
        value, frozenset({"owner", "container", "key", "value"}), path
    )
    return DerivationTuple(
        _name(data["owner"], f"{path}.owner"),
        _string(data["container"], f"{path}.container"),
        _term(data["key"], f"{path}.key"),
        _term(data["value"], f"{path}.value"),
    )


def _value(value: object, path: str) -> DerivationValue:
    data = _object(
        value, frozenset({"object", "field", "values", "collection"}), path
    )
    field = data["field"]
    if field is not None:
        field = _string(field, f"{path}.field")
    if type(data["collection"]) is not bool:
        raise DerivationValidationError(f"{path}.collection must be a boolean")
    return DerivationValue(
        _name(data["object"], f"{path}.object"),
        field,
        _array(data["values"], f"{path}.values", _name),
        data["collection"],
    )


def _continuation(value: object, path: str) -> DerivationContinuation:
    data = _object(
        value,
        frozenset({"root", "frames"}),
        path,
    )
    return DerivationContinuation(
        _name(data["root"], f"{path}.root"),
        _array(data["frames"], f"{path}.frames", _frame),
    )


def _unit(value: object, path: str) -> DerivationUnit:
    data = _object(
        value,
        frozenset(
            {
                "kind",
                "event",
                "state_before",
                "handler",
                "candidate_state",
                "depends_on",
                "drives",
                "directives",
                "ensures",
                "establishes",
                "invariants",
                "state_after",
                "emits",
                "yields",
                "resumes",
                "switches",
                "bindings",
                "relation_effects",
                "status",
            }
        ),
        path,
        frozenset({"failure"}),
    )
    handler = _optional_name(data["handler"], f"{path}.handler")
    if handler is not None:
        canonical_handler = canonicalize_signal_name(handler)
        if canonical_handler != handler:
            raise DerivationValidationError(
                f"{path}.handler must use canonical signal "
                f"{'::'.join(canonical_handler)}"
            )
    return DerivationUnit(
        kind=_string(data["kind"], f"{path}.kind"),
        event=_event(
            data["event"],
            f"{path}.event",
            accept_compatibility_aliases=False,
        ),
        state_before=_optional_name(data["state_before"], f"{path}.state_before"),
        handler=handler,
        candidate_state=_optional_name(
            data["candidate_state"], f"{path}.candidate_state"
        ),
        depends_on=_array(data["depends_on"], f"{path}.depends_on", _check),
        drives=_array(data["drives"], f"{path}.drives", _unit),
        directives=_array(data["directives"], f"{path}.directives", _directive),
        ensures=_array(data["ensures"], f"{path}.ensures", _check),
        establishes=_array(data["establishes"], f"{path}.establishes", _check),
        invariants=_array(data["invariants"], f"{path}.invariants", _check),
        state_after=_optional_name(data["state_after"], f"{path}.state_after"),
        emits=_array(data["emits"], f"{path}.emits", _unit),
        status=_string(data["status"], f"{path}.status"),
        failure=None
        if "failure" not in data
        else _failure(data["failure"], f"{path}.failure"),
        yields=_array(data["yields"], f"{path}.yields", _unit),
        resumes=_array(data["resumes"], f"{path}.resumes", _unit),
        switches=_array(data["switches"], f"{path}.switches", _switch),
        bindings=_array(data["bindings"], f"{path}.bindings", _binding_result),
        relation_effects=_array(
            data["relation_effects"],
            f"{path}.relation_effects",
            _relation_effect,
        ),
    )


def _switch(value: object, path: str) -> DerivationSwitch:
    data = _object(
        value,
        frozenset({"binding", "task", "idle_fallback", "cycle_closed", "after_drives"}),
        path,
    )
    if type(data["idle_fallback"]) is not bool:
        raise DerivationValidationError(f"{path}.idle_fallback must be a boolean")
    if type(data["cycle_closed"]) is not bool:
        raise DerivationValidationError(f"{path}.cycle_closed must be a boolean")
    return DerivationSwitch(
        _string(data["binding"], f"{path}.binding"),
        _name(data["task"], f"{path}.task"),
        data["idle_fallback"],
        data["cycle_closed"],
        _integer(data["after_drives"], f"{path}.after_drives"),
    )


def _scheduler(value: object, path: str) -> DerivationScheduler:
    data = _object(
        value,
        frozenset({"scheduler", "idle_task", "runq"}),
        path,
    )
    return DerivationScheduler(
        _name(data["scheduler"], f"{path}.scheduler"),
        _name(data["idle_task"], f"{path}.idle_task"),
        _array(data["runq"], f"{path}.runq", _name),
    )


def _event_flow(value: object, path: str) -> DerivationEventFlow:
    data = _object(
        value,
        frozenset(
            {
                "flow",
                "cpu",
                "suspended_task_flow",
                "user_runtime",
                "signal",
                "outcome",
            }
        ),
        path,
    )
    return DerivationEventFlow(
        _name(data["flow"], f"{path}.flow"),
        _name(data["cpu"], f"{path}.cpu"),
        _name(data["suspended_task_flow"], f"{path}.suspended_task_flow"),
        _name(data["user_runtime"], f"{path}.user_runtime"),
        _string(data["signal"], f"{path}.signal"),
        _string(data["outcome"], f"{path}.outcome"),
    )


def _interrupt_control(value: object, path: str) -> DerivationInterruptControl:
    data = _object(
        value,
        frozenset({"cpu", "mode", "pending"}),
        path,
    )
    return DerivationInterruptControl(
        _name(data["cpu"], f"{path}.cpu"),
        _string(data["mode"], f"{path}.mode"),
        _array(data["pending"], f"{path}.pending", _string),
    )


def _path(value: object, path: str) -> DerivationPath:
    data = _object(
        value,
        frozenset(
            {
                "status",
                "units",
                "final_state",
                "facts",
                "continuations",
                "final_values",
                "schedulers",
                "current_task_ref",
                "current_cpu_ref",
                "event_flows",
                "interrupt_controls",
                "tuples",
            }
        ),
        path,
        frozenset({"failure"}),
    )
    return DerivationPath(
        status=_string(data["status"], f"{path}.status"),
        units=_array(data["units"], f"{path}.units", _unit),
        final_state=_array(data["final_state"], f"{path}.final_state", _state),
        facts=_array(data["facts"], f"{path}.facts", _fact),
        failure=None
        if "failure" not in data
        else _failure(data["failure"], f"{path}.failure"),
        continuations=_array(data["continuations"], f"{path}.continuations", _continuation),
        final_values=_array(data["final_values"], f"{path}.final_values", _value),
        schedulers=_array(data["schedulers"], f"{path}.schedulers", _scheduler),
        current_task_ref=_optional_name(
            data["current_task_ref"], f"{path}.current_task_ref"
        ),
        current_cpu_ref=_optional_name(
            data["current_cpu_ref"], f"{path}.current_cpu_ref"
        ),
        event_flows=_array(data["event_flows"], f"{path}.event_flows", _event_flow),
        interrupt_controls=_array(
            data["interrupt_controls"],
            f"{path}.interrupt_controls",
            _interrupt_control,
        ),
        tuples=_array(data["tuples"], f"{path}.tuples", _tuple),
    )


def load_derivation_result(stream: TextIO) -> DerivationResult:
    """Load and strictly validate one schema-v12 result document."""

    raw = _load_json(stream)
    document = _object(
        raw,
        frozenset(
            {
                "schema_version",
                "status",
                "paths",
            }
        ),
        "document",
    )
    return DerivationResult(
        schema_version=_integer(document["schema_version"], "schema_version"),
        status=_string(document["status"], "status"),
        paths=_array(document["paths"], "paths", _path),
    )


def _expression_data(expression: ModelExpression) -> dict[str, Any]:
    return {
        "kind": expression.kind,
        "value": expression.value,
        "children": [_expression_data(item) for item in expression.children],
    }


def _event_data(event: DerivationEvent) -> dict[str, Any]:
    return {
        "source": list(event.source),
        "target": list(event.target),
        "signal": list(event.signal),
        "mode": event.mode,
        "arguments": [_expression_data(item) for item in event.arguments],
    }


def _failure_data(failure: DerivationFailure) -> dict[str, Any]:
    return {
        "code": failure.code,
        "path": failure.path,
        "message": failure.message,
        "features": list(failure.features),
    }


def _check_data(check: DerivationCheck) -> dict[str, Any]:
    return {"expression": check.expression, "status": check.status}


def _directive_data(directive: DerivationDirective) -> dict[str, Any]:
    return {"kind": directive.kind, "message": directive.message}


def _continuation_data(item: DerivationContinuation) -> dict[str, Any]:
    return {
        "root": list(item.root),
        "frames": [
            {
                "object": list(frame.object),
                "handler": list(frame.handler),
                "control_index": frame.control_index,
                "bindings": [
                    {"name": binding.name, "term": _term_data(binding.term)}
                    for binding in frame.bindings
                ],
            }
            for frame in item.frames
        ],
    }


def _term_data(term: DerivationTerm) -> dict[str, Any]:
    return {
        "kind": term.kind,
        "type": list(term.type),
        "value": list(term.value) if term.kind == "object" else term.value,
    }


def _binding_result_data(item: DerivationBindingResult) -> dict[str, Any]:
    return {
        "name": item.name,
        "type": list(item.type),
        "expression": _expression_data(item.expression),
        "owner": list(item.owner),
        "key": _term_data(item.key),
        "value": None if item.value is None else _term_data(item.value),
        "status": item.status,
        "failure_code": item.failure_code,
        "candidates": [_term_data(term) for term in item.candidates],
    }


def _relation_effect_data(item: DerivationRelationEffect) -> dict[str, Any]:
    return {
        "owner": list(item.owner),
        "container": item.container,
        "key": _term_data(item.key),
        "value": _term_data(item.value),
        "status": item.status,
        "conflict_values": [_term_data(term) for term in item.conflict_values],
    }


def _unit_data(unit: DerivationUnit) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": unit.kind,
        "event": _event_data(unit.event),
        "state_before": None if unit.state_before is None else list(unit.state_before),
        "handler": None if unit.handler is None else list(unit.handler),
        "candidate_state": (
            None if unit.candidate_state is None else list(unit.candidate_state)
        ),
        "depends_on": [_check_data(item) for item in unit.depends_on],
        "drives": [_unit_data(item) for item in unit.drives],
        "directives": [_directive_data(item) for item in unit.directives],
        "ensures": [_check_data(item) for item in unit.ensures],
        "establishes": [_check_data(item) for item in unit.establishes],
        "invariants": [_check_data(item) for item in unit.invariants],
        "state_after": None if unit.state_after is None else list(unit.state_after),
        "emits": [_unit_data(item) for item in unit.emits],
        "yields": [_unit_data(item) for item in unit.yields],
        "resumes": [_unit_data(item) for item in unit.resumes],
        "switches": [
            {
                "binding": item.binding,
                "task": list(item.task),
                "idle_fallback": item.idle_fallback,
                "cycle_closed": item.cycle_closed,
                "after_drives": item.after_drives,
            }
            for item in unit.switches
        ],
        "bindings": [_binding_result_data(item) for item in unit.bindings],
        "relation_effects": [
            _relation_effect_data(item) for item in unit.relation_effects
        ],
        "status": unit.status,
    }
    if unit.failure is not None:
        data["failure"] = _failure_data(unit.failure)
    return data


def dump_derivation_sequence(sequence: DerivationSequence, stream: TextIO) -> None:
    if not isinstance(sequence, DerivationSequence):
        raise TypeError("sequence must be a DerivationSequence")
    json.dump(
        {
            "schema_version": sequence.schema_version,
            "events": [_event_data(event) for event in sequence.events],
        },
        stream,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    stream.write("\n")


def dump_derivation_result(result: DerivationResult, stream: TextIO) -> None:
    """Write one canonical schema-v12 derivation result followed by a newline."""

    if not isinstance(result, DerivationResult):
        raise TypeError("result must be a DerivationResult")
    def path_data(path: DerivationPath) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": path.status,
            "units": [_unit_data(unit) for unit in path.units],
            "final_state": [
                {
                    "object": list(item.object),
                    "state": None if item.state is None else list(item.state),
                }
                for item in path.final_state
            ],
            "facts": [
                {"predicate": list(item.predicate), "arguments": list(item.arguments)}
                for item in path.facts
            ],
            "continuations": [
                _continuation_data(item) for item in path.continuations
            ],
            "final_values": [
                {
                    "object": list(item.object),
                    "field": item.field,
                    "values": [list(value) for value in item.values],
                    "collection": item.collection,
                }
                for item in path.final_values
            ],
            "schedulers": [
                {
                    "scheduler": list(item.scheduler),
                    "idle_task": list(item.idle_task),
                    "runq": [list(task) for task in item.runq],
                }
                for item in path.schedulers
            ],
            "current_task_ref": None
            if path.current_task_ref is None
            else list(path.current_task_ref),
            "current_cpu_ref": None
            if path.current_cpu_ref is None
            else list(path.current_cpu_ref),
            "event_flows": [
                {
                    "flow": list(item.flow),
                    "cpu": list(item.cpu),
                    "suspended_task_flow": list(item.suspended_task_flow),
                    "user_runtime": list(item.user_runtime),
                    "signal": item.signal,
                    "outcome": item.outcome,
                }
                for item in path.event_flows
            ],
            "interrupt_controls": [
                {
                    "cpu": list(item.cpu),
                    "mode": item.mode,
                    "pending": list(item.pending),
                }
                for item in path.interrupt_controls
            ],
            "tuples": [
                {
                    "owner": list(item.owner),
                    "container": item.container,
                    "key": _term_data(item.key),
                    "value": _term_data(item.value),
                }
                for item in path.tuples
            ],
        }
        if path.failure is not None:
            data["failure"] = _failure_data(path.failure)
        return data

    data: dict[str, Any] = {
        "schema_version": result.schema_version,
        "status": result.status,
        "paths": [path_data(path) for path in result.paths],
    }
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")
