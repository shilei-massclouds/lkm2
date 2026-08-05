"""Data model for derivation sequences and deterministic results."""

from __future__ import annotations

from dataclasses import dataclass
import re


SEQUENCE_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MODES = frozenset({"drive", "emit"})
_STATUSES = frozenset(
    {
        "passed",
        "unhandled_signal",
        "sequence_incomplete",
        "unsupported_feature",
        "signal_not_pending",
        "undeclared_external_signal",
    }
)


class DerivationValidationError(ValueError):
    """Raised when a derivation document or in-memory value is invalid."""


def _name(value: object, path: str) -> None:
    if type(value) is not tuple or not value:
        raise DerivationValidationError(f"{path} must be a non-empty tuple of identifiers")
    for index, part in enumerate(value):
        if type(part) is not str or _IDENTIFIER.fullmatch(part) is None:
            raise DerivationValidationError(f"{path}[{index}] is not a valid identifier: {part!r}")


def _tuple_of(value: object, item_type: type, path: str) -> None:
    if type(value) is not tuple:
        raise DerivationValidationError(f"{path} must be a tuple")
    for index, item in enumerate(value):
        if not isinstance(item, item_type):
            raise DerivationValidationError(f"{path}[{index}] must be a {item_type.__name__}")


@dataclass(frozen=True, slots=True)
class DerivationEvent:
    source: tuple[str, ...]
    target: tuple[str, ...]
    signal: tuple[str, ...]
    mode: str

    def __post_init__(self) -> None:
        _name(self.source, "event.source")
        _name(self.target, "event.target")
        _name(self.signal, "event.signal")
        if len(self.source) < 2 or len(self.target) < 2:
            raise DerivationValidationError(
                "event source and target must be absolute declaration names"
            )
        if len(self.signal) != 2 or self.signal[0] != "Transition":
            raise DerivationValidationError("event.signal must have the form Transition::<Name>")
        if self.mode not in _MODES:
            raise DerivationValidationError(f"event.mode must be 'drive' or 'emit', got {self.mode!r}")


@dataclass(frozen=True, slots=True)
class DerivationSequence:
    schema_version: int
    events: tuple[DerivationEvent, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise DerivationValidationError("schema_version must be an integer")
        if self.schema_version != SEQUENCE_SCHEMA_VERSION:
            raise DerivationValidationError(
                f"unsupported schema_version {self.schema_version!r}; expected {SEQUENCE_SCHEMA_VERSION}"
            )
        _tuple_of(self.events, DerivationEvent, "events")


@dataclass(frozen=True, slots=True)
class DerivationTraceStep:
    index: int
    event: DerivationEvent
    state_before: tuple[str, ...] | None
    state_after: tuple[str, ...] | None
    status: str
    generated: tuple[DerivationEvent, ...]

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise DerivationValidationError("trace.index must be a non-negative integer")
        if not isinstance(self.event, DerivationEvent):
            raise DerivationValidationError("trace.event must be a DerivationEvent")
        for path, state in (("trace.state_before", self.state_before), ("trace.state_after", self.state_after)):
            if state is not None:
                _name(state, path)
                if len(state) != 2 or state[0] != "State":
                    raise DerivationValidationError(f"{path} must have the form State::<Name> or null")
        if self.status not in {"handled", "unhandled_signal", "signal_not_pending", "undeclared_external_signal"}:
            raise DerivationValidationError(f"invalid trace status {self.status!r}")
        _tuple_of(self.generated, DerivationEvent, "trace.generated")


@dataclass(frozen=True, slots=True)
class DerivationState:
    object: tuple[str, ...]
    state: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _name(self.object, "final_state.object")
        if len(self.object) < 2:
            raise DerivationValidationError(
                "final_state.object must be an absolute declaration name"
            )
        if self.state is not None:
            _name(self.state, "final_state.state")
            if len(self.state) != 2 or self.state[0] != "State":
                raise DerivationValidationError("final_state.state must have the form State::<Name> or null")


@dataclass(frozen=True, slots=True)
class DerivationFailure:
    code: str
    event_index: int | None
    message: str
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in _STATUSES - {"passed"}:
            raise DerivationValidationError(f"invalid failure code {self.code!r}")
        if self.event_index is not None and (type(self.event_index) is not int or self.event_index < 0):
            raise DerivationValidationError("failure.event_index must be a non-negative integer or null")
        if type(self.message) is not str:
            raise DerivationValidationError("failure.message must be a string")
        if type(self.features) is not tuple or any(type(item) is not str for item in self.features):
            raise DerivationValidationError("failure.features must be a tuple of strings")


@dataclass(frozen=True, slots=True)
class DerivationResult:
    schema_version: int
    status: str
    trace: tuple[DerivationTraceStep, ...]
    final_state: tuple[DerivationState, ...]
    pending_signals: tuple[DerivationEvent, ...]
    failure: DerivationFailure | None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != RESULT_SCHEMA_VERSION:
            raise DerivationValidationError(
                f"result schema_version must be {RESULT_SCHEMA_VERSION}"
            )
        if self.status not in _STATUSES:
            raise DerivationValidationError(f"invalid result status {self.status!r}")
        _tuple_of(self.trace, DerivationTraceStep, "trace")
        _tuple_of(self.final_state, DerivationState, "final_state")
        _tuple_of(self.pending_signals, DerivationEvent, "pending_signals")
        names = [item.object for item in self.final_state]
        if len(set(names)) != len(names):
            raise DerivationValidationError("final_state contains a duplicate object")
        object.__setattr__(self, "final_state", tuple(sorted(self.final_state, key=lambda item: item.object)))
        if self.status == "passed" and self.failure is not None:
            raise DerivationValidationError("passed result must not contain a failure")
        if self.status != "passed":
            if not isinstance(self.failure, DerivationFailure):
                raise DerivationValidationError("failed result requires a failure")
            if self.failure.code != self.status:
                raise DerivationValidationError("failure.code must match result.status")
