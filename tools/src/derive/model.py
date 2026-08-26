"""Data model for derivation root selection and schema-v12 path results."""

from __future__ import annotations

from dataclasses import dataclass
import re

from model_ir import ModelExpression, canonicalize_signal_name


SEQUENCE_SCHEMA_VERSION = 3
RESULT_SCHEMA_VERSION = 12
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MODES = frozenset({"drive", "emit", "yield", "resume"})
_UNIT_KINDS = frozenset({"root", "drive", "emit", "yield", "resume"})
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
        "duplicate_collection_item",
        "invalid_current_task_ref",
        "invalid_current_cpu_ref",
        "invalid_syscall_cpu_target",
        "invalid_exception_cpu_target",
        "unknown_cpu_target",
        "unknown_interrupt_mode",
        "nested_event_flow",
        "unsupported_runtime_signal",
        "unimplemented_task_exit",
        "invalid_derivation_line",
        "duplicate_runq_task",
        "idle_task_not_queueable",
        "task_not_queued",
        "relation_key_missing",
        "relation_key_ambiguous",
        "map_key_missing",
        "map_key_conflict",
    }
)
_UNIT_STATUSES = frozenset(
    {"passed", "yielded", "stopped", "cycle_closed", *_FAILURE_CODES}
)


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
    arguments: tuple[ModelExpression, ...] = ()

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
                "event.mode must be 'drive', 'emit', 'yield', or 'resume', "
                f"got {self.mode!r}"
            )
        _tuple_of(self.arguments, ModelExpression, "event.arguments")


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
class DerivationTerm:
    """A typed String or object-reference runtime term."""

    kind: str
    type: tuple[str, ...]
    value: str | tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in {"string", "object"}:
            raise DerivationValidationError("term.kind must be 'string' or 'object'")
        _name(self.type, "term.type")
        if self.kind == "string":
            if self.type != ("String",) or type(self.value) is not str:
                raise DerivationValidationError(
                    "string term requires type String and a string value"
                )
        else:
            if type(self.value) is not tuple:
                raise DerivationValidationError("object term value must be a qualified name")
            _name(self.value, "term.value")


@dataclass(frozen=True, slots=True)
class DerivationBindingResult:
    name: str
    type: tuple[str, ...]
    expression: ModelExpression
    owner: tuple[str, ...]
    key: DerivationTerm
    value: DerivationTerm | None
    status: str
    failure_code: str | None = None
    candidates: tuple[DerivationTerm, ...] = ()

    def __post_init__(self) -> None:
        if type(self.name) is not str or _IDENTIFIER.fullmatch(self.name) is None:
            raise DerivationValidationError("binding_result.name must be an identifier")
        _name(self.type, "binding_result.type")
        if not isinstance(self.expression, ModelExpression):
            raise DerivationValidationError(
                "binding_result.expression must be a ModelExpression"
            )
        _name(self.owner, "binding_result.owner")
        if not isinstance(self.key, DerivationTerm):
            raise DerivationValidationError("binding_result.key must be a DerivationTerm")
        if self.value is not None and not isinstance(self.value, DerivationTerm):
            raise DerivationValidationError(
                "binding_result.value must be a DerivationTerm or null"
            )
        if self.status not in {"passed", "failed"}:
            raise DerivationValidationError(
                "binding_result.status must be 'passed' or 'failed'"
            )
        allowed_failures = {
            "relation_key_missing",
            "relation_key_ambiguous",
            "map_key_missing",
        }
        if (self.status == "passed") != (self.failure_code is None):
            raise DerivationValidationError(
                "binding_result failure_code must be null exactly on success"
            )
        if self.failure_code is not None and self.failure_code not in allowed_failures:
            raise DerivationValidationError("invalid binding_result.failure_code")
        _tuple_of(self.candidates, DerivationTerm, "binding_result.candidates")


@dataclass(frozen=True, slots=True)
class DerivationRelationEffect:
    owner: tuple[str, ...]
    container: str
    key: DerivationTerm
    value: DerivationTerm
    status: str
    conflict_values: tuple[DerivationTerm, ...] = ()

    def __post_init__(self) -> None:
        _name(self.owner, "relation_effect.owner")
        if self.container not in {"Relation", "Map"}:
            raise DerivationValidationError(
                "relation_effect.container must be Relation or Map"
            )
        if not isinstance(self.key, DerivationTerm) or not isinstance(
            self.value, DerivationTerm
        ):
            raise DerivationValidationError(
                "relation_effect key/value must be DerivationTerm values"
            )
        if self.status not in {"established", "failed"}:
            raise DerivationValidationError(
                "relation_effect.status must be established or failed"
            )
        _tuple_of(
            self.conflict_values,
            DerivationTerm,
            "relation_effect.conflict_values",
        )


