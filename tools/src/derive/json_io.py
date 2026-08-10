"""Strict JSON boundaries for sequence schema v2 and result schema v4."""

from __future__ import annotations

import json
from typing import Any, Callable, TextIO, TypeVar

from model_ir import canonicalize_signal_name

from .model import (
    DerivationCheck,
    DerivationContinuation,
    DerivationDirective,
    DerivationEvent,
    DerivationFact,
    DerivationFailure,
    DerivationFrame,
    DerivationResult,
    DerivationSequence,
    DerivationState,
    DerivationUnit,
    DerivationValidationError,
    DerivationYieldToken,
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


def _event(
    value: object, path: str, *, accept_compatibility_aliases: bool
) -> DerivationEvent:
    data = _object(
        value, frozenset({"source", "target", "signal", "mode"}), path
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


def _yield_token(value: object, path: str) -> DerivationYieldToken:
    data = _object(value, frozenset({"object", "generation"}), path)
    return DerivationYieldToken(
        _name(data["object"], f"{path}.object"),
        _integer(data["generation"], f"{path}.generation"),
    )


def _optional_yield_token(
    value: object, path: str
) -> DerivationYieldToken | None:
    return None if value is None else _yield_token(value, path)


def _frame(value: object, path: str) -> DerivationFrame:
    data = _object(
        value,
        frozenset({"object", "handler", "control_index", "generation"}),
        path,
    )
    return DerivationFrame(
        _name(data["object"], f"{path}.object"),
        _name(data["handler"], f"{path}.handler"),
        _integer(data["control_index"], f"{path}.control_index"),
        _integer(data["generation"], f"{path}.generation"),
    )


def _continuation(value: object, path: str) -> DerivationContinuation:
    data = _object(
        value,
        frozenset({"object", "generation", "frames", "yield_token"}),
        path,
    )
    return DerivationContinuation(
        _name(data["object"], f"{path}.object"),
        _integer(data["generation"], f"{path}.generation"),
        _array(data["frames"], f"{path}.frames", _frame),
        _optional_yield_token(data["yield_token"], f"{path}.yield_token"),
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
                "yield_token_created",
                "yield_token_consumed",
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
        yield_token_created=_optional_yield_token(
            data["yield_token_created"], f"{path}.yield_token_created"
        ),
        yield_token_consumed=_optional_yield_token(
            data["yield_token_consumed"], f"{path}.yield_token_consumed"
        ),
    )


def load_derivation_result(stream: TextIO) -> DerivationResult:
    """Load and strictly validate one schema-v4 result document."""

    raw = _load_json(stream)
    document = _object(
        raw,
        frozenset(
            {
                "schema_version",
                "status",
                "units",
                "final_state",
                "facts",
                "continuations",
            }
        ),
        "document",
        frozenset({"failure"}),
    )
    return DerivationResult(
        schema_version=_integer(document["schema_version"], "schema_version"),
        status=_string(document["status"], "status"),
        units=_array(document["units"], "units", _unit),
        final_state=_array(document["final_state"], "final_state", _state),
        facts=_array(document["facts"], "facts", _fact),
        failure=None
        if "failure" not in document
        else _failure(document["failure"], "failure"),
        continuations=_array(
            document["continuations"], "continuations", _continuation
        ),
    )


def _event_data(event: DerivationEvent) -> dict[str, Any]:
    return {
        "source": list(event.source),
        "target": list(event.target),
        "signal": list(event.signal),
        "mode": event.mode,
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


def _yield_token_data(token: DerivationYieldToken) -> dict[str, Any]:
    return {"object": list(token.object), "generation": token.generation}


def _continuation_data(item: DerivationContinuation) -> dict[str, Any]:
    return {
        "object": list(item.object),
        "generation": item.generation,
        "frames": [
            {
                "object": list(frame.object),
                "handler": list(frame.handler),
                "control_index": frame.control_index,
                "generation": frame.generation,
            }
            for frame in item.frames
        ],
        "yield_token": None
        if item.yield_token is None
        else _yield_token_data(item.yield_token),
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
        "yield_token_created": None
        if unit.yield_token_created is None
        else _yield_token_data(unit.yield_token_created),
        "yield_token_consumed": None
        if unit.yield_token_consumed is None
        else _yield_token_data(unit.yield_token_consumed),
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
    """Write one canonical schema-v4 derivation result followed by a newline."""

    if not isinstance(result, DerivationResult):
        raise TypeError("result must be a DerivationResult")
    data: dict[str, Any] = {
        "schema_version": result.schema_version,
        "status": result.status,
        "units": [_unit_data(unit) for unit in result.units],
        "final_state": [
            {
                "object": list(item.object),
                "state": None if item.state is None else list(item.state),
            }
            for item in result.final_state
        ],
        "facts": [
            {"predicate": list(item.predicate), "arguments": list(item.arguments)}
            for item in result.facts
        ],
        "continuations": [
            _continuation_data(item) for item in result.continuations
        ],
    }
    if result.failure is not None:
        data["failure"] = _failure_data(result.failure)
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")
