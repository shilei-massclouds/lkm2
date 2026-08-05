"""Strict JSON boundaries for derivation sequence schema v1 and result schema v1."""

from __future__ import annotations

import json
from typing import Any, TextIO

from .model import (
    DerivationEvent,
    DerivationFailure,
    DerivationResult,
    DerivationSequence,
    DerivationState,
    DerivationTraceStep,
    DerivationValidationError,
)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DerivationValidationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _object(value: object, fields: frozenset[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise DerivationValidationError(f"{path} must be an object")
    missing = sorted(fields - set(value))
    if missing:
        raise DerivationValidationError(f"{path} is missing field {missing[0]!r}")
    unknown = sorted(set(value) - fields)
    if unknown:
        raise DerivationValidationError(f"{path} contains unknown field {unknown[0]!r}")
    return value


def _string(value: object, path: str) -> str:
    if type(value) is not str:
        raise DerivationValidationError(f"{path} must be a string")
    return value


def _name(value: object, path: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise DerivationValidationError(f"{path} must be an array of identifiers")
    return tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))


def _event(value: object, path: str) -> DerivationEvent:
    data = _object(value, frozenset({"source", "target", "signal", "mode"}), path)
    return DerivationEvent(
        source=_name(data["source"], f"{path}.source"),
        target=_name(data["target"], f"{path}.target"),
        signal=_name(data["signal"], f"{path}.signal"),
        mode=_string(data["mode"], f"{path}.mode"),
    )


def _load_json(stream: TextIO) -> object:
    try:
        return json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(DerivationValidationError(f"invalid JSON constant {value!r}")), object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise DerivationValidationError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def load_derivation_sequence(stream: TextIO) -> DerivationSequence:
    """Load one strict derivation sequence document."""

    raw = _load_json(stream)
    document = _object(raw, frozenset({"schema_version", "events"}), "document")
    version = document["schema_version"]
    if type(version) is not int:
        raise DerivationValidationError("schema_version must be an integer")
    events = document["events"]
    if type(events) is not list:
        raise DerivationValidationError("events must be an array")
    return DerivationSequence(version, tuple(_event(item, f"events[{index}]") for index, item in enumerate(events)))


def _failure(value: object, path: str) -> DerivationFailure:
    data = _object(value, frozenset({"code", "event_index", "message", "features"}), path)
    index = data["event_index"]
    if index is not None and type(index) is not int:
        raise DerivationValidationError(f"{path}.event_index must be an integer or null")
    features = data["features"]
    if type(features) is not list:
        raise DerivationValidationError(f"{path}.features must be an array")
    return DerivationFailure(
        _string(data["code"], f"{path}.code"),
        index,
        _string(data["message"], f"{path}.message"),
        tuple(_string(item, f"{path}.features[{i}]") for i, item in enumerate(features)),
    )


def load_derivation_result(stream: TextIO) -> DerivationResult:
    """Load and validate one result document (primarily for round-trip tests)."""

    raw = _load_json(stream)
    document = _object(
        raw,
        frozenset({"schema_version", "status", "trace", "final_state", "pending_signals", "failure"}),
        "document",
    )
    version = document["schema_version"]
    if type(version) is not int:
        raise DerivationValidationError("schema_version must be an integer")
    trace_data = document["trace"]
    if type(trace_data) is not list:
        raise DerivationValidationError("trace must be an array")
    trace = []
    for i, item in enumerate(trace_data):
        path = f"trace[{i}]"
        data = _object(item, frozenset({"index", "event", "state_before", "state_after", "status", "generated"}), path)
        generated = data["generated"]
        if type(generated) is not list:
            raise DerivationValidationError(f"{path}.generated must be an array")
        index = data["index"]
        if type(index) is not int:
            raise DerivationValidationError(f"{path}.index must be an integer")
        trace.append(
            DerivationTraceStep(
                index,
                _event(data["event"], f"{path}.event"),
                None if data["state_before"] is None else _name(data["state_before"], f"{path}.state_before"),
                None if data["state_after"] is None else _name(data["state_after"], f"{path}.state_after"),
                _string(data["status"], f"{path}.status"),
                tuple(_event(event, f"{path}.generated[{j}]") for j, event in enumerate(generated)),
            )
        )
    state_data = document["final_state"]
    if type(state_data) is not list:
        raise DerivationValidationError("final_state must be an array")
    states = []
    for i, item in enumerate(state_data):
        path = f"final_state[{i}]"
        data = _object(item, frozenset({"object", "state"}), path)
        states.append(DerivationState(_name(data["object"], f"{path}.object"), None if data["state"] is None else _name(data["state"], f"{path}.state")))
    pending = document["pending_signals"]
    if type(pending) is not list:
        raise DerivationValidationError("pending_signals must be an array")
    failure_data = document["failure"]
    return DerivationResult(
        version,
        _string(document["status"], "status"),
        tuple(trace),
        tuple(states),
        tuple(_event(item, f"pending_signals[{i}]") for i, item in enumerate(pending)),
        None if failure_data is None else _failure(failure_data, "failure"),
    )


def _event_data(event: DerivationEvent) -> dict[str, Any]:
    return {"source": list(event.source), "target": list(event.target), "signal": list(event.signal), "mode": event.mode}


def dump_derivation_sequence(sequence: DerivationSequence, stream: TextIO) -> None:
    if not isinstance(sequence, DerivationSequence):
        raise TypeError("sequence must be a DerivationSequence")
    json.dump({"schema_version": sequence.schema_version, "events": [_event_data(event) for event in sequence.events]}, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")


def dump_derivation_result(result: DerivationResult, stream: TextIO) -> None:
    """Write one canonical derivation result followed by a newline."""

    if not isinstance(result, DerivationResult):
        raise TypeError("result must be a DerivationResult")
    data = {
        "schema_version": result.schema_version,
        "status": result.status,
        "trace": [
            {
                "index": step.index,
                "event": _event_data(step.event),
                "state_before": None if step.state_before is None else list(step.state_before),
                "state_after": None if step.state_after is None else list(step.state_after),
                "status": step.status,
                "generated": [_event_data(event) for event in step.generated],
            }
            for step in result.trace
        ],
        "final_state": [
            {"object": list(item.object), "state": None if item.state is None else list(item.state)}
            for item in result.final_state
        ],
        "pending_signals": [_event_data(event) for event in result.pending_signals],
        "failure": None
        if result.failure is None
        else {
            "code": result.failure.code,
            "event_index": result.failure.event_index,
            "message": result.failure.message,
            "features": list(result.failure.features),
        },
    }
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")
