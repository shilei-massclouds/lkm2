"""Frozen data model and semantic validation for Model IR schema v4."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import ClassVar


SCHEMA_VERSION = 4
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_EXPRESSION_KINDS = frozenset(
    {"identifier", "integer", "string", "unary", "binary", "member", "path", "index", "call"}
)
_BLOCK_KINDS = frozenset(
    {
        "depends_on",
        "may_change",
        "drives",
        "ensures",
        "establishes",
        "emits",
        "deferred",
    }
)
_SIGNAL_MODES = frozenset({"drive", "emit"})
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

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "field.name")
        if not isinstance(self.type, ModelTypeExpression):
            raise ModelIRValidationError("field.type must be a ModelTypeExpression")


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

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "type.name")
        if self.fields is not None:
            _validate_tuple(self.fields, ModelField, "type.fields")
            names = [field.name for field in self.fields]
            if len(set(names)) != len(names):
                raise ModelIRValidationError(f"duplicate field in type {'.'.join(self.name)!r}")


@dataclass(frozen=True, slots=True)
class ModelSignal:
    source: tuple[str, ...]
    target: tuple[str, ...]
    signal: tuple[str, ...]
    mode: str

    def __post_init__(self) -> None:
        _validate_qualified_name(self.source, "signal.source")
        _validate_qualified_name(self.target, "signal.target")
        _validate_signal_name(self.signal, "signal.signal")
        if len(self.source) < 2 or len(self.target) < 2:
            raise ModelIRValidationError(
                "signal source and target must be absolute declaration names"
            )
        if self.mode not in _SIGNAL_MODES:
            raise ModelIRValidationError(
                f"signal.mode must be 'drive' or 'emit', got {self.mode!r}"
            )


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

    def __post_init__(self) -> None:
        if self.kind not in _BLOCK_KINDS:
            raise ModelIRValidationError(f"invalid handler block kind {self.kind!r}")
        _validate_tuple(self.expressions, ModelExpression, "handler_block.expressions")
        _validate_tuple(self.signals, ModelSignal, "handler_block.signals")
        if self.kind in {"drives", "emits"}:
            if self.expressions or self.deferred is not None:
                raise ModelIRValidationError(f"{self.kind} block may only contain signals")
            expected_mode = "drive" if self.kind == "drives" else "emit"
            if any(signal.mode != expected_mode for signal in self.signals):
                raise ModelIRValidationError(f"{self.kind} block has a mismatched signal mode")
        elif self.kind == "deferred":
            if self.expressions or self.signals or self.deferred is None:
                raise ModelIRValidationError("deferred block must contain one deferred declaration")
        elif self.signals or self.deferred is not None:
            raise ModelIRValidationError(f"{self.kind} block may only contain expressions")


@dataclass(frozen=True, slots=True)
class ModelTransition:
    signal: tuple[str, ...]
    target_state: tuple[str, ...]
    blocks: tuple[ModelHandlerBlock, ...]

    def __post_init__(self) -> None:
        _validate_signal_name(self.signal, "transition.signal")
        if self.signal[0] != "Transition":
            raise ModelIRValidationError(
                "transition.signal must have the form Transition::<Name>"
            )
        _validate_special_name(self.target_state, "State", "transition.target_state")
        _validate_tuple(self.blocks, ModelHandlerBlock, "transition.blocks")


@dataclass(frozen=True, slots=True)
class ModelAction:
    signal: tuple[str, ...]
    blocks: tuple[ModelHandlerBlock, ...]

    def __post_init__(self) -> None:
        _validate_special_name(self.signal, "Action", "action.signal")
        _validate_tuple(self.blocks, ModelHandlerBlock, "action.blocks")


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

    def __post_init__(self) -> None:
        _validate_qualified_name(self.name, "object.name")
        if not isinstance(self.base_type, ModelTypeExpression):
            raise ModelIRValidationError("object.base_type must be a ModelTypeExpression")
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
        if ordered_states and self.initial_state is None:
            raise ModelIRValidationError(
                f"stateful object {'.'.join(self.name)!r} requires initial_state"
            )
        if not ordered_states and self.initial_state is not None:
            raise ModelIRValidationError(
                f"stateless object {'.'.join(self.name)!r} must not have initial_state"
            )
        if self.initial_state is not None and self.initial_state not in set(state_names):
            raise ModelIRValidationError(
                f"object {'.'.join(self.name)!r} has invalid initial_state {'::'.join(self.initial_state)!r}"
            )
        for state in ordered_states:
            for transition in state.transitions:
                if transition.target_state not in set(state_names):
                    raise ModelIRValidationError(
                        f"transition in {'.'.join(self.name)!r} targets unknown state {'::'.join(transition.target_state)!r}"
                    )
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

        objects = {item.name for module in ordered for item in module.objects}
        externals = {item.name for module in ordered for item in module.externals}
        if self.entry.origin not in externals:
            raise ModelIRValidationError(
                f"entry.origin {'.'.join(self.entry.origin)!r} is not a declared external"
            )
        for module in ordered:
            for external in module.externals:
                for signal in external.signals:
                    if signal.target not in objects:
                        raise ModelIRValidationError(
                            f"signal targets unknown object {'.'.join(signal.target)!r}"
                        )
            for model_object in module.objects:
                for state in model_object.states:
                    for handler in (*state.transitions, *state.actions):
                        for block in handler.blocks:
                            for signal in block.signals:
                                if signal.source != model_object.name:
                                    raise ModelIRValidationError(
                                        f"handler in {'.'.join(model_object.name)!r} contains a signal with another source"
                                    )
                                if signal.target not in objects:
                                    raise ModelIRValidationError(
                                        f"signal targets unknown object {'.'.join(signal.target)!r}"
                                    )
        object.__setattr__(self, "modules", ordered)

    @property
    def objects(self) -> tuple[ModelObject, ...]:
        return tuple(item for module in self.modules for item in module.objects)

    @property
    def externals(self) -> tuple[ModelExternal, ...]:
        return tuple(item for module in self.modules for item in module.externals)
