"""Data model for derivation root selection and schema-v4 results."""

from __future__ import annotations

from dataclasses import dataclass
import re

from model_ir import canonicalize_signal_name


SEQUENCE_SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 4
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MODES = frozenset({"drive", "emit", "yield"})
_UNIT_KINDS = frozenset({"root", "drive", "emit", "yield"})
_CHECK_STATUSES = frozenset({"passed", "failed", "established", "unsupported"})
_FAILURE_CODES = frozenset(
    {
        "unhandled_signal",
        "depends_on_failed",
        "ensures_failed",
        "invariant_failed",
        "unsupported_feature",
        "undeclared_external_signal",
        "no_resumable_continuation",
        "invalid_continuation_action",
        "continuation_reentry",
        "panic",
    }
)
_UNIT_STATUSES = frozenset({"passed", "yielded", "stopped", *_FAILURE_CODES})


class DerivationValidationError(ValueError):
    """Raised when a derivation document or in-memory value is invalid."""


def _name(value: object, path: str) -> None:
    if type(value) is not tuple or not value:
        raise DerivationValidationError(f"{path} must be a non-empty tuple of identifiers")
    for index, part in enumerate(value):
        if type(part) is not str or _IDENTIFIER.fullmatch(part) is None:
            raise DerivationValidationError(
                f"{path}[{index}] is not a valid identifier: {part!r}"
            )


def _tuple_of(value: object, item_type: type, path: str) -> None:
    if type(value) is not tuple:
        raise DerivationValidationError(f"{path} must be a tuple")
    for index, item in enumerate(value):
        if not isinstance(item, item_type):
            raise DerivationValidationError(
                f"{path}[{index}] must be a {item_type.__name__}"
            )


def _state(value: tuple[str, ...] | None, path: str) -> None:
    if value is None:
        return
    _name(value, path)
    if len(value) != 2 or value[0] != "State":
        raise DerivationValidationError(
            f"{path} must have the form State::<Name> or null"
        )


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
        if len(self.signal) != 2 or self.signal[0] not in {"Transition", "Action"}:
            raise DerivationValidationError(
                "event.signal must have the form Transition::<Name> or Action::<Name>"
            )
        object.__setattr__(self, "signal", canonicalize_signal_name(self.signal))
        if self.mode not in _MODES:
            raise DerivationValidationError(
                f"event.mode must be 'drive', 'emit', or 'yield', got {self.mode!r}"
            )


@dataclass(frozen=True, slots=True)
class DerivationSequence:
    schema_version: int
    events: tuple[DerivationEvent, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise DerivationValidationError("schema_version must be an integer")
        if self.schema_version != SEQUENCE_SCHEMA_VERSION:
            raise DerivationValidationError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {SEQUENCE_SCHEMA_VERSION}"
            )
        _tuple_of(self.events, DerivationEvent, "events")
        if any(event.mode == "yield" for event in self.events):
            raise DerivationValidationError(
                "sequence events cannot use the internal yield mode"
            )


@dataclass(frozen=True, slots=True)
class DerivationFact:
    predicate: tuple[str, ...]
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.predicate, "fact.predicate")
        if type(self.arguments) is not tuple or any(
            type(item) is not str for item in self.arguments
        ):
            raise DerivationValidationError("fact.arguments must be a tuple of strings")


@dataclass(frozen=True, slots=True)
class DerivationCheck:
    expression: str
    status: str

    def __post_init__(self) -> None:
        if type(self.expression) is not str or not self.expression:
            raise DerivationValidationError("check.expression must be a non-empty string")
        if self.status not in _CHECK_STATUSES:
            raise DerivationValidationError(f"invalid check status {self.status!r}")


@dataclass(frozen=True, slots=True)
class DerivationFailure:
    code: str
    path: str
    message: str
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in _FAILURE_CODES:
            raise DerivationValidationError(f"invalid failure code {self.code!r}")
        if type(self.path) is not str or not self.path:
            raise DerivationValidationError("failure.path must be a non-empty string")
        if type(self.message) is not str:
            raise DerivationValidationError("failure.message must be a string")
        if type(self.features) is not tuple or any(
            type(item) is not str for item in self.features
        ):
            raise DerivationValidationError("failure.features must be a tuple of strings")


