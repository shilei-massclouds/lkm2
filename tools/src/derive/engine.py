"""Single-threaded deterministic state derivation."""

from __future__ import annotations

from model_ir import ModelIR, ModelSignal

from .model import (
    RESULT_SCHEMA_VERSION,
    DerivationEvent,
    DerivationFailure,
    DerivationResult,
    DerivationSequence,
    DerivationState,
    DerivationTraceStep,
)


def _event(signal: ModelSignal) -> DerivationEvent:
    return DerivationEvent(signal.source, signal.target, signal.signal, signal.mode)


def _unsupported_features(model: ModelIR) -> tuple[str, ...]:
    features: set[str] = set()
    for model_object in model.objects:
        if model_object.attrs is not None:
            features.add("attrs")
        if model_object.references:
            features.add("reference")
        for state in model_object.states:
            if state.invariants:
                features.add("invariant")
            if state.actions:
                features.add("action")
            for handler in (*state.transitions, *state.actions):
                for block in handler.blocks:
                    if block.kind in {"depends_on", "may_change", "ensures", "deferred"}:
                        features.add(block.kind)
    return tuple(sorted(features))


def _states(model: ModelIR) -> dict[tuple[str, ...], tuple[str, ...] | None]:
    return {model_object.name: model_object.initial_state for model_object in model.objects}


def _final_state(states: dict[tuple[str, ...], tuple[str, ...] | None]) -> tuple[DerivationState, ...]:
    return tuple(DerivationState(name, state) for name, state in sorted(states.items()))


def _failed(
    status: str,
    states: dict[tuple[str, ...], tuple[str, ...] | None],
    trace: list[DerivationTraceStep],
    pending: list[DerivationEvent],
    message: str,
    event_index: int | None,
    features: tuple[str, ...] = (),
) -> DerivationResult:
    return DerivationResult(
        RESULT_SCHEMA_VERSION,
        status,
        tuple(trace),
        _final_state(states),
        tuple(pending),
        DerivationFailure(status, event_index, message, features),
    )


def derive(model: ModelIR, sequence: DerivationSequence) -> DerivationResult:
    """Execute an explicit sequence without scheduling, search, or hidden choices."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    if not isinstance(sequence, DerivationSequence):
        raise TypeError("sequence must be a DerivationSequence")
    states = _states(model)
    unsupported = _unsupported_features(model)
    if unsupported:
        return _failed(
            "unsupported_feature",
            states,
            [],
            [],
            "model uses semantics that derive v1 cannot execute",
            None,
            unsupported,
        )

    objects = {item.name: item for item in model.objects}
    origin = next(item for item in model.externals if item.name == model.entry.origin)
    external_events = tuple(_event(signal) for signal in origin.signals)
    pending: list[DerivationEvent] = []
    trace: list[DerivationTraceStep] = []

    for index, selected in enumerate(sequence.events):
        before = states.get(selected.target)
        if selected.source == model.entry.origin:
            if selected not in external_events:
                trace.append(DerivationTraceStep(index, selected, before, before, "undeclared_external_signal", ()))
                return _failed(
                    "undeclared_external_signal",
                    states,
                    trace,
                    pending,
                    "external event is not declared by entry.origin",
                    index,
                )
            pending.append(selected)
        try:
            pending_index = pending.index(selected)
        except ValueError:
            trace.append(DerivationTraceStep(index, selected, before, before, "signal_not_pending", ()))
            return _failed(
                "signal_not_pending",
                states,
                trace,
                pending,
                "internal event is not present in the pending queue",
                index,
            )

        model_object = objects[selected.target]
        state = next((item for item in model_object.states if item.name == before), None)
        handler = None if state is None else next(
            (item for item in state.transitions if item.signal == selected.signal), None
        )
        if handler is None:
            trace.append(DerivationTraceStep(index, selected, before, before, "unhandled_signal", ()))
            return _failed(
                "unhandled_signal",
                states,
                trace,
                pending,
                "target object has no matching transition handler in its current state",
                index,
            )

        generated = tuple(
            _event(signal)
            for block in handler.blocks
            if block.kind in {"drives", "emits"}
            for signal in block.signals
        )
        states[selected.target] = handler.target_state
        pending.pop(pending_index)
        pending.extend(generated)
        trace.append(
            DerivationTraceStep(
                index, selected, before, handler.target_state, "handled", generated
            )
        )

    if pending:
        return _failed(
            "sequence_incomplete",
            states,
            trace,
            pending,
            "sequence ended with pending signals",
            None,
        )
    return DerivationResult(
        RESULT_SCHEMA_VERSION,
        "passed",
        tuple(trace),
        _final_state(states),
        (),
        None,
    )
