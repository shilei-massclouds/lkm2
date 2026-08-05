"""Deterministic defaults derived from a Model IR document."""

from __future__ import annotations

from model_ir import ModelIR

from .model import SEQUENCE_SCHEMA_VERSION, DerivationEvent, DerivationSequence


def default_derivation_sequence(model: ModelIR) -> DerivationSequence:
    """Select every signal declared by entry.origin in declaration order."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    origin = next(
        external for external in model.externals if external.name == model.entry.origin
    )
    return DerivationSequence(
        SEQUENCE_SCHEMA_VERSION,
        tuple(
            DerivationEvent(signal.source, signal.target, signal.signal, signal.mode)
            for signal in origin.signals
        ),
    )