@dataclass(frozen=True, slots=True)
class DerivationYieldToken:
    object: tuple[str, ...]
    generation: int

    def __post_init__(self) -> None:
        _name(self.object, "yield_token.object")
        if len(self.object) < 2:
            raise DerivationValidationError(
                "yield_token.object must be an absolute declaration name"
            )
        if type(self.generation) is not int or self.generation < 1:
            raise DerivationValidationError(
                "yield_token.generation must be a positive integer"
            )


@dataclass(frozen=True, slots=True)
class DerivationFrame:
    object: tuple[str, ...]
    handler: tuple[str, ...]
    control_index: int
    generation: int

    def __post_init__(self) -> None:
        _name(self.object, "frame.object")
        _name(self.handler, "frame.handler")
        if len(self.handler) != 2 or self.handler[0] != "Action":
            raise DerivationValidationError(
                "frame.handler must have the form Action::<Name>"
            )
        if type(self.control_index) is not int or self.control_index < 0:
            raise DerivationValidationError(
                "frame.control_index must be a non-negative integer"
            )
        if type(self.generation) is not int or self.generation < 0:
            raise DerivationValidationError(
                "frame.generation must be a non-negative integer"
            )


@dataclass(frozen=True, slots=True)
class DerivationContinuation:
    object: tuple[str, ...]
    generation: int
    frames: tuple[DerivationFrame, ...]
    yield_token: DerivationYieldToken | None = None

    def __post_init__(self) -> None:
        _name(self.object, "continuation.object")
        if type(self.generation) is not int or self.generation < 0:
            raise DerivationValidationError(
                "continuation.generation must be a non-negative integer"
            )
        _tuple_of(self.frames, DerivationFrame, "continuation.frames")
        if not self.frames:
            raise DerivationValidationError("continuation.frames must not be empty")
        if any(frame.object != self.object for frame in self.frames):
            raise DerivationValidationError(
                "continuation frames must belong to the continuation object"
            )
        if any(frame.generation != self.generation for frame in self.frames):
            raise DerivationValidationError(
                "continuation frame generation must match its snapshot"
            )
        if self.yield_token is not None:
            if not isinstance(self.yield_token, DerivationYieldToken):
                raise DerivationValidationError(
                    "continuation.yield_token must be a DerivationYieldToken or null"
                )
            if (
                self.yield_token.object != self.object
                or self.yield_token.generation != self.generation
            ):
                raise DerivationValidationError(
                    "continuation yield token must match its object and generation"
                )


@dataclass(frozen=True, slots=True)
class DerivationDirective:
    kind: str
    message: str

    def __post_init__(self) -> None:
        if self.kind not in {"print", "panic"}:
            raise DerivationValidationError(
                f"invalid derivation directive kind {self.kind!r}"
            )
        if type(self.message) is not str:
            raise DerivationValidationError("directive.message must be a string")


