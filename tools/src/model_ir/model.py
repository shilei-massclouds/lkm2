"""Frozen data model and semantic validation for Model IR schema v13."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import ClassVar


SCHEMA_VERSION = 13
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_EXPRESSION_KINDS = frozenset(
    {"identifier", "integer", "string", "unary", "binary", "member", "path", "index", "call"}
)
_BLOCK_KINDS = frozenset(
    {
        "depends_on",
        "may_change",
        "drives",
        "yields",
        "ensures",
        "establishes",
        "emits",
        "resumes",
        "updates",
        "print",
        "panic",
        "deferred",
        "switches",
        "binds",
    }
)
_SIGNAL_MODES = frozenset({"drive", "emit", "yield", "resume"})
_UNARY_OPERATORS = frozenset({"!", "-"})
_BINARY_OPERATORS = frozenset(
    {"||", "&&", "==", "!=", ">=", "<=", ">", "<", "+", "-", "*", "/", "%"}
)


class ModelIRValidationError(ValueError):
    """Raised when an in-memory or serialized Model IR is invalid."""


def canonicalize_signal_name(value: tuple[str, ...]) -> tuple[str, ...]:
    """Return the canonical signal name for a compatibility-boundary input."""

    if value == ("Transition", "Startup"):
        return ("Transition", "Preset")
    return value


def _validate_identifier(value: object, path: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ModelIRValidationError(f"{path} is not a valid identifier: {value!r}")


def _validate_qualified_name(value: object, path: str) -> None:
    if type(value) is not tuple:
        raise ModelIRValidationError(f"{path} must be a tuple of identifiers")
    if not value:
        raise ModelIRValidationError(f"{path} must not be empty")
    for index, part in enumerate(value):
        _validate_identifier(part, f"{path}[{index}]")


def _validate_tuple(value: object, item_type: type, path: str) -> None:
    if type(value) is not tuple:
        raise ModelIRValidationError(f"{path} must be a tuple")
    for index, item in enumerate(value):
        if not isinstance(item, item_type):
            raise ModelIRValidationError(
                f"{path}[{index}] must be a {item_type.__name__}"
            )


def _validate_special_name(value: tuple[str, ...], prefix: str, path: str) -> None:
    _validate_qualified_name(value, path)
    if len(value) != 2 or value[0] != prefix:
        raise ModelIRValidationError(
            f"{path} must have the form {prefix}::<Name>"
        )


def _validate_signal_name(value: tuple[str, ...], path: str) -> None:
    _validate_qualified_name(value, path)
    if len(value) != 2 or value[0] not in {"Transition", "Action"}:
        raise ModelIRValidationError(
            f"{path} must have the form Transition::<Name> or Action::<Name>"
        )
    canonical = canonicalize_signal_name(value)
    if canonical != value:
        raise ModelIRValidationError(
            f"{path} must use canonical signal {'::'.join(canonical)}"
        )


def _expression_access(
    expression: ModelExpression,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    if expression.kind == "identifier":
        return (str(expression.value),), ()
    if expression.kind not in {"member", "path"}:
        return None
    base = _expression_access(expression.children[0])
    if base is None:
        return None
    names, operations = base
    return names + (str(expression.value),), operations + (expression.kind,)


@dataclass(frozen=True, slots=True)
class ModelEntry:
    origin: tuple[str, ...]
    spec: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_qualified_name(self.origin, "entry.origin")
        _validate_qualified_name(self.spec, "entry.spec")
        if len(self.spec) != 1:
            raise ModelIRValidationError(
                "entry.spec must contain exactly one primary root module identifier"
            )
        if len(self.origin) < 2:
            raise ModelIRValidationError("entry.origin must be an absolute declaration name")


@dataclass(frozen=True, slots=True)
class ModelExpression:
    """A small, lossless-enough expression tree shared by all declarative clauses."""

    kind: str
    value: str | int | None = None
    children: tuple[ModelExpression, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _EXPRESSION_KINDS:
            raise ModelIRValidationError(f"invalid expression kind {self.kind!r}")
        _validate_tuple(self.children, ModelExpression, "expression.children")
        if self.kind == "identifier":
            _validate_identifier(self.value, "expression.value")
            expected = 0
        elif self.kind == "integer":
            if type(self.value) is not int or self.value < 0:
                raise ModelIRValidationError(
                    "integer expression value must be a non-negative integer"
                )
            expected = 0
        elif self.kind == "string":
            if type(self.value) is not str:
                raise ModelIRValidationError("string expression value must be a string")
            expected = 0
        elif self.kind in {"unary", "member", "path"}:
            if type(self.value) is not str:
                raise ModelIRValidationError(
                    f"{self.kind} expression value must be a string"
                )
            if self.kind in {"member", "path"}:
                _validate_identifier(self.value, "expression.value")
            elif self.value not in _UNARY_OPERATORS:
                raise ModelIRValidationError(f"invalid unary operator {self.value!r}")
            expected = 1
        elif self.kind == "binary":
            if type(self.value) is not str:
                raise ModelIRValidationError("binary expression value must be a string")
            if self.value not in _BINARY_OPERATORS:
                raise ModelIRValidationError(f"invalid binary operator {self.value!r}")
            expected = 2
        elif self.kind == "index":
            if self.value is not None:
                raise ModelIRValidationError("index expression value must be null")
            expected = 2
        else:  # call
            if self.value is not None:
                raise ModelIRValidationError("call expression value must be null")
            if not self.children:
                raise ModelIRValidationError("call expression requires a callee")
            expected = None
        if expected is not None and len(self.children) != expected:
            raise ModelIRValidationError(
                f"{self.kind} expression requires {expected} children"
            )


@dataclass(frozen=True, slots=True)
class ModelTypeExpression:
    name: tuple[str, ...]
    arguments: tuple[ModelTypeExpression, ...] = ()

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "type_expression.name")
        _validate_tuple(
            self.arguments, ModelTypeExpression, "type_expression.arguments"
        )


@dataclass(frozen=True, slots=True)
class ModelField:
    name: str
    type: ModelTypeExpression
    mutable: bool = False
    default: ModelExpression | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "field.name")
        if not isinstance(self.type, ModelTypeExpression):
            raise ModelIRValidationError("field.type must be a ModelTypeExpression")
        if type(self.mutable) is not bool:
            raise ModelIRValidationError("field.mutable must be a boolean")
        if self.default is not None and not isinstance(self.default, ModelExpression):
            raise ModelIRValidationError("field.default must be a ModelExpression or null")


@dataclass(frozen=True, slots=True)
class ModelParameter:
    name: str
    type: ModelTypeExpression

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "parameter.name")
        if not isinstance(self.type, ModelTypeExpression):
            raise ModelIRValidationError("parameter.type must be a ModelTypeExpression")


@dataclass(frozen=True, slots=True)
class ModelPredicate:
    name: tuple[str, ...]
    generic_parameters: tuple[str, ...]
    parameters: tuple[ModelParameter, ...]
    return_type: ModelTypeExpression
    body: tuple[ModelExpression, ...] | None

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "predicate.name")
        if type(self.generic_parameters) is not tuple:
            raise ModelIRValidationError("predicate.generic_parameters must be a tuple")
        for index, name in enumerate(self.generic_parameters):
            _validate_identifier(name, f"predicate.generic_parameters[{index}]")
        if len(set(self.generic_parameters)) != len(self.generic_parameters):
            raise ModelIRValidationError(f"duplicate generic parameter in {'.'.join(self.name)!r}")
        _validate_tuple(self.parameters, ModelParameter, "predicate.parameters")
        parameter_names = [parameter.name for parameter in self.parameters]
        if len(set(parameter_names)) != len(parameter_names):
            raise ModelIRValidationError(f"duplicate parameter in {'.'.join(self.name)!r}")
        if not isinstance(self.return_type, ModelTypeExpression):
            raise ModelIRValidationError(
                "predicate.return_type must be a ModelTypeExpression"
            )
        if self.body is not None:
            _validate_tuple(self.body, ModelExpression, "predicate.body")


@dataclass(frozen=True, slots=True)
class ModelType:
    name: tuple[str, ...]
    fields: tuple[ModelField, ...] | None
    base_type: ModelTypeExpression | None = None
    continuation: bool = False
    initial_state: tuple[str, ...] | None = None
    states: tuple[ModelState, ...] = ()
    sched_core: bool = False
    user_runtime: bool = False
    cpu_core: bool = False
    syscall_exit_flow: bool = False
    event_flow: bool = False

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "type.name")
        if self.base_type is not None and not isinstance(
            self.base_type, ModelTypeExpression
        ):
            raise ModelIRValidationError(
                "type.base_type must be a ModelTypeExpression or null"
            )
        if type(self.continuation) is not bool:
            raise ModelIRValidationError("type.continuation must be a boolean")
        if type(self.sched_core) is not bool:
            raise ModelIRValidationError("type.sched_core must be a boolean")
        if type(self.user_runtime) is not bool:
            raise ModelIRValidationError("type.user_runtime must be a boolean")
        if type(self.cpu_core) is not bool:
            raise ModelIRValidationError("type.cpu_core must be a boolean")
        if type(self.syscall_exit_flow) is not bool:
            raise ModelIRValidationError("type.syscall_exit_flow must be a boolean")
        if type(self.event_flow) is not bool:
            raise ModelIRValidationError("type.event_flow must be a boolean")
        if self.fields is not None:
            _validate_tuple(self.fields, ModelField, "type.fields")
            names = [field.name for field in self.fields]
            if len(set(names)) != len(names):
                raise ModelIRValidationError(f"duplicate field in type {'.'.join(self.name)!r}")
        if self.initial_state is not None:
            _validate_special_name(self.initial_state, "State", "type.initial_state")
        _validate_tuple(self.states, ModelState, "type.states")
        state_names = tuple(state.name for state in self.states)
        if len(set(state_names)) != len(state_names):
            raise ModelIRValidationError(
                f"duplicate state in type {'.'.join(self.name)!r}"
            )
        object.__setattr__(self, "states", tuple(sorted(self.states, key=lambda item: item.name)))


@dataclass(frozen=True, slots=True)
class ModelSignal:
    source: tuple[str, ...]
    target: ModelExpression
    signal: tuple[str, ...]
    mode: str
    arguments: tuple[ModelExpression, ...] = ()

    def __post_init__(self) -> None:
        _validate_qualified_name(self.source, "signal.source")
        if type(self.target) is tuple:
            _validate_qualified_name(self.target, "signal.target")
            result = ModelExpression("identifier", self.target[0])
            for part in self.target[1:]:
                result = ModelExpression("path", part, (result,))
            object.__setattr__(self, "target", result)
        if not isinstance(self.target, ModelExpression):
            raise ModelIRValidationError("signal.target must be a ModelExpression")
        _validate_signal_name(self.signal, "signal.signal")
        if len(self.source) < 2:
            raise ModelIRValidationError(
                "signal source must be an absolute declaration name"
            )
        if self.mode not in _SIGNAL_MODES:
            raise ModelIRValidationError(
                "signal.mode must be 'drive', 'emit', 'yield', or 'resume', "
                f"got {self.mode!r}"
            )
        _validate_tuple(self.arguments, ModelExpression, "signal.arguments")


@dataclass(frozen=True, slots=True)
class ModelUpdate:
    target: ModelExpression
    value: ModelExpression

    def __post_init__(self) -> None:
        if not isinstance(self.target, ModelExpression):
            raise ModelIRValidationError("update.target must be a ModelExpression")
        if not isinstance(self.value, ModelExpression):
            raise ModelIRValidationError("update.value must be a ModelExpression")


@dataclass(frozen=True, slots=True)
class ModelBinding:
    """One ordered, read-only handler-local lookup binding."""

    name: str
    type: ModelTypeExpression
    expression: ModelExpression

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "binding.name")
        if not isinstance(self.type, ModelTypeExpression):
            raise ModelIRValidationError("binding.type must be a ModelTypeExpression")
        if not isinstance(self.expression, ModelExpression):
            raise ModelIRValidationError("binding.expression must be a ModelExpression")


@dataclass(frozen=True, slots=True)
class ModelDeferred:
    name: str
    number: str
    category: ModelExpression
    summary: str
    evidence: tuple[ModelExpression, ...]
    close_when: str

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "deferred.name")
        if type(self.number) is not str or re.fullmatch(r"[0-9]+", self.number) is None:
            raise ModelIRValidationError("deferred.number must be decimal text")
        if not isinstance(self.category, ModelExpression):
            raise ModelIRValidationError("deferred.category must be a ModelExpression")
        if type(self.summary) is not str:
            raise ModelIRValidationError("deferred.summary must be a string")
        _validate_tuple(self.evidence, ModelExpression, "deferred.evidence")
        if type(self.close_when) is not str:
            raise ModelIRValidationError("deferred.close_when must be a string")


@dataclass(frozen=True, slots=True)
class ModelHandlerBlock:
    kind: str
    expressions: tuple[ModelExpression, ...] = ()
    signals: tuple[ModelSignal, ...] = ()
    deferred: ModelDeferred | None = None
    updates: tuple[ModelUpdate, ...] = ()
    switches: str | None = None
    bindings: tuple[ModelBinding, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _BLOCK_KINDS:
            raise ModelIRValidationError(f"invalid handler block kind {self.kind!r}")
        _validate_tuple(self.expressions, ModelExpression, "handler_block.expressions")
        _validate_tuple(self.signals, ModelSignal, "handler_block.signals")
        _validate_tuple(self.updates, ModelUpdate, "handler_block.updates")
        _validate_tuple(self.bindings, ModelBinding, "handler_block.bindings")
        if self.switches is not None:
            _validate_identifier(self.switches, "handler_block.switches")
        if self.kind in {"drives", "yields", "emits", "resumes"}:
            if self.expressions or self.deferred is not None or self.updates or self.switches is not None or self.bindings:
                raise ModelIRValidationError(f"{self.kind} block may only contain signals")
            expected_mode = {
                "drives": "drive",
                "yields": "yield",
                "emits": "emit",
                "resumes": "resume",
            }[self.kind]
            if any(signal.mode != expected_mode for signal in self.signals):
                raise ModelIRValidationError(f"{self.kind} block has a mismatched signal mode")
        elif self.kind == "deferred":
            if self.expressions or self.signals or self.deferred is None or self.updates or self.switches is not None or self.bindings:
                raise ModelIRValidationError("deferred block must contain one deferred declaration")
        elif self.kind == "updates":
            if self.expressions or self.signals or self.deferred is not None or self.switches is not None or self.bindings:
                raise ModelIRValidationError("updates block may only contain updates")
        elif self.kind == "switches":
            if self.expressions or self.signals or self.deferred is not None or self.updates or self.switches is None or self.bindings:
                raise ModelIRValidationError(
                    "switches block must contain exactly one binding"
                )
        elif self.kind == "binds":
            if self.expressions or self.signals or self.deferred is not None or self.updates or self.switches is not None:
                raise ModelIRValidationError("binds block may only contain bindings")
            if not self.bindings:
                raise ModelIRValidationError("binds block must contain at least one binding")
            names = tuple(binding.name for binding in self.bindings)
            if len(set(names)) != len(names):
                raise ModelIRValidationError("binds block contains a duplicate binding")
        elif self.kind in {"print", "panic"}:
            if self.signals or self.deferred is not None or self.updates or self.switches is not None or self.bindings:
                raise ModelIRValidationError(
                    f"{self.kind} block may only contain one string expression"
                )
            if len(self.expressions) != 1 or self.expressions[0].kind != "string":
                raise ModelIRValidationError(
                    f"{self.kind} block requires exactly one string expression"
                )
        elif self.signals or self.deferred is not None or self.updates or self.switches is not None or self.bindings:
            raise ModelIRValidationError(f"{self.kind} block may only contain expressions")


def _expression_identifiers(expression: ModelExpression) -> set[str]:
    result = (
        {str(expression.value)} if expression.kind == "identifier" else set()
    )
    for child in expression.children:
        result.update(_expression_identifiers(child))
    return result


def _validate_handler_locals(
    blocks: tuple[ModelHandlerBlock, ...],
    parameters: tuple[ModelParameter, ...],
) -> None:
    binding_blocks = tuple(block for block in blocks if block.kind == "binds")
    if len(binding_blocks) > 1:
        raise ModelIRValidationError("handler may contain at most one binds block")
    bindings = tuple(
        binding for block in binding_blocks for binding in block.bindings
    )
    names = tuple(binding.name for binding in bindings)
    reserved = {
        "self",
        "CurrentTaskRef",
        "CurrentCPU",
        "TaskFlowRef",
        "ResumeTargetRef",
        "InterruptFlowRef",
        "ExceptionFlowRef",
        "SyscallExitFlowRef",
        "InterruptControlRef",
    }
    parameter_names = {parameter.name for parameter in parameters}
    switch_names = {
        block.switches
        for block in blocks
        if block.kind == "switches" and block.switches is not None
    }
    conflicts = set(names) & (parameter_names | switch_names | reserved)
    if conflicts:
        raise ModelIRValidationError(
            f"binding {sorted(conflicts)[0]!r} conflicts with a handler local"
        )
    all_names = set(names)
    seen: set[str] = set()
    for binding in bindings:
        forward = (_expression_identifiers(binding.expression) & all_names) - seen
        if forward:
            raise ModelIRValidationError(
                f"binding {binding.name!r} references later binding {sorted(forward)[0]!r}"
            )
        seen.add(binding.name)
    for block in blocks:
        if block.kind != "updates":
            continue
        for update in block.updates:
            access = _expression_access(update.target)
            if access is not None and access[0][0] in all_names:
                raise ModelIRValidationError(
                    f"binding {access[0][0]!r} is read-only and cannot be updated"
                )


@dataclass(frozen=True, slots=True)
class ModelTransition:
    signal: tuple[str, ...]
    target_state: tuple[str, ...] | None
    blocks: tuple[ModelHandlerBlock, ...]
    abstract: bool = False
    override: bool = False
    parameters: tuple[ModelParameter, ...] = ()

    def __post_init__(self) -> None:
        _validate_signal_name(self.signal, "transition.signal")
        if self.signal[0] != "Transition":
            raise ModelIRValidationError(
                "transition.signal must have the form Transition::<Name>"
            )
        if type(self.abstract) is not bool or type(self.override) is not bool:
            raise ModelIRValidationError("transition abstract/override flags must be booleans")
        if self.target_state is None:
            if not self.abstract:
                raise ModelIRValidationError("concrete transition requires a target state")
        else:
            _validate_special_name(self.target_state, "State", "transition.target_state")
        if self.abstract and (self.target_state is not None or self.blocks):
            raise ModelIRValidationError(
                "abstract transition must not contain a target state or blocks"
            )
        _validate_tuple(self.blocks, ModelHandlerBlock, "transition.blocks")
        _validate_tuple(self.parameters, ModelParameter, "transition.parameters")
        names = tuple(parameter.name for parameter in self.parameters)
        if len(set(names)) != len(names):
            raise ModelIRValidationError("duplicate transition parameter")
        _validate_handler_locals(self.blocks, self.parameters)


@dataclass(frozen=True, slots=True)
class ModelAction:
    signal: tuple[str, ...]
    blocks: tuple[ModelHandlerBlock, ...]
    abstract: bool = False
    override: bool = False
    parameters: tuple[ModelParameter, ...] = ()

    def __post_init__(self) -> None:
        _validate_special_name(self.signal, "Action", "action.signal")
        _validate_tuple(self.blocks, ModelHandlerBlock, "action.blocks")
        if type(self.abstract) is not bool or type(self.override) is not bool:
            raise ModelIRValidationError("action abstract/override flags must be booleans")
        if self.abstract and self.blocks:
            raise ModelIRValidationError("abstract action must not contain blocks")
        if not self.abstract and not self.blocks:
            raise ModelIRValidationError(
                "concrete action must contain at least one handler block"
            )
        _validate_tuple(self.parameters, ModelParameter, "action.parameters")
        names = tuple(parameter.name for parameter in self.parameters)
        if len(set(names)) != len(names):
            raise ModelIRValidationError("duplicate action parameter")
        _validate_handler_locals(self.blocks, self.parameters)


@dataclass(frozen=True, slots=True)
class ModelState:
    name: tuple[str, ...]
    invariants: tuple[tuple[ModelExpression, ...], ...]
    transitions: tuple[ModelTransition, ...]
    actions: tuple[ModelAction, ...]

    def __post_init__(self) -> None:
        _validate_special_name(self.name, "State", "state.name")
        if type(self.invariants) is not tuple:
            raise ModelIRValidationError("state.invariants must be a tuple")
        for index, block in enumerate(self.invariants):
            _validate_tuple(block, ModelExpression, f"state.invariants[{index}]")
        _validate_tuple(self.transitions, ModelTransition, "state.transitions")
        _validate_tuple(self.actions, ModelAction, "state.actions")
        ordered = tuple(sorted(self.transitions, key=lambda item: item.signal))
        signals = [item.signal for item in ordered]
        if len(set(signals)) != len(signals):
            duplicate = next(signal for signal in signals if signals.count(signal) > 1)
            raise ModelIRValidationError(
                f"duplicate transition handler {'::'.join(duplicate)!r} in state {self.name[-1]!r}"
            )
        object.__setattr__(self, "transitions", ordered)
        action_signals = [item.signal for item in self.actions]
        if len(set(action_signals)) != len(action_signals):
            raise ModelIRValidationError(
                f"duplicate action handler in state {self.name[-1]!r}"
            )
        object.__setattr__(self, "actions", tuple(sorted(self.actions, key=lambda item: item.signal)))


@dataclass(frozen=True, slots=True)
class ModelReferenceAssignment:
    target: ModelExpression
    value: ModelExpression

    def __post_init__(self) -> None:
        if not isinstance(self.target, ModelExpression):
            raise ModelIRValidationError("reference target must be a ModelExpression")
        if not isinstance(self.value, ModelExpression):
            raise ModelIRValidationError("reference value must be a ModelExpression")


@dataclass(frozen=True, slots=True)
class ModelReference:
    name: str
    assignments: tuple[ModelReferenceAssignment, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "reference.name")
        _validate_tuple(
            self.assignments, ModelReferenceAssignment, "reference.assignments"
        )


@dataclass(frozen=True, slots=True)
class ModelObject:
    name: tuple[str, ...]
    base_type: ModelTypeExpression
    initial_state: tuple[str, ...] | None
    parent: ModelExpression | None
    source: ModelExpression | None
    attrs: tuple[ModelField, ...] | None
    states: tuple[ModelState, ...]
    references: tuple[ModelReference, ...]
    continuation: bool = False
    idle_task: ModelExpression | None = None
    logical_id: int | None = None

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "object.name")
        if not isinstance(self.base_type, ModelTypeExpression):
            raise ModelIRValidationError("object.base_type must be a ModelTypeExpression")
        if type(self.continuation) is not bool:
            raise ModelIRValidationError("object.continuation must be a boolean")
        if self.idle_task is not None and not isinstance(
            self.idle_task, ModelExpression
        ):
            raise ModelIRValidationError(
                "object.idle_task must be a ModelExpression or null"
            )
        if self.logical_id is not None and (
            type(self.logical_id) is not int or self.logical_id < 0
        ):
            raise ModelIRValidationError(
                "object.logical_id must be a non-negative integer or null"
            )
        if self.initial_state is not None:
            _validate_special_name(self.initial_state, "State", "object.initial_state")
        for path, expression in (("object.parent", self.parent), ("object.source", self.source)):
            if expression is not None and not isinstance(expression, ModelExpression):
                raise ModelIRValidationError(f"{path} must be a ModelExpression or null")
        if self.attrs is not None:
            _validate_tuple(self.attrs, ModelField, "object.attrs")
            attr_names = [field.name for field in self.attrs]
            if len(set(attr_names)) != len(attr_names):
                raise ModelIRValidationError(f"duplicate attr in object {'.'.join(self.name)!r}")
        _validate_tuple(self.states, ModelState, "object.states")
        ordered_states = tuple(sorted(self.states, key=lambda item: item.name))
        state_names = [state.name for state in ordered_states]
        if len(set(state_names)) != len(state_names):
            raise ModelIRValidationError(f"duplicate state in object {'.'.join(self.name)!r}")
        _validate_tuple(self.references, ModelReference, "object.references")
        reference_names = [reference.name for reference in self.references]
        if len(set(reference_names)) != len(reference_names):
            raise ModelIRValidationError(f"duplicate reference in object {'.'.join(self.name)!r}")
        object.__setattr__(self, "states", ordered_states)
        object.__setattr__(self, "references", tuple(sorted(self.references, key=lambda item: item.name)))


@dataclass(frozen=True, slots=True)
class ModelExternal:
    name: tuple[str, ...]
    signals: tuple[ModelSignal, ...]

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "external.name")
        _validate_tuple(self.signals, ModelSignal, "external.signals")
        if any(signal.source != self.name for signal in self.signals):
            raise ModelIRValidationError(
                f"external {'.'.join(self.name)!r} contains a signal with another source"
            )
        if any(signal.mode == "yield" for signal in self.signals):
            raise ModelIRValidationError(
                "external declarations cannot use the internal yield signal mode"
            )


@dataclass(frozen=True, slots=True)
class ModelModule:
    name: tuple[str, ...]
    predicates: tuple[ModelPredicate, ...] = ()
    types: tuple[ModelType, ...] = ()
    objects: tuple[ModelObject, ...] = ()
    externals: tuple[ModelExternal, ...] = ()

    _COLLECTIONS: ClassVar[tuple[tuple[str, type], ...]] = (
        ("predicates", ModelPredicate),
        ("types", ModelType),
        ("objects", ModelObject),
        ("externals", ModelExternal),
    )

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "module.name")
        all_names: list[tuple[str, ...]] = []
        for field_name, item_type in self._COLLECTIONS:
            values = getattr(self, field_name)
            _validate_tuple(values, item_type, f"module.{field_name}")
            ordered = tuple(sorted(values, key=lambda item: item.name))
            for item in ordered:
                if item.name[:-1] != self.name:
                    raise ModelIRValidationError(
                        f"declaration {'.'.join(item.name)!r} is not in module {'.'.join(self.name)!r}"
                    )
                all_names.append(item.name)
            object.__setattr__(self, field_name, ordered)
        if len(set(all_names)) != len(all_names):
            duplicate = next(name for name in all_names if all_names.count(name) > 1)
            raise ModelIRValidationError(f"duplicate declaration {'.'.join(duplicate)!r}")


@dataclass(frozen=True, slots=True)
class ModelIR:
    schema_version: int
    entry: ModelEntry
    modules: tuple[ModelModule, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise ModelIRValidationError("schema_version must be an integer")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelIRValidationError(
                f"unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION}"
            )
        if not isinstance(self.entry, ModelEntry):
            raise ModelIRValidationError("entry must be a ModelEntry")
        _validate_tuple(self.modules, ModelModule, "modules")
        ordered = tuple(sorted(self.modules, key=lambda module: module.name))
        names = tuple(module.name for module in ordered)
        if len(set(names)) != len(names):
            duplicate = next(name for name in names if names.count(name) > 1)
            raise ModelIRValidationError(f"duplicate module {'.'.join(duplicate)!r}")
        if not names:
            raise ModelIRValidationError("modules must not be empty")
        name_set = set(names)
        for name in names:
            if len(name) > 1 and name[:-1] not in name_set:
                raise ModelIRValidationError(
                    f"module {'.'.join(name)!r} is missing parent module {'.'.join(name[:-1])!r}"
                )
        root_names = {name for name in names if len(name) == 1}
        if self.entry.spec not in root_names:
            raise ModelIRValidationError("entry.spec must name a declared root module")

        object_items = {
            item.name: item for module in ordered for item in module.objects
        }
        objects = set(object_items)
        type_items = {
            item.name: item for module in ordered for item in module.types
        }

        def static_target(expression: ModelExpression) -> tuple[str, ...] | None:
            access = _expression_access(expression)
            if access is None or not access[0] or any(
                operation != "path" for operation in access[1]
            ):
                return None
            raw = access[0]
            if raw in object_items:
                return raw
            matches = tuple(
                name for name in object_items if name[-len(raw) :] == raw
            )
            return matches[0] if len(matches) == 1 else None

        def resolve_type_name(
            expression: ModelTypeExpression, module: tuple[str, ...]
        ) -> tuple[str, ...] | None:
            candidates = (expression.name, module + expression.name)
            for candidate in candidates:
                if candidate in type_items:
                    return candidate
            matches = tuple(
                name
                for name in type_items
                if name[-len(expression.name) :] == expression.name
            )
            return matches[0] if len(matches) == 1 else None

        def object_has_type(model_object: ModelObject, suffix: str) -> bool:
            current = resolve_type_name(
                model_object.base_type, model_object.name[:-1]
            )
            seen: set[tuple[str, ...]] = set()
            while current is not None and current not in seen:
                seen.add(current)
                if current[-1] == suffix:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        def object_is_sched_core(model_object: ModelObject) -> bool:
            current = resolve_type_name(
                model_object.base_type, model_object.name[:-1]
            )
            seen: set[tuple[str, ...]] = set()
            while current is not None and current not in seen:
                seen.add(current)
                if type_items[current].sched_core:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        def object_is_user_runtime(model_object: ModelObject) -> bool:
            current = resolve_type_name(
                model_object.base_type, model_object.name[:-1]
            )
            seen: set[tuple[str, ...]] = set()
            while current is not None and current not in seen:
                seen.add(current)
                if type_items[current].user_runtime:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        def object_is_cpu_core(model_object: ModelObject) -> bool:
            current = resolve_type_name(
                model_object.base_type, model_object.name[:-1]
            )
            seen: set[tuple[str, ...]] = set()
            while current is not None and current not in seen:
                seen.add(current)
                if type_items[current].cpu_core:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        def object_is_syscall_exit_flow(model_object: ModelObject) -> bool:
            current = resolve_type_name(
                model_object.base_type, model_object.name[:-1]
            )
            seen: set[tuple[str, ...]] = set()
            while current is not None and current not in seen:
                seen.add(current)
                if type_items[current].syscall_exit_flow:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        def object_is_event_flow(model_object: ModelObject) -> bool:
            current = resolve_type_name(
                model_object.base_type, model_object.name[:-1]
            )
            seen: set[tuple[str, ...]] = set()
            while current is not None and current not in seen:
                seen.add(current)
                if type_items[current].event_flow:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        bootstrap_idle_tasks = {
            idle
            for scheduler in object_items.values()
            if object_is_sched_core(scheduler) and scheduler.idle_task is not None
            for idle in (static_target(scheduler.idle_task),)
            if idle is not None
        }

        def type_is_task(model_type: ModelType) -> bool:
            if len(task_types) != 1:
                return False
            task_name = task_types[0].name
            current = model_type.name
            seen: set[tuple[str, ...]] = set()
            while current in type_items and current not in seen:
                seen.add(current)
                if current == task_name:
                    return True
                base = type_items[current].base_type
                current = (
                    None
                    if base is None
                    else resolve_type_name(base, current[:-1])
                )
            return False

        user_runtime_types = tuple(
            item for item in type_items.values() if item.user_runtime
        )
        cpu_core_types = tuple(
            item for item in type_items.values() if item.cpu_core
        )
        syscall_exit_flow_types = tuple(
            item for item in type_items.values() if item.syscall_exit_flow
        )
        interrupt_flow_types = tuple(
            item
            for item in type_items.values()
            if item.event_flow and item.continuation and item.name[-1] == "InterruptFlow"
        )
        exception_flow_types = tuple(
            item
            for item in type_items.values()
            if item.event_flow and item.continuation and item.name[-1] == "ExceptionFlow"
        )
        if len(cpu_core_types) > 1:
            raise ModelIRValidationError(
                "the model may declare at most one cpu_core type"
            )
        if len(syscall_exit_flow_types) > 1:
            raise ModelIRValidationError(
                "the model may declare at most one syscall_exit_flow type"
            )
        if len(interrupt_flow_types) > 1:
            raise ModelIRValidationError(
                "the model may declare at most one interrupt_flow type"
            )
        if len(exception_flow_types) > 1:
            raise ModelIRValidationError(
                "the model may declare at most one exception_flow type"
            )

        def protocol_action(
            model_type: ModelType, signal: tuple[str, ...]
        ) -> ModelAction | None:
            online = next(
                (
                    state
                    for state in model_type.states
                    if state.name == ("State", "Online")
                ),
                None,
            )
            return None if online is None else next(
                (action for action in online.actions if action.signal == signal),
                None,
            )

        def one_i32_parameter(action: ModelAction | None, name: str) -> bool:
            return bool(
                action is not None
                and not action.abstract
                and len(action.parameters) == 1
                and action.parameters[0].name == name
                and action.parameters[0].type == ModelTypeExpression(("i32",))
            )

        for model_type in cpu_core_types:
            for signal_name, selector, flow_types in (
                ("OnInterrupt", "InterruptFlowRef", interrupt_flow_types),
                ("OnException", "ExceptionFlowRef", exception_flow_types),
            ):
                if not flow_types:
                    continue
                event_action = protocol_action(
                    model_type, ("Action", signal_name)
                )
                event_signals = () if event_action is None else tuple(
                    signal
                    for block in event_action.blocks
                    if block.kind == "resumes"
                    for signal in block.signals
                )
                if (
                    event_action is None
                    or event_action.abstract
                    or event_action.parameters
                    or len(event_signals) != 1
                    or _expression_access(event_signals[0].target)
                    != (("self", selector), ("member",))
                    or event_signals[0].signal != ("Action", "Enter")
                    or event_signals[0].mode != "resume"
                    or event_signals[0].arguments
                ):
                    raise ModelIRValidationError(
                        f"cpu_core requires Online Action::{signal_name} forwarding to self.{selector}.Action::Enter"
                    )
            if not syscall_exit_flow_types:
                continue
            action = protocol_action(model_type, ("Action", "OnSyscallExit"))
            signals = () if action is None else tuple(
                signal
                for block in action.blocks
                if block.kind == "resumes"
                for signal in block.signals
            )
            if (
                not one_i32_parameter(action, "status")
                or len(signals) != 1
                or _expression_access(signals[0].target)
                != (("self", "SyscallExitFlowRef"), ("member",))
                or signals[0].signal != ("Action", "Enter")
                or signals[0].mode != "resume"
                or signals[0].arguments
                != (ModelExpression("identifier", "status"),)
            ):
                raise ModelIRValidationError(
                    "cpu_core requires Online Action::OnSyscallExit(status: i32) "
                    "forwarding to self.SyscallExitFlowRef.Action::Enter(status)"
                )

        for model_type in syscall_exit_flow_types:
            if not model_type.event_flow:
                raise ModelIRValidationError(
                    "syscall_exit_flow types must inherit the event_flow protocol"
                )
            action = protocol_action(model_type, ("Action", "Enter"))
            signals = () if action is None else tuple(
                signal
                for block in action.blocks
                if block.kind == "drives"
                for signal in block.signals
            )
            if (
                not one_i32_parameter(action, "status")
                or len(signals) != 1
                or signals[0].target
                != ModelExpression("identifier", "CurrentTaskRef")
                or signals[0].signal != ("Action", "Exit")
                or signals[0].mode != "drive"
                or signals[0].arguments
                != (ModelExpression("identifier", "status"),)
            ):
                raise ModelIRValidationError(
                    "syscall_exit_flow requires Online Action::Enter(status: i32) "
                    "driving CurrentTaskRef.Action::Exit(status)"
                )

        for model_type in (*interrupt_flow_types, *exception_flow_types):
            action = protocol_action(model_type, ("Action", "Enter"))
            if action is None or action.abstract or action.parameters:
                raise ModelIRValidationError(
                    "returning event_flow types require a concrete parameterless Online Action::Enter"
                )

        task_flow_types = tuple(
            item for item in type_items.values() if item.name[-1] == "TaskFlow"
        )
        if cpu_core_types and task_flow_types:
            fields = {
                field.name: field for field in task_flow_types[0].fields or ()
            }
            cpu_ref = fields.get("cpu_ref")
            resolved_cpu_ref = (
                None
                if cpu_ref is None
                else resolve_type_name(cpu_ref.type, task_flow_types[0].name[:-1])
            )
            if (
                cpu_ref is None
                or not cpu_ref.mutable
                or resolved_cpu_ref != cpu_core_types[0].name
            ):
                raise ModelIRValidationError(
                    "TaskFlow requires mutable cpu_ref: CPU when cpu_core is modeled"
                )
        task_types = tuple(
            item for item in type_items.values() if item.name[-1] == "Task"
        )
        if len(user_runtime_types) > 1:
            raise ModelIRValidationError(
                "the model may declare at most one user_runtime type"
            )
        for model_type in user_runtime_types:
            online = next(
                (
                    state
                    for state in model_type.states
                    if state.name == ("State", "Online")
                ),
                None,
            )
            enter = None if online is None else next(
                (
                    action
                    for action in online.actions
                    if action.signal == ("Action", "Enter")
                ),
                None,
            )
            transitions = {
                (state.name, transition.signal, transition.target_state)
                for state in model_type.states
                for transition in state.transitions
            }
            required = {
                (
                    ("State", "Base"),
                    ("Transition", "Preset"),
                    ("State", "Prepared"),
                ),
                (
                    ("State", "Prepared"),
                    ("Transition", "Setup"),
                    ("State", "Ready"),
                ),
                (
                    ("State", "Ready"),
                    ("Transition", "Enable"),
                    ("State", "Online"),
                ),
            }
            if (
                model_type.continuation
                or model_type.sched_core
                or model_type.initial_state != ("State", "Base")
                or not required.issubset(transitions)
                or enter is None
                or not enter.abstract
                or enter.parameters
            ):
                raise ModelIRValidationError(
                    "user_runtime type requires the Base/Prepared/Ready/Online "
                    "lifecycle and an abstract, parameterless Online Action::Enter"
                )

        externals = {item.name for module in ordered for item in module.externals}
        if self.entry.origin not in externals:
            raise ModelIRValidationError(
                f"entry.origin {'.'.join(self.entry.origin)!r} is not a declared external"
            )
        for module in ordered:
            for model_type in module.types:
                if any(
                    action.signal == ("Action", "ResetCurrent")
                    for state in model_type.states
                    for action in state.actions
                ):
                    raise ModelIRValidationError(
                        "Action::ResetCurrent may only be declared by BootTask in State::OnCpu"
                    )
                if model_type.name[-1] in {
                    "TaskFlowRef", "ResumeTargetRef", "InterruptFlowRef",
                    "ExceptionFlowRef", "SyscallExitFlowRef", "InterruptControlRef"
                }:
                    raise ModelIRValidationError(
                        "TaskFlowRef and ResumeTargetRef are reserved read-only Task selectors and cannot be declared"
                    )
                if any(
                    field.name in {
                        "TaskFlowRef", "ResumeTargetRef", "InterruptFlowRef",
                        "ExceptionFlowRef", "SyscallExitFlowRef", "InterruptControlRef"
                    }
                    for field in model_type.fields or ()
                ):
                    raise ModelIRValidationError(
                        "TaskFlowRef and ResumeTargetRef are reserved read-only Task selectors and cannot be declared"
                    )
                type_state_names = tuple(state.name for state in model_type.states)
                if model_type.states and model_type.initial_state is None:
                    raise ModelIRValidationError(
                        f"stateful type {'.'.join(model_type.name)!r} requires initial_state"
                    )
                if (
                    model_type.initial_state is not None
                    and model_type.initial_state not in set(type_state_names)
                ):
                    raise ModelIRValidationError(
                        f"type {'.'.join(model_type.name)!r} has invalid initial_state "
                        f"{'::'.join(model_type.initial_state)!r}"
                    )
                if model_type.continuation and (
                    model_type.initial_state != ("State", "Online")
                    or type_state_names != (("State", "Online"),)
                    or model_type.states[0].transitions
                ):
                    raise ModelIRValidationError(
                        f"continuation type {'.'.join(model_type.name)!r} must have "
                        "exactly initial_state State::Online, one State::Online, "
                        "and no transitions"
                    )
                if model_type.cpu_core and (
                    model_type.continuation
                    or model_type.sched_core
                    or model_type.user_runtime
                ):
                    raise ModelIRValidationError(
                        "cpu_core cannot also be a continuation, sched_core, or user_runtime"
                    )
                if model_type.event_flow and (
                    model_type.cpu_core
                    or model_type.sched_core
                    or model_type.user_runtime
                ):
                    raise ModelIRValidationError(
                        "event_flow cannot also be cpu_core, sched_core, or user_runtime"
                    )
                if model_type.syscall_exit_flow and not model_type.continuation:
                    raise ModelIRValidationError(
                        "syscall_exit_flow types must also be continuations"
                    )
                if model_type.syscall_exit_flow and not model_type.event_flow:
                    raise ModelIRValidationError(
                        "syscall_exit_flow types must inherit the event_flow protocol"
                    )
                if model_type.sched_core and any(
                    action.signal in {
                        ("Action", "Enqueue"),
                        ("Action", "Dequeue"),
                    }
                    for state in model_type.states
                    for action in state.actions
                ):
                    raise ModelIRValidationError(
                        f"sched_core type {'.'.join(model_type.name)!r} must not "
                        "declare Action::Enqueue or Action::Dequeue"
                    )
                if type_is_task(model_type):
                    for state in model_type.states:
                        for transition in state.transitions:
                            if transition.signal != ("Transition", "Resume"):
                                continue
                            if (
                                state.name != ("State", "Online")
                                or transition.target_state != ("State", "OnCpu")
                            ):
                                raise ModelIRValidationError(
                                    "Task Transition::Resume is only allowed from "
                                    "State::Online to State::OnCpu"
                                )
                for state in model_type.states:
                    for handler in (*state.transitions, *state.actions):
                        for block in handler.blocks:
                            for signal in block.signals:
                                target_access = _expression_access(signal.target)
                                task_owned_selector = target_access in {
                                    (("self", "TaskFlowRef"), ("member",)),
                                    (("self", "ResumeTargetRef"), ("member",)),
                                }
                                event_flow_selector = (
                                    target_access is not None
                                    and target_access[0]
                                    in {
                                        ("self", "InterruptFlowRef"),
                                        ("self", "ExceptionFlowRef"),
                                        ("self", "SyscallExitFlowRef"),
                                    }
                                    and target_access[1] == ("member",)
                                )
                                mentions_task_selector = (
                                    target_access is not None
                                    and any(
                                        segment
                                        in {"TaskFlowRef", "ResumeTargetRef"}
                                        for segment in target_access[0]
                                    )
                                )
                                if mentions_task_selector and not task_owned_selector:
                                    raise ModelIRValidationError(
                                        "TaskFlowRef and ResumeTargetRef require the exact self-owned selector form"
                                    )
                                target_name = static_target(signal.target)
                                if (
                                    type_is_task(model_type)
                                    and isinstance(handler, ModelTransition)
                                    and handler.signal
                                    in {
                                        ("Transition", "Suspend"),
                                        ("Transition", "Resume"),
                                    }
                                    and target_name is not None
                                    and object_is_sched_core(object_items[target_name])
                                    and signal.signal
                                    in {
                                        ("Action", "Enqueue"),
                                        ("Action", "Dequeue"),
                                    }
                                ):
                                    raise ModelIRValidationError(
                                        "Task Suspend/Resume handlers must not call sched_core Enqueue/Dequeue"
                                    )
                                if (
                                    target_name is not None
                                    and object_has_type(
                                        object_items[target_name], "TaskFlow"
                                    )
                                    and signal.signal == ("Action", "Enter")
                                ):
                                    raise ModelIRValidationError(
                                        "TaskFlow Action::Enter must use a Task-owned TaskFlowRef or ResumeTargetRef selector"
                                    )
                                if event_flow_selector:
                                    expected_handler = {
                                        "InterruptFlowRef": ("Action", "OnInterrupt"),
                                        "ExceptionFlowRef": ("Action", "OnException"),
                                        "SyscallExitFlowRef": ("Action", "OnSyscallExit"),
                                    }[target_access[0][-1]]
                                    invalid_arguments = (
                                        bool(signal.arguments)
                                        if target_access[0][-1]
                                        != "SyscallExitFlowRef"
                                        else len(signal.arguments)
                                        != len(handler.parameters)
                                    )
                                    if (
                                        not model_type.cpu_core
                                        or handler.signal != expected_handler
                                        or block.kind != "resumes"
                                        or signal.mode != "resume"
                                        or signal.signal != ("Action", "Enter")
                                        or invalid_arguments
                                    ):
                                        raise ModelIRValidationError(
                                            "Event FlowRef selectors are only available in their matching CPU receive handler"
                                        )
                                    continue
                                if not task_owned_selector:
                                    continue
                                if not type_is_task(model_type):
                                    raise ModelIRValidationError(
                                        "TaskFlowRef and ResumeTargetRef are only available in Task handlers"
                                    )
                                if (
                                    signal.arguments
                                    or signal.signal != ("Action", "Enter")
                                ):
                                    raise ModelIRValidationError(
                                        "Task-owned resume selectors only accept parameterless Action::Enter"
                                    )
                                if signal.mode != "resume" or block.kind != "resumes":
                                    raise ModelIRValidationError(
                                        "Task-owned resume selector Action::Enter must use resumes"
                                    )
                            for update in block.updates:
                                access = _expression_access(update.target)
                                if access is not None and any(
                                    segment in {"TaskFlowRef", "ResumeTargetRef"}
                                    for segment in access[0]
                                ):
                                    raise ModelIRValidationError(
                                        "TaskFlowRef and ResumeTargetRef are read-only and cannot be updated"
                                    )
                type_state_set = set(type_state_names)
                for state in model_type.states:
                    for transition in state.transitions:
                        if (
                            not transition.abstract
                            and transition.target_state not in type_state_set
                        ):
                            assert transition.target_state is not None
                            raise ModelIRValidationError(
                                f"transition in type {'.'.join(model_type.name)!r} "
                                f"targets unknown state {'::'.join(transition.target_state)!r}"
                            )
            for external in module.externals:
                for signal in external.signals:
                    target_name = static_target(signal.target)
                    if target_name not in objects:
                        raise ModelIRValidationError(
                            "external signal target must resolve to a declared object"
                        )
                    assert target_name is not None
                    target = object_items[target_name]
                    if (
                        object_has_type(target, "TaskFlow")
                        and signal.signal == ("Action", "Enter")
                    ):
                        raise ModelIRValidationError(
                            "TaskFlow Action::Enter must use a Task-owned TaskFlowRef or ResumeTargetRef selector"
                        )
                    if (
                        object_is_sched_core(target)
                        and signal.signal
                        in {("Action", "Enqueue"), ("Action", "Dequeue")}
                    ):
                        raise ModelIRValidationError(
                            "external sources cannot call sched_core Enqueue/Dequeue"
                        )
                    if signal.mode == "resume" and (
                        not target.continuation
                        or signal.signal != ("Action", "Enter")
                    ):
                        raise ModelIRValidationError(
                            "resumes must target Action::Enter on a continuation object"
                        )
                    if target.continuation and (
                        signal.mode != "resume"
                        or signal.signal != ("Action", "Enter")
                    ):
                        raise ModelIRValidationError(
                            "external continuation entry must use resumes Action::Enter"
                        )
            for model_object in module.objects:
                if model_object.name[-1] in {
                    "CurrentTaskRef",
                    "CurrentCPU",
                    "TaskFlowRef",
                    "ResumeTargetRef",
                    "InterruptFlowRef",
                    "ExceptionFlowRef",
                    "SyscallExitFlowRef",
                    "InterruptControlRef",
                }:
                    raise ModelIRValidationError(
                        f"{model_object.name[-1]} is a reserved runtime selector and must not be "
                        "declared as an object"
                    )
                if object_is_user_runtime(model_object):
                    raise ModelIRValidationError(
                        "user_runtime instances are inference-owned Task children and "
                        "must not be declared as model objects"
                    )
                if object_is_event_flow(model_object):
                    raise ModelIRValidationError(
                        "event_flow instances are inference-owned CPU children and "
                        "must not be declared as model objects"
                    )
                if any(
                    any(
                        segment in {
                            "CurrentTaskRef",
                            "CurrentCPU",
                            "TaskFlowRef",
                            "ResumeTargetRef",
                            "InterruptFlowRef",
                            "ExceptionFlowRef",
                            "SyscallExitFlowRef",
                            "InterruptControlRef",
                        }
                        for segment in (
                            _expression_access(assignment.target) or ((), ())
                        )[0]
                    )
                    for reference in model_object.references
                    for assignment in reference.assignments
                ):
                    raise ModelIRValidationError(
                        "runtime selectors are read-only and cannot be assigned"
                    )
                if any(
                    field.name
                    in {
                        "CurrentCPU",
                        "TaskFlowRef",
                        "ResumeTargetRef",
                        "InterruptFlowRef",
                        "ExceptionFlowRef",
                        "SyscallExitFlowRef",
                        "InterruptControlRef",
                    }
                    for field in model_object.attrs or ()
                ) or any(
                    reference.name
                    in {
                        "CurrentCPU",
                        "TaskFlowRef",
                        "ResumeTargetRef",
                        "InterruptFlowRef",
                        "ExceptionFlowRef",
                        "SyscallExitFlowRef",
                        "InterruptControlRef",
                    }
                    for reference in model_object.references
                ):
                    raise ModelIRValidationError(
                        "TaskFlowRef and ResumeTargetRef are reserved read-only Task selectors and cannot be declared"
                    )
                state_names = tuple(state.name for state in model_object.states)
                state_name_set = set(state_names)
                sched_core = object_is_sched_core(model_object)
                cpu_core = object_is_cpu_core(model_object)
                if cpu_core and model_object.logical_id is None:
                    raise ModelIRValidationError(
                        f"cpu_core object {'.'.join(model_object.name)!r} requires logical_id"
                    )
                if not cpu_core and model_object.logical_id is not None:
                    raise ModelIRValidationError(
                        f"non-cpu_core object {'.'.join(model_object.name)!r} must not have logical_id"
                    )
                if object_has_type(model_object, "Task"):
                    for state in model_object.states:
                        for transition in state.transitions:
                            if transition.signal != ("Transition", "Resume"):
                                continue
                            if (
                                state.name != ("State", "Online")
                                or transition.target_state != ("State", "OnCpu")
                            ):
                                raise ModelIRValidationError(
                                    "Task Transition::Resume is only allowed from "
                                    "State::Online to State::OnCpu"
                                )
                idle_name = (
                    None
                    if model_object.idle_task is None
                    else static_target(model_object.idle_task)
                )
                if sched_core and idle_name is None:
                    raise ModelIRValidationError(
                        f"sched_core object {'.'.join(model_object.name)!r} requires idle_task"
                    )
                if sched_core and any(
                    object_is_cpu_core(item) for item in object_items.values()
                ):
                    parent_name = (
                        None
                        if model_object.parent is None
                        else static_target(model_object.parent)
                    )
                    if (
                        parent_name is None
                        or not object_is_cpu_core(object_items[parent_name])
                    ):
                        raise ModelIRValidationError(
                            "sched_core object must be owned by a cpu_core parent"
                        )
                if not sched_core and model_object.idle_task is not None:
                    raise ModelIRValidationError(
                        f"non-sched_core object {'.'.join(model_object.name)!r} must not have idle_task"
                    )
                if idle_name is not None and (
                    idle_name not in object_items
                    or not object_has_type(object_items[idle_name], "Task")
                ):
                    raise ModelIRValidationError(
                        f"idle_task on {'.'.join(model_object.name)!r} must reference a Task object"
                    )
                if sched_core and ("State", "Online") not in state_name_set:
                    raise ModelIRValidationError(
                        f"sched_core object {'.'.join(model_object.name)!r} requires State::Online"
                    )
                if sched_core and any(
                    action.signal
                    in {("Action", "Enqueue"), ("Action", "Dequeue")}
                    for state in model_object.states
                    for action in state.actions
                ):
                    raise ModelIRValidationError(
                        f"sched_core object {'.'.join(model_object.name)!r} must not declare core queue actions"
                    )
                if state_names and model_object.initial_state is None:
                    raise ModelIRValidationError(
                        f"stateful object {'.'.join(model_object.name)!r} requires initial_state"
                    )
                if not state_names and model_object.initial_state is not None:
                    raise ModelIRValidationError(
                        f"stateless object {'.'.join(model_object.name)!r} must not have initial_state"
                    )
                if (
                    model_object.initial_state is not None
                    and model_object.initial_state not in state_name_set
                ):
                    raise ModelIRValidationError(
                        f"object {'.'.join(model_object.name)!r} has invalid initial_state "
                        f"{'::'.join(model_object.initial_state)!r}"
                    )
                if model_object.continuation:
                    if (
                        model_object.initial_state != ("State", "Online")
                        or state_names != (("State", "Online"),)
                    ):
                        raise ModelIRValidationError(
                            f"continuation object {'.'.join(model_object.name)!r} must have "
                            "exactly initial_state State::Online and one State::Online"
                        )
                    if model_object.states[0].transitions:
                        raise ModelIRValidationError(
                            f"continuation object {'.'.join(model_object.name)!r} must not "
                            "declare transitions"
                        )
                    if not any(
                        action.signal == ("Action", "Enter")
                        for action in model_object.states[0].actions
                    ):
                        raise ModelIRValidationError(
                            f"continuation object {'.'.join(model_object.name)!r} requires "
                            "a concrete Action::Enter handler"
                        )
                for state in model_object.states:
                    for handler in (*state.transitions, *state.actions):
                        if handler.abstract:
                            raise ModelIRValidationError(
                                f"object {'.'.join(model_object.name)!r} contains an "
                                f"abstract {'transition' if isinstance(handler, ModelTransition) else 'action'} handler"
                            )
                        if (
                            isinstance(handler, ModelTransition)
                            and handler.target_state not in state_name_set
                        ):
                            assert handler.target_state is not None
                            raise ModelIRValidationError(
                                f"transition in {'.'.join(model_object.name)!r} targets "
                                f"unknown state {'::'.join(handler.target_state)!r}"
                            )
                        switched_bindings: set[str] = set()
                        switch_count = 0
                        for block in handler.blocks:
                            if block.kind == "switches":
                                switch_count += 1
                                if (
                                    switch_count > 1
                                    or not sched_core
                                    or not isinstance(handler, ModelAction)
                                ):
                                    raise ModelIRValidationError(
                                        "switches is allowed at most once in a sched_core Action handler"
                                    )
                                assert block.switches is not None
                                if block.switches in {
                                    parameter.name
                                    for parameter in handler.parameters
                                }:
                                    raise ModelIRValidationError(
                                        "switches binding conflicts with a handler parameter"
                                    )
                                switched_bindings.add(block.switches)
                                continue
                            for signal in block.signals:
                                if signal.source != model_object.name:
                                    raise ModelIRValidationError(
                                        f"handler in {'.'.join(model_object.name)!r} contains a signal with another source"
                                    )
                                target_name = static_target(signal.target)
                                dynamic_name = (
                                    str(signal.target.value)
                                    if signal.target.kind == "identifier"
                                    else None
                                )
                                target_access = _expression_access(signal.target)
                                runtime_selector = target_access == (
                                    ("CurrentTaskRef", "UserAppRuntimeRef"),
                                    ("member",),
                                )
                                task_owned_selector = target_access in {
                                    (("self", "TaskFlowRef"), ("member",)),
                                    (("self", "ResumeTargetRef"), ("member",)),
                                }
                                event_flow_selector = (
                                    target_access is not None
                                    and target_access[0]
                                    in {
                                        ("self", "InterruptFlowRef"),
                                        ("self", "ExceptionFlowRef"),
                                        ("self", "SyscallExitFlowRef"),
                                    }
                                    and target_access[1] == ("member",)
                                )
                                interrupt_control_selector = target_access == (
                                    ("CurrentCPU", "InterruptControlRef"),
                                    ("member",),
                                )
                                if task_owned_selector:
                                    if not object_has_type(model_object, "Task"):
                                        raise ModelIRValidationError(
                                            "TaskFlowRef and ResumeTargetRef are only available in Task handlers"
                                        )
                                    if (
                                        signal.arguments
                                        or signal.signal != ("Action", "Enter")
                                    ):
                                        raise ModelIRValidationError(
                                            "Task-owned resume selectors only accept parameterless Action::Enter"
                                        )
                                    if signal.mode != "resume":
                                        raise ModelIRValidationError(
                                            "Task-owned resume selector Action::Enter must use resumes"
                                        )
                                    continue
                                if event_flow_selector:
                                    expected_handler = {
                                        "InterruptFlowRef": ("Action", "OnInterrupt"),
                                        "ExceptionFlowRef": ("Action", "OnException"),
                                        "SyscallExitFlowRef": ("Action", "OnSyscallExit"),
                                    }[target_access[0][-1]]
                                    invalid_arguments = (
                                        bool(signal.arguments)
                                        if target_access[0][-1]
                                        != "SyscallExitFlowRef"
                                        else len(signal.arguments)
                                        != len(handler.parameters)
                                    )
                                    if (
                                        not cpu_core
                                        or handler.signal != expected_handler
                                        or block.kind != "resumes"
                                        or signal.mode != "resume"
                                        or signal.signal != ("Action", "Enter")
                                        or invalid_arguments
                                    ):
                                        raise ModelIRValidationError(
                                            "Event FlowRef selectors are only available in their matching CPU receive handler"
                                        )
                                    continue
                                dynamic = runtime_selector or interrupt_control_selector or dynamic_name in {
                                    "CurrentTaskRef",
                                    "CurrentCPU",
                                    *switched_bindings,
                                }
                                if target_name is None and not dynamic:
                                    raise ModelIRValidationError(
                                        "dynamic signal target is not in scope"
                                    )
                                if dynamic:
                                    valid_runtime = (
                                        runtime_selector
                                        and len(task_types) == 1
                                        and len(user_runtime_types) == 1
                                        and (
                                            not signal.arguments
                                            or dynamic_name
                                            in {"CurrentTaskRef", "CurrentCPU"}
                                        )
                                    )
                                    valid_task = (
                                        not runtime_selector
                                        and dynamic_name in {
                                            "CurrentTaskRef",
                                            *switched_bindings,
                                        }
                                        and len(task_types) == 1
                                        and (
                                            not signal.arguments
                                            or dynamic_name == "CurrentTaskRef"
                                        )
                                    )
                                    valid_cpu = (
                                        dynamic_name == "CurrentCPU"
                                        and not signal.arguments
                                    )
                                    valid_interrupt_control = (
                                        interrupt_control_selector
                                        and not signal.arguments
                                        and signal.signal
                                        in {
                                            ("Action", "MaskAll"),
                                            ("Action", "ClearPending"),
                                            ("Action", "Unmask"),
                                        }
                                    )
                                    if not (
                                        valid_runtime
                                        or valid_task
                                        or valid_cpu
                                        or valid_interrupt_control
                                    ):
                                        raise ModelIRValidationError(
                                            "dynamic signal target is unavailable or has arguments"
                                        )
                                    continue
                                assert target_name is not None
                                if target_name not in objects:
                                    raise ModelIRValidationError(
                                        "signal targets unknown object"
                                    )
                                target = object_items[target_name]
                                if (
                                    object_has_type(model_object, "Task")
                                    and isinstance(handler, ModelTransition)
                                    and handler.signal
                                    in {
                                        ("Transition", "Suspend"),
                                        ("Transition", "Resume"),
                                    }
                                    and object_is_sched_core(target)
                                    and signal.signal
                                    in {
                                        ("Action", "Enqueue"),
                                        ("Action", "Dequeue"),
                                    }
                                ):
                                    raise ModelIRValidationError(
                                        "Task Suspend/Resume handlers must not call sched_core Enqueue/Dequeue"
                                    )
                                if (
                                    object_has_type(target, "TaskFlow")
                                    and signal.signal == ("Action", "Enter")
                                ):
                                    raise ModelIRValidationError(
                                        "TaskFlow Action::Enter must use a Task-owned TaskFlowRef or ResumeTargetRef selector"
                                    )
                                if (
                                    object_is_sched_core(target)
                                    and signal.signal
                                    in {
                                        ("Action", "Enqueue"),
                                        ("Action", "Dequeue"),
                                    }
                                    and (
                                        signal.arguments
                                        or not object_has_type(
                                            model_object, "Task"
                                        )
                                    )
                                ):
                                    raise ModelIRValidationError(
                                        "sched_core Enqueue/Dequeue accepts no arguments and requires a Task source"
                                    )
                                if signal.mode == "resume" and (
                                    not target.continuation
                                    or signal.signal != ("Action", "Enter")
                                ):
                                    raise ModelIRValidationError(
                                        "resumes must target Action::Enter on a "
                                        "continuation object"
                                    )
                                if target.continuation and not (
                                    signal.mode == "resume"
                                    and signal.signal == ("Action", "Enter")
                                ) and not (
                                    signal.signal[0] == "Action"
                                    and signal.source == target_name
                                    and signal.mode == "drive"
                                ):
                                    raise ModelIRValidationError(
                                        "continuation entry from outside must use "
                                        "resumes Action::Enter"
                                    )
                                if block.kind == "yields" and (
                                    not model_object.continuation
                                    or not isinstance(handler, ModelAction)
                                ):
                                    raise ModelIRValidationError(
                                        "yields is only allowed in a continuation "
                                        "Action handler"
                                    )
                            for update in block.updates:
                                access = _expression_access(update.target)
                                if access is not None and any(
                                    segment in {"TaskFlowRef", "ResumeTargetRef"}
                                    for segment in access[0]
                                ):
                                    raise ModelIRValidationError(
                                        "TaskFlowRef and ResumeTargetRef are read-only and cannot be updated"
                                    )
        for model_object in object_items.values():
            for state in model_object.states:
                for action in state.actions:
                    if action.signal != ("Action", "ResetCurrent"):
                        continue
                    if (
                        model_object.name not in bootstrap_idle_tasks
                        or state.name != ("State", "OnCpu")
                        or action.parameters
                    ):
                        raise ModelIRValidationError(
                            "Action::ResetCurrent may only be declared by BootTask in State::OnCpu"
                        )
        tasks = tuple(
            item for item in object_items.values() if object_has_type(item, "Task")
        )
        task_flows = tuple(
            item
            for item in object_items.values()
            if object_has_type(item, "TaskFlow")
        )
        if tasks or task_flows:
            for task in tasks:
                parents = tuple(
                    flow
                    for flow in task_flows
                    if flow.parent is not None
                    and static_target(flow.parent) == task.name
                )
                if len(parents) != 1:
                    raise ModelIRValidationError(
                        f"Task object {'.'.join(task.name)!r} requires exactly one parent TaskFlow; got {len(parents)}"
                    )
        cpu_ids = tuple(
            item.logical_id
            for item in object_items.values()
            if object_is_cpu_core(item)
        )
        if len(set(cpu_ids)) != len(cpu_ids):
            raise ModelIRValidationError("cpu_core logical_id values must be unique")
        object.__setattr__(self, "modules", ordered)

    @property
    def objects(self) -> tuple[ModelObject, ...]:
        return tuple(item for module in self.modules for item in module.objects)

    @property
    def externals(self) -> tuple[ModelExternal, ...]:
        return tuple(item for module in self.modules for item in module.externals)