@dataclass(frozen=True, slots=True)
class DerivationTuple:
    owner: tuple[str, ...]
    container: str
    key: DerivationTerm
    value: DerivationTerm

    def __post_init__(self) -> None:
        _name(self.owner, "tuple.owner")
        if self.container not in {"Relation", "Map"}:
            raise DerivationValidationError("tuple.container must be Relation or Map")
        if not isinstance(self.key, DerivationTerm) or not isinstance(
            self.value, DerivationTerm
        ):
            raise DerivationValidationError("tuple key/value must be DerivationTerm values")


@dataclass(frozen=True, slots=True)
class DerivationFrame:
    object: tuple[str, ...]
    handler: tuple[str, ...]
    control_index: int
    bindings: tuple[DerivationBinding, ...] = ()

    def __post_init__(self) -> None:
        _name(self.object, "frame.object")
        if len(self.object) < 2:
            raise DerivationValidationError(
                "frame.object must be an absolute declaration name"
            )
        _name(self.handler, "frame.handler")
        if len(self.handler) != 2 or self.handler[0] != "Action":
            raise DerivationValidationError(
                "frame.handler must have the form Action::<Name>"
            )
        if type(self.control_index) is not int or self.control_index < 0:
            raise DerivationValidationError(
                "frame.control_index must be a non-negative integer"
            )
        _tuple_of(self.bindings, DerivationBinding, "frame.bindings")


@dataclass(frozen=True, slots=True)
class DerivationBinding:
    name: str
    term: DerivationTerm

    def __post_init__(self) -> None:
        if type(self.name) is not str or _IDENTIFIER.fullmatch(self.name) is None:
            raise DerivationValidationError("binding.name must be an identifier")
        if not isinstance(self.term, DerivationTerm):
            raise DerivationValidationError("binding.term must be a DerivationTerm")