@dataclass(frozen=True, slots=True)
class DerivationUnit:
    kind: str
    event: DerivationEvent
    state_before: tuple[str, ...] | None
    handler: tuple[str, ...] | None
    candidate_state: tuple[str, ...] | None
    depends_on: tuple[DerivationCheck, ...]
    drives: tuple[DerivationUnit, ...]
    ensures: tuple[DerivationCheck, ...]
    establishes: tuple[DerivationCheck, ...]
    invariants: tuple[DerivationCheck, ...]
    state_after: tuple[str, ...] | None
    emits: tuple[DerivationUnit, ...]
    status: str
    failure: DerivationFailure | None
    yields: tuple[DerivationUnit, ...] = ()
    yield_token_created: DerivationYieldToken | None = None
    yield_token_consumed: DerivationYieldToken | None = None
    directives: tuple[DerivationDirective, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _UNIT_KINDS:
            raise DerivationValidationError(f"invalid unit kind {self.kind!r}")
        if not isinstance(self.event, DerivationEvent):
            raise DerivationValidationError("unit.event must be a DerivationEvent")
        if self.kind != "root" and self.kind != self.event.mode:
            raise DerivationValidationError(
                "drive/emit unit kind must match unit.event.mode"
            )
        _state(self.state_before, "unit.state_before")
        if self.handler is not None:
            _name(self.handler, "unit.handler")
            if len(self.handler) != 2 or self.handler[0] not in {"Transition", "Action"}:
                raise DerivationValidationError(
                    "unit.handler must have the form Transition::<Name>, Action::<Name>, or null"
                )
            canonical = canonicalize_signal_name(self.handler)
            if canonical != self.handler:
                raise DerivationValidationError(
                    f"unit.handler must use canonical signal {'::'.join(canonical)}"
                )
        _state(self.candidate_state, "unit.candidate_state")
        _tuple_of(self.depends_on, DerivationCheck, "unit.depends_on")
        _tuple_of(self.drives, DerivationUnit, "unit.drives")
        _tuple_of(self.ensures, DerivationCheck, "unit.ensures")
        _tuple_of(self.establishes, DerivationCheck, "unit.establishes")
        _tuple_of(self.invariants, DerivationCheck, "unit.invariants")
        for label, checks, allowed in (
            ("depends_on", self.depends_on, {"passed", "failed", "unsupported"}),
            ("ensures", self.ensures, {"passed", "failed", "unsupported"}),
            ("establishes", self.establishes, {"established", "unsupported"}),
            ("invariants", self.invariants, {"passed", "failed", "unsupported"}),
        ):
            if any(check.status not in allowed for check in checks):
                raise DerivationValidationError(
                    f"unit.{label} contains a check with an invalid status"
                )
        _state(self.state_after, "unit.state_after")
        _tuple_of(self.emits, DerivationUnit, "unit.emits")
        _tuple_of(self.yields, DerivationUnit, "unit.yields")
        _tuple_of(self.directives, DerivationDirective, "unit.directives")
        for label, token in (
            ("yield_token_created", self.yield_token_created),
            ("yield_token_consumed", self.yield_token_consumed),
        ):
            if token is not None and not isinstance(token, DerivationYieldToken):
                raise DerivationValidationError(
                    f"unit.{label} must be a DerivationYieldToken or null"
                )
        if self.handler is not None and self.handler != self.event.signal:
            raise DerivationValidationError("unit.handler must match unit.event.signal")
        if self.handler is None and self.candidate_state is not None:
            raise DerivationValidationError(
                "unit without a handler must not contain a candidate state"
            )
        if self.handler is not None:
            is_transition = self.handler[0] == "Transition"
            if is_transition != (self.candidate_state is not None):
                raise DerivationValidationError(
                    "transition unit requires a candidate state and action unit forbids one"
                )
        if self.status not in _UNIT_STATUSES:
            raise DerivationValidationError(f"invalid unit status {self.status!r}")
        panic_directives = tuple(
            directive for directive in self.directives if directive.kind == "panic"
        )
        if self.status == "panic":
            if (
                len(panic_directives) != 1
                or self.directives[-1] != panic_directives[0]
                or self.failure is None
                or self.failure.message != panic_directives[0].message
            ):
                raise DerivationValidationError(
                    "panic unit must end with the matching panic directive"
                )
        elif panic_directives:
            raise DerivationValidationError(
                "only a panic unit may contain a panic directive"
            )
        if self.status == "passed":
            if self.failure is not None:
                raise DerivationValidationError("passed unit must not contain a failure")
            if self.handler is None:
                raise DerivationValidationError(
                    "passed unit must contain a handler"
                )
            if self.handler[0] == "Transition" and self.state_after != self.candidate_state:
                raise DerivationValidationError(
                    "passed transition unit must complete its candidate state"
                )
            if self.handler[0] == "Action" and self.state_after != self.state_before:
                raise DerivationValidationError(
                    "passed action unit must preserve its entering state"
                )
            if any(
                check.status not in {"passed", "established"}
                for checks in (
                    self.depends_on,
                    self.ensures,
                    self.establishes,
                    self.invariants,
                )
                for check in checks
            ):
                raise DerivationValidationError(
                    "passed unit must not contain a failed or unsupported check"
                )
            if self.yield_token_created is not None:
                raise DerivationValidationError(
                    "passed unit must not create a yield token"
                )
        elif self.status == "yielded":
            if self.failure is not None or self.state_after is not None:
                raise DerivationValidationError(
                    "yielded unit must be suspended without a direct failure"
                )
            if self.handler is None or self.handler[0] != "Action":
                raise DerivationValidationError(
                    "yielded unit requires an Action handler"
                )
            if self.yield_token_created is None or not self.yields:
                raise DerivationValidationError(
                    "yielded unit requires a created token and yielded target"
                )
        elif self.status == "stopped":
            if self.failure is not None:
                raise DerivationValidationError("stopped unit must not duplicate child failure")
            if self.state_after is not None:
                raise DerivationValidationError("stopped unit must not contain a completed state")
            if self.handler is None or not self.drives or self.emits:
                raise DerivationValidationError(
                    "stopped unit requires a handler and a failed drive chain"
                )
        else:
            if not isinstance(self.failure, DerivationFailure):
                raise DerivationValidationError("failed unit requires a failure")
            if self.failure.code != self.status:
                raise DerivationValidationError(
                    "unit failure.code must match unit.status"
                )
            if self.state_after is not None:
                raise DerivationValidationError("failed unit must not contain a completed state")
            if self.emits:
                raise DerivationValidationError("failed unit must not contain emitted units")


def _unit_failures(units: tuple[DerivationUnit, ...]) -> tuple[DerivationFailure, ...]:
    failures: list[DerivationFailure] = []
    for unit in units:
        if unit.failure is not None:
            failures.append(unit.failure)
        failures.extend(_unit_failures(unit.drives))
        failures.extend(_unit_failures(unit.emits))
        failures.extend(_unit_failures(unit.yields))
    return tuple(failures)


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
        _state(self.state, "final_state.state")


@dataclass(frozen=True, slots=True)
class DerivationResult:
    schema_version: int
    status: str
    units: tuple[DerivationUnit, ...]
    final_state: tuple[DerivationState, ...]
    facts: tuple[DerivationFact, ...]
    failure: DerivationFailure | None = None
    continuations: tuple[DerivationContinuation, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != RESULT_SCHEMA_VERSION:
            raise DerivationValidationError(
                f"result schema_version must be {RESULT_SCHEMA_VERSION}"
            )
        if self.status not in {"passed", "yielded", *_FAILURE_CODES}:
            raise DerivationValidationError(f"invalid result status {self.status!r}")
        _tuple_of(self.units, DerivationUnit, "units")
        _tuple_of(self.final_state, DerivationState, "final_state")
        _tuple_of(self.facts, DerivationFact, "facts")
        _tuple_of(self.continuations, DerivationContinuation, "continuations")
        names = [item.object for item in self.final_state]
        if len(set(names)) != len(names):
            raise DerivationValidationError("final_state contains a duplicate object")
        if len(set(self.facts)) != len(self.facts):
            raise DerivationValidationError("facts contains a duplicate fact")
        object.__setattr__(
            self, "final_state", tuple(sorted(self.final_state, key=lambda item: item.object))
        )
        object.__setattr__(
            self, "facts", tuple(sorted(self.facts, key=lambda item: (item.predicate, item.arguments)))
        )
        continuation_names = [item.object for item in self.continuations]
        if len(set(continuation_names)) != len(continuation_names):
            raise DerivationValidationError(
                "continuations contains a duplicate object"
            )
        object.__setattr__(
            self,
            "continuations",
            tuple(sorted(self.continuations, key=lambda item: item.object)),
        )
        if self.status in {"passed", "yielded"} and self.failure is not None:
            raise DerivationValidationError(
                "passed/yielded result must not contain a failure"
            )
        unit_failures = _unit_failures(self.units)
        if self.status in {"passed", "yielded"} and unit_failures:
            raise DerivationValidationError(
                "passed/yielded result contains a failed unit"
            )
        if self.status == "yielded" and not any(
            item.yield_token is not None for item in self.continuations
        ):
            raise DerivationValidationError(
                "yielded result requires an outstanding continuation yield token"
            )
        if self.status not in {"passed", "yielded"}:
            if not isinstance(self.failure, DerivationFailure):
                raise DerivationValidationError("failed result requires a failure")
            if self.failure.code != self.status:
                raise DerivationValidationError(
                    "failure.code must match result.status"
                )
            if self.failure.path != "model" and self.failure not in unit_failures:
                raise DerivationValidationError(
                    "result failure is not recorded at a nested unit failure point"
                )
        if self.status == "panic" and self.continuations:
            raise DerivationValidationError(
                "panic result must not retain continuation frames or tokens"
            )
