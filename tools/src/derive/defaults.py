"""Deterministic defaults derived from a Model IR document."""

from __future__ import annotations

from model_ir import ModelExpression, ModelIR

from .model import SEQUENCE_SCHEMA_VERSION, DerivationEvent, DerivationSequence


def default_derivation_sequence(model: ModelIR) -> DerivationSequence:
    """Select every signal declared by entry.origin in declaration order."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    origin = next(
        external for external in model.externals if external.name == model.entry.origin
    )

    def target(expression: ModelExpression) -> tuple[str, ...]:
        parts: list[str] = []
        cursor = expression
        while cursor.kind == "path":
            parts.append(str(cursor.value))
            cursor = cursor.children[0]
        if cursor.kind != "identifier":
            raise RuntimeError("compiled external target is not static")
        parts.append(str(cursor.value))
        raw = tuple(reversed(parts))
        matches = tuple(item.name for item in model.objects if item.name[-len(raw):] == raw)
        if len(matches) != 1:
            raise RuntimeError("compiled external target is unresolved")
        return matches[0]
    return DerivationSequence(
        SEQUENCE_SCHEMA_VERSION,
        tuple(
            DerivationEvent(
                signal.source,
                target(signal.target),
                signal.signal,
                signal.mode,
                signal.arguments,
            )
            for signal in origin.signals
        ),
    )