@dataclass(frozen=True, slots=True)
class DerivationContinuation:
    root: tuple[str, ...]
    frames: tuple[DerivationFrame, ...]

    def __post_init__(self) -> None:
        _name(self.root, "continuation.root")
        if len(self.root) < 2:
            raise DerivationValidationError(
                "continuation.root must be an absolute declaration name"
            )
        _tuple_of(self.frames, DerivationFrame, "continuation.frames")
        if not self.frames:
            raise DerivationValidationError("continuation.frames must not be empty")
        if self.frames[0].object != self.root:
            raise DerivationValidationError(
                "continuation first frame must belong to its root"
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
class DerivationSwitch:
    binding: str
    task: tuple[str, ...]
    idle_fallback: bool = False
    cycle_closed: bool = False
    after_drives: int = 0

    def __post_init__(self) -> None:
        if type(self.binding) is not str or _IDENTIFIER.fullmatch(self.binding) is None:
            raise DerivationValidationError("switch.binding must be an identifier")
        _name(self.task, "switch.task")
        if type(self.idle_fallback) is not bool:
            raise DerivationValidationError(
                "switch.idle_fallback must be a boolean"
            )
        if type(self.cycle_closed) is not bool:
            raise DerivationValidationError(
                "switch.cycle_closed must be a boolean"
            )
        if type(self.after_drives) is not int or self.after_drives < 0:
            raise DerivationValidationError(
                "switch.after_drives must be a non-negative integer"
            )


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
    directives: tuple[DerivationDirective, ...] = ()
    resumes: tuple[DerivationUnit, ...] = ()
    switches: tuple[DerivationSwitch, ...] = ()
    bindings: tuple[DerivationBindingResult, ...] = ()
    relation_effects: tuple[DerivationRelationEffect, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _UNIT_KINDS:
            raise DerivationValidationError(f"invalid unit kind {self.kind!r}")
        if not isinstance(self.event, DerivationEvent):
            raise DerivationValidationError("unit.event must be a DerivationEvent")
        if self.kind != "root" and self.kind != self.event.mode:
            raise DerivationValidationError(
                "non-root unit kind must match unit.event.mode"
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
        _tuple_of(self.resumes, DerivationUnit, "unit.resumes")
        _tuple_of(self.switches, DerivationSwitch, "unit.switches")
        _tuple_of(self.bindings, DerivationBindingResult, "unit.bindings")
        _tuple_of(
            self.relation_effects,
            DerivationRelationEffect,
            "unit.relation_effects",
        )
        object.__setattr__(
            self,
            "relation_effects",
            tuple(
                sorted(
                    self.relation_effects,
                    key=lambda item: (
                        item.owner,
                        item.container,
                        item.key.kind,
                        item.key.type,
                        item.key.value,
                        item.value.kind,
                        item.value.type,
                        item.value.value,
                        item.status,
                    ),
                )
            ),
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
        if self.status in {"passed", "cycle_closed"}:
            if self.failure is not None:
                raise DerivationValidationError("successful unit must not contain a failure")
            if self.handler is None:
                raise DerivationValidationError(
                    "successful unit must contain a handler"
                )
            if self.handler[0] == "Transition" and self.state_after != self.candidate_state:
                raise DerivationValidationError(
                    "successful transition unit must complete its candidate state"
                )
            if self.handler[0] == "Action" and self.state_after != self.state_before:
                raise DerivationValidationError(
                    "successful action unit must preserve its entering state"
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
                    "successful unit must not contain a failed or unsupported check"
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
            if not self.yields and not any(
                _unit_contains_yield(child) for child in self.drives
            ):
                raise DerivationValidationError(
                    "yielded unit requires a yielded target in its continuation chain"
                )
        elif self.status == "stopped":
            if self.failure is not None:
                raise DerivationValidationError("stopped unit must not duplicate child failure")
            if self.state_after is not None:
                raise DerivationValidationError("stopped unit must not contain a completed state")
            if self.handler is None or not (self.drives or self.yields) or self.emits or self.resumes:
                raise DerivationValidationError(
                    "stopped unit requires a handler and a failed drive/yield chain"
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
            if self.emits or self.resumes:
                raise DerivationValidationError(
                    "failed unit must not contain emitted or resumed units"
                )


def _unit_failures(units: tuple[DerivationUnit, ...]) -> tuple[DerivationFailure, ...]:
    failures: list[DerivationFailure] = []
    for unit in units:
        if unit.failure is not None:
            failures.append(unit.failure)
        failures.extend(_unit_failures(unit.drives))
        failures.extend(_unit_failures(unit.emits))
        failures.extend(_unit_failures(unit.yields))
        failures.extend(_unit_failures(unit.resumes))
    return tuple(failures)


def _unit_contains_yield(unit: DerivationUnit) -> bool:
    return bool(unit.yields) or any(
        _unit_contains_yield(child) for child in unit.drives
    )


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
class DerivationValue:
    object: tuple[str, ...]
    field: str | None
    values: tuple[tuple[str, ...], ...]
    collection: bool = False

    def __post_init__(self) -> None:
        _name(self.object, "final_value.object")
        if self.field is not None and (
            type(self.field) is not str or _IDENTIFIER.fullmatch(self.field) is None
        ):
            raise DerivationValidationError(
                "final_value.field must be an identifier or null"
            )
        if type(self.values) is not tuple:
            raise DerivationValidationError("final_value.values must be a tuple")
        for index, value in enumerate(self.values):
            _name(value, f"final_value.values[{index}]")
        if type(self.collection) is not bool:
            raise DerivationValidationError("final_value.collection must be a boolean")
        if self.collection != (self.field is None):
            raise DerivationValidationError(
                "collection final values must have a null field and field values must not"
            )
        if not self.collection and len(self.values) != 1:
            raise DerivationValidationError(
                "ordinary field final values require exactly one object reference"
            )


@dataclass(frozen=True, slots=True)
class DerivationScheduler:
    scheduler: tuple[str, ...]
    idle_task: tuple[str, ...]
    runq: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        _name(self.scheduler, "scheduler.scheduler")
        _name(self.idle_task, "scheduler.idle_task")
        if type(self.runq) is not tuple:
            raise DerivationValidationError("scheduler.runq must be a tuple")
        for index, task in enumerate(self.runq):
            _name(task, f"scheduler.runq[{index}]")
        if len(set(self.runq)) != len(self.runq):
            raise DerivationValidationError("scheduler.runq contains a duplicate Task")
        if self.idle_task in self.runq:
            raise DerivationValidationError(
                "scheduler.runq must not contain the idle Task"
            )


@dataclass(frozen=True, slots=True)
class DerivationEventFlow:
    flow: tuple[str, ...]
    cpu: tuple[str, ...]
    suspended_task_flow: tuple[str, ...]
    user_runtime: tuple[str, ...]
    signal: str
    outcome: str

    def __post_init__(self) -> None:
        _name(self.flow, "event_flow.flow")
        _name(self.cpu, "event_flow.cpu")
        _name(self.suspended_task_flow, "event_flow.suspended_task_flow")
        _name(self.user_runtime, "event_flow.user_runtime")
        if type(self.signal) is not str or not self.signal:
            raise DerivationValidationError(
                "event_flow.signal must be a non-empty string"
            )
        if self.outcome not in {"returned", "terminal"}:
            raise DerivationValidationError(
                "event_flow.outcome must be 'returned' or 'terminal'"
            )


@dataclass(frozen=True, slots=True)
class DerivationInterruptControl:
    cpu: tuple[str, ...]
    mode: str
    pending: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.cpu, "interrupt_control.cpu")
        if self.mode not in {"Unknown", "Masked", "Unmasked"}:
            raise DerivationValidationError(
                "interrupt_control.mode must be 'Unknown', 'Masked', or 'Unmasked'"
            )
        if type(self.pending) is not tuple or any(
            type(item) is not str or not item for item in self.pending
        ):
            raise DerivationValidationError(
                "interrupt_control.pending must be a tuple of non-empty signal names"
            )


@dataclass(frozen=True, slots=True)
class DerivationPath:
    status: str
    units: tuple[DerivationUnit, ...]
    final_state: tuple[DerivationState, ...]
    facts: tuple[DerivationFact, ...]
    failure: DerivationFailure | None = None
    continuations: tuple[DerivationContinuation, ...] = ()
    final_values: tuple[DerivationValue, ...] = ()
    schedulers: tuple[DerivationScheduler, ...] = ()
    current_task_ref: tuple[str, ...] | None = None
    current_cpu_ref: tuple[str, ...] | None = None
    event_flows: tuple[DerivationEventFlow, ...] = ()
    interrupt_controls: tuple[DerivationInterruptControl, ...] = ()
    tuples: tuple[DerivationTuple, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"passed", "yielded", "cycle_closed", *_FAILURE_CODES}:
            raise DerivationValidationError(f"invalid path status {self.status!r}")
        _tuple_of(self.units, DerivationUnit, "units")
        _tuple_of(self.final_state, DerivationState, "final_state")
        _tuple_of(self.facts, DerivationFact, "facts")
        _tuple_of(self.continuations, DerivationContinuation, "continuations")
        _tuple_of(self.final_values, DerivationValue, "final_values")
        _tuple_of(self.schedulers, DerivationScheduler, "schedulers")
        _tuple_of(self.event_flows, DerivationEventFlow, "event_flows")
        _tuple_of(
            self.interrupt_controls,
            DerivationInterruptControl,
            "interrupt_controls",
        )
        _tuple_of(self.tuples, DerivationTuple, "tuples")
        if len(set(self.tuples)) != len(self.tuples):
            raise DerivationValidationError("tuples contains a duplicate tuple")
        object.__setattr__(
            self,
            "tuples",
            tuple(
                sorted(
                    self.tuples,
                    key=lambda item: (
                        item.owner,
                        item.container,
                        item.key.kind,
                        item.key.type,
                        item.key.value,
                        item.value.kind,
                        item.value.type,
                        item.value.value,
                    ),
                )
            ),
        )
        interrupt_cpus = [item.cpu for item in self.interrupt_controls]
        if len(set(interrupt_cpus)) != len(interrupt_cpus):
            raise DerivationValidationError(
                "interrupt_controls contains a duplicate CPU"
            )
        object.__setattr__(
            self,
            "interrupt_controls",
            tuple(sorted(self.interrupt_controls, key=lambda item: item.cpu)),
        )
        if self.current_task_ref is not None:
            _name(self.current_task_ref, "current_task_ref")
        if self.current_cpu_ref is not None:
            _name(self.current_cpu_ref, "current_cpu_ref")
        value_keys = [(item.object, item.field) for item in self.final_values]
        if len(set(value_keys)) != len(value_keys):
            raise DerivationValidationError("final_values contains a duplicate target")
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
        object.__setattr__(
            self,
            "final_values",
            tuple(
                sorted(
                    self.final_values,
                    key=lambda item: (item.object, "" if item.field is None else item.field),
                )
            ),
        )
        continuation_roots = [item.root for item in self.continuations]
        if len(set(continuation_roots)) != len(continuation_roots):
            raise DerivationValidationError(
                "continuations contains a duplicate root"
            )
        object.__setattr__(
            self,
            "continuations",
            tuple(sorted(self.continuations, key=lambda item: item.root)),
        )
        if self.status in {"passed", "yielded", "cycle_closed"} and self.failure is not None:
            raise DerivationValidationError(
                "passed/yielded result must not contain a failure"
            )
        unit_failures = _unit_failures(self.units)
        if self.status in {"passed", "yielded", "cycle_closed"} and unit_failures:
            raise DerivationValidationError(
                "passed/yielded result contains a failed unit"
            )
        if self.status == "yielded" and not self.continuations:
            raise DerivationValidationError(
                "yielded result requires an outstanding continuation breakpoint"
            )
        if self.status == "passed" and self.continuations:
            raise DerivationValidationError(
                "passed result must not retain continuation breakpoints"
            )
        if self.status not in {"passed", "yielded", "cycle_closed"}:
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
                "panic result must not retain continuation frames"
            )
        if self.status == "invalid_derivation_line":
            if self.current_task_ref is not None:
                raise DerivationValidationError(
                    "invalid_derivation_line requires a null current_task_ref"
                )
            if self.current_cpu_ref is not None:
                raise DerivationValidationError(
                    "invalid_derivation_line requires a null current_cpu_ref"
                )
            if self.schedulers:
                raise DerivationValidationError(
                    "invalid_derivation_line must not expose scheduler snapshots"
                )
            if self.interrupt_controls:
                raise DerivationValidationError(
                    "invalid_derivation_line must not expose interrupt control snapshots"
                )
        elif self.current_cpu_ref is None:
            raise DerivationValidationError(
                "a valid derivation line requires a concrete current_cpu_ref"
            )
        elif self.current_cpu_ref not in interrupt_cpus:
            raise DerivationValidationError(
                "a valid derivation line requires an interrupt control snapshot for CurrentCPU"
            )
        if self.status != "invalid_derivation_line" and len(self.schedulers) != 1:
            raise DerivationValidationError(
                "a valid derivation line requires exactly one scheduler"
            )


@dataclass(frozen=True, slots=True)
class DerivationResult:
    schema_version: int
    status: str
    paths: tuple[DerivationPath, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != RESULT_SCHEMA_VERSION:
            raise DerivationValidationError(
                f"result schema_version must be {RESULT_SCHEMA_VERSION}"
            )
        if self.status not in {"passed", "yielded", "failed"}:
            raise DerivationValidationError(f"invalid result status {self.status!r}")
        _tuple_of(self.paths, DerivationPath, "paths")
        if not self.paths:
            raise DerivationValidationError("result.paths must not be empty")
        expected = (
            "failed"
            if any(path.failure is not None for path in self.paths)
            else "yielded"
            if any(path.status == "yielded" for path in self.paths)
            else "passed"
        )
        if self.status != expected:
            raise DerivationValidationError(
                f"result.status must aggregate to {expected!r}"
            )

    @property
    def units(self) -> tuple[DerivationUnit, ...]:
        return self.paths[0].units

    @property
    def final_state(self) -> tuple[DerivationState, ...]:
        return self.paths[0].final_state

    @property
    def facts(self) -> tuple[DerivationFact, ...]:
        return self.paths[0].facts

    @property
    def failure(self) -> DerivationFailure | None:
        return self.paths[0].failure

    @property
    def continuations(self) -> tuple[DerivationContinuation, ...]:
        return self.paths[0].continuations

    @property
    def final_values(self) -> tuple[DerivationValue, ...]:
        return self.paths[0].final_values

    @property
    def schedulers(self) -> tuple[DerivationScheduler, ...]:
        return self.paths[0].schedulers

    @property
    def current_task_ref(self) -> tuple[str, ...] | None:
        return self.paths[0].current_task_ref

    @property
    def current_cpu_ref(self) -> tuple[str, ...] | None:
        return self.paths[0].current_cpu_ref

    @property
    def event_flows(self) -> tuple[DerivationEventFlow, ...]:
        return self.paths[0].event_flows

    @property
    def interrupt_controls(self) -> tuple[DerivationInterruptControl, ...]:
        return self.paths[0].interrupt_controls
