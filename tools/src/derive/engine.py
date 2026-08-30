"""Single-threaded deterministic state derivation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json

from model_ir import (
    ModelAction,
    ModelBinding,
    ModelExpression,
    ModelHandlerBlock,
    ModelIR,
    ModelObject,
    ModelSignal,
    ModelTypeExpression,
)

from .model import (
    RESULT_SCHEMA_VERSION,
    DerivationCheck,
    DerivationBinding,
    DerivationBindingResult,
    DerivationContinuation,
    DerivationDirective,
    DerivationEvent,
    DerivationEventFlow,
    DerivationInterruptControl,
    DerivationFact,
    DerivationFailure,
    DerivationFrame,
    DerivationPath,
    DerivationResult,
    DerivationScheduler,
    DerivationSwitch,
    DerivationTerm,
    DerivationTuple,
    DerivationRelationEffect,
    DerivationSequence,
    DerivationState,
    DerivationUnit,
    DerivationValue,
)
from .runtime_signals import (
    UserRuntimeSignal,
    UserRuntimeSignalProgram,
    default_user_runtime_signals,
)


@dataclass(slots=True)
class _ResumeFrame:
    object: tuple[str, ...]
    event: DerivationEvent
    kind: str
    handler: tuple[str, ...]
    control_index: int = 0
    entered: bool = False
    before: tuple[str, ...] | None = None
    depends_on: list[DerivationCheck] = field(default_factory=list)
    drives: list[DerivationUnit] = field(default_factory=list)
    ensures: list[DerivationCheck] = field(default_factory=list)
    establishes: list[DerivationCheck] = field(default_factory=list)
    invariants: list[DerivationCheck] = field(default_factory=list)
    emits: list[DerivationUnit] = field(default_factory=list)
    yields: list[DerivationUnit] = field(default_factory=list)
    directives: list[DerivationDirective] = field(default_factory=list)
    bindings: dict[str, DerivationTerm | ModelExpression] = field(
        default_factory=dict
    )
    binding_results: list[DerivationBindingResult] = field(default_factory=list)
    relation_effects: list[DerivationRelationEffect] = field(default_factory=list)
    staged_tuples: set[DerivationTuple] = field(default_factory=set)
    staged_values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None
    resumes: list[DerivationUnit] = field(default_factory=list)

    def reset_segment(self) -> None:
        self.depends_on.clear()
        self.drives.clear()
        self.ensures.clear()
        self.establishes.clear()
        self.invariants.clear()
        self.emits.clear()
        self.yields.clear()
        self.directives.clear()
        self.resumes.clear()


@dataclass(slots=True)
class _ContinuationRuntime:
    root: tuple[str, ...]
    frames: list[_ResumeFrame] = field(default_factory=list)
    executing: bool = False
    suspended: bool = False
    completed: bool = False
    waiting_yield_target: bool = False
    resume_requested: bool = False
    owner_active: bool = False
    result_unit: DerivationUnit | None = None


@dataclass(slots=True)
class _SchedulerRuntime:
    scheduler: tuple[str, ...]
    idle_task: tuple[str, ...]
    runq: list[tuple[str, ...]] = field(default_factory=list)


@dataclass(slots=True)
class _CpuRuntime:
    cpu: tuple[str, ...]
    logical_id: int
    scheduler: tuple[str, ...] | None = None
    next_syscall_exit_flow: int = 0
    active_syscall_exit_flow: tuple[str, ...] | None = None
    next_interrupt_flow: int = 0
    active_interrupt_flow: tuple[str, ...] | None = None
    next_exception_flow: int = 0
    active_exception_flow: tuple[str, ...] | None = None
    interrupt_mode: str = "Unknown"
    pending_interrupts: list["_PendingInterrupt"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _PendingInterrupt:
    signal: UserRuntimeSignal
    owner: tuple[str, ...]
    task_flow: tuple[str, ...]
    user_runtime: tuple[str, ...]


class _SwitchNeeded(Exception):
    def __init__(self, candidates: tuple[tuple[str, ...], ...]) -> None:
        super().__init__("scheduler switch requires path expansion")
        self.candidates = candidates


class _UnsupportedExpression(Exception):
    def __init__(self, feature: str) -> None:
        super().__init__(feature)
        self.feature = feature


def _unsupported_features(model: ModelIR) -> tuple[str, ...]:
    features: set[str] = set()
    for model_object in model.objects:
        if model_object.references:
            features.add("reference")
        for state in model_object.states:
            for handler in (*state.transitions, *state.actions):
                for block in handler.blocks:
                    if block.kind in {"may_change", "deferred"}:
                        features.add(block.kind)
    return tuple(sorted(features))


def _states(model: ModelIR) -> dict[tuple[str, ...], tuple[str, ...] | None]:
    return {model_object.name: model_object.initial_state for model_object in model.objects}


def _final_state(
    states: dict[tuple[str, ...], tuple[str, ...] | None],
) -> tuple[DerivationState, ...]:
    return tuple(DerivationState(name, state) for name, state in sorted(states.items()))


def _shortest_names(names: tuple[tuple[str, ...], ...]) -> dict[tuple[str, ...], str]:
    result: dict[tuple[str, ...], str] = {}
    for name in names:
        for length in range(1, len(name) + 1):
            suffix = name[-length:]
            if sum(other[-length:] == suffix for other in names) == 1:
                result[name] = "::".join(suffix)
                break
    return result


def _access(expression: ModelExpression) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    if expression.kind == "identifier":
        return (str(expression.value),), ()
    if expression.kind not in {"member", "path"}:
        return None
    base = _access(expression.children[0])
    if base is None:
        return None
    segments, operations = base
    return segments + (str(expression.value),), operations + (expression.kind,)


def _format_expression(expression: ModelExpression) -> str:
    if expression.kind == "identifier":
        return str(expression.value)
    if expression.kind == "integer":
        return str(expression.value)
    if expression.kind == "string":
        return json.dumps(expression.value, ensure_ascii=False)
    if expression.kind == "unary":
        return f"{expression.value}{_format_expression(expression.children[0])}"
    if expression.kind == "binary":
        left, right = expression.children
        return f"({_format_expression(left)} {expression.value} {_format_expression(right)})"
    if expression.kind in {"member", "path"}:
        separator = "." if expression.kind == "member" else "::"
        return f"{_format_expression(expression.children[0])}{separator}{expression.value}"
    if expression.kind == "index":
        return (
            f"{_format_expression(expression.children[0])}"
            f"[{_format_expression(expression.children[1])}]"
        )
    if expression.kind == "call":
        arguments = ", ".join(_format_expression(item) for item in expression.children[1:])
        return f"{_format_expression(expression.children[0])}({arguments})"
    raise _UnsupportedExpression(f"expression:{expression.kind}")


class _Context:
    def __init__(self, model: ModelIR) -> None:
        self.model = model
        self.objects = {item.name: item for item in model.objects}
        self.object_modules = {
            item.name: module.name
            for module in model.modules
            for item in module.objects
        }
        self.types = {
            item.name: item for module in model.modules for item in module.types
        }
        predicates = tuple(
            item for module in model.modules for item in module.predicates
        )
        self.predicates = {item.name: item for item in predicates}
        self.object_names = _shortest_names(tuple(self.objects))
        self.predicate_names = _shortest_names(tuple(self.predicates))
        self.schedulers: dict[tuple[str, ...], _SchedulerRuntime] = {}
        self.current_task_resolver = None
        self.user_runtime_type = next(
            (item for item in self.types.values() if item.user_runtime), None
        )
        self.user_runtime_resolver = None
        self.task_flow_resolver = None
        self.resume_target_resolver = None
        self.current_cpu_resolver = None
        self.interrupt_control_resolver = None
        self.interrupt_flow_resolver = None
        self.exception_flow_resolver = None
        self.syscall_exit_flow_resolver = None
        self.event_flow_types = {
            item.name[-1]: item
            for item in self.types.values()
            if item.event_flow and item.continuation
        }
        self.syscall_exit_flow_type = next(
            (item for item in self.types.values() if item.syscall_exit_flow), None
        )
        self.containers: dict[
            tuple[str, ...], tuple[str, tuple[str, ...], tuple[str, ...]]
        ] = {}
        for model_object in model.objects:
            if model_object.base_type.name not in {("Relation",), ("Map",)}:
                continue
            key_type, value_type = model_object.base_type.arguments
            self.containers[model_object.name] = (
                model_object.base_type.name[0],
                self.canonical_type(key_type, self.object_module(model_object.name)),
                self.canonical_type(value_type, self.object_module(model_object.name)),
            )
        self.tuples: set[DerivationTuple] = set()
        self.query_tuples: set[DerivationTuple] = self.tuples

    def canonical_type(
        self, expression: ModelTypeExpression, module: tuple[str, ...]
    ) -> tuple[str, ...]:
        if expression.name == ("String",):
            return ("String",)
        resolved = self._type_name(expression.name, module)
        if resolved is None:
            raise RuntimeError(
                f"compiled type {'::'.join(expression.name)!r} is unresolved"
            )
        return resolved

    def object_module(self, name: tuple[str, ...]) -> tuple[str, ...]:
        # Inference-owned runtime children are created after Model IR loading
        # and intentionally have no declaring module entry. Preserve their
        # historical relative scope while using the recorded module for every
        # declared (including type-expanded nested) object.
        return self.object_modules.get(name, name[:-1])

    def _type_name(
        self, raw: tuple[str, ...], module: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        if raw in self.types:
            return raw
        if module + raw in self.types:
            return module + raw
        matches = tuple(name for name in self.types if name[-len(raw) :] == raw)
        return matches[0] if len(matches) == 1 else None

    def object_has_type(self, name: tuple[str, ...], suffix: str) -> bool:
        model_object = self.objects[name]
        current = self._type_name(
            model_object.base_type.name, self.object_module(name)
        )
        seen: set[tuple[str, ...]] = set()
        while current is not None and current not in seen:
            seen.add(current)
            if current[-1] == suffix:
                return True
            base = self.types[current].base_type
            current = (
                None
                if base is None
                else self._type_name(base.name, current[:-1])
            )
        return False

    def object_type_flag(self, name: tuple[str, ...], flag: str) -> bool:
        model_object = self.objects[name]
        current = self._type_name(
            model_object.base_type.name, self.object_module(name)
        )
        seen: set[tuple[str, ...]] = set()
        while current is not None and current not in seen:
            seen.add(current)
            if bool(getattr(self.types[current], flag)):
                return True
            base = self.types[current].base_type
            current = (
                None if base is None else self._type_name(base.name, current[:-1])
            )
        return False

    @staticmethod
    def object_expression(name: tuple[str, ...]) -> ModelExpression:
        result = ModelExpression("identifier", name[0])
        for part in name[1:]:
            result = ModelExpression("path", part, (result,))
        return result

    def _resolve(
        self,
        raw: tuple[str, ...],
        candidates: dict[tuple[str, ...], object],
        module: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        if raw and raw[0] == "model":
            raw = raw[1:]
        elif raw and raw[0] == "self":
            raw = module + raw[1:]
        elif raw and raw[0] == "super":
            count = 0
            while count < len(raw) and raw[count] == "super":
                count += 1
            raw = module[: len(module) - count] + raw[count:]
        if raw in candidates:
            return raw
        local = module + raw
        if local in candidates:
            return local
        matches = tuple(name for name in candidates if name[-len(raw) :] == raw)
        return matches[0] if len(matches) == 1 else None

    def object_reference(
        self, expression: ModelExpression, module: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        flattened = _access(expression)
        if flattened is None:
            return None
        raw, operations = flattened
        if raw[-1:] == ("state",) and operations[-1:] == ("member",):
            raw, operations = raw[:-1], operations[:-1]
        if not raw or any(operation == "member" for operation in operations):
            return None
        return self._resolve(raw, self.objects, module)

    def resolve_value(
        self,
        expression: ModelExpression,
        module: tuple[str, ...],
        source: tuple[str, ...] | None,
        bindings: dict[str, DerivationTerm | ModelExpression],
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]],
    ) -> tuple[str, ...] | None:
        if expression.kind == "identifier":
            identifier = str(expression.value)
            if identifier in bindings:
                bound = bindings[identifier]
                return (
                    bound.value
                    if isinstance(bound, DerivationTerm)
                    and bound.kind == "object"
                    and isinstance(bound.value, tuple)
                    else None
                )
            if identifier == "self":
                return source
            if identifier == "CurrentTaskRef":
                if self.current_task_resolver is None:
                    raise _UnsupportedExpression("CurrentTaskRef:unavailable")
                return self.current_task_resolver()
            if identifier == "CurrentCPU":
                if self.current_cpu_resolver is None:
                    raise _UnsupportedExpression("CurrentCPU:unavailable")
                return self.current_cpu_resolver()
        flattened = _access(expression)
        if (
            flattened is not None
            and len(flattened[0]) == 2
            and flattened[0][1] == "state"
            and flattened[1] == ("member",)
            and flattened[0][0] in bindings
        ):
            bound = bindings[flattened[0][0]]
            if (
                isinstance(bound, DerivationTerm)
                and bound.kind == "object"
                and isinstance(bound.value, tuple)
            ):
                return bound.value
        if flattened == (("self", "TaskFlowRef"), ("member",)):
            if source is None or self.task_flow_resolver is None:
                raise _UnsupportedExpression("self.TaskFlowRef:unavailable")
            return self.task_flow_resolver(source)
        if flattened == (("self", "ResumeTargetRef"), ("member",)):
            if source is None or self.resume_target_resolver is None:
                raise _UnsupportedExpression("self.ResumeTargetRef:unavailable")
            return self.resume_target_resolver(source)
        if flattened == (("self", "InterruptFlowRef"), ("member",)):
            if source is None or self.interrupt_flow_resolver is None:
                raise _UnsupportedExpression("self.InterruptFlowRef:unavailable")
            return self.interrupt_flow_resolver(source)
        if flattened == (("self", "ExceptionFlowRef"), ("member",)):
            if source is None or self.exception_flow_resolver is None:
                raise _UnsupportedExpression("self.ExceptionFlowRef:unavailable")
            return self.exception_flow_resolver(source)
        if flattened == (("self", "SyscallExitFlowRef"), ("member",)):
            if source is None or self.syscall_exit_flow_resolver is None:
                raise _UnsupportedExpression("self.SyscallExitFlowRef:unavailable")
            return self.syscall_exit_flow_resolver(source)
        if flattened == (
            ("CurrentCPU", "InterruptControlRef"),
            ("member",),
        ):
            if self.interrupt_control_resolver is None:
                raise _UnsupportedExpression(
                    "CurrentCPU.InterruptControlRef:unavailable"
                )
            return self.interrupt_control_resolver()
        if flattened == (
            ("CurrentTaskRef", "UserAppRuntimeRef"),
            ("member",),
        ):
            if self.user_runtime_resolver is None:
                raise _UnsupportedExpression(
                    "CurrentTaskRef.UserAppRuntimeRef:unavailable"
                )
            return self.user_runtime_resolver()
        if (
            flattened is not None
            and source is not None
            and flattened[0][:1] == ("self",)
            and flattened[1] == ("member",)
            and len(flattened[0]) == 2
        ):
            return values.get((source, flattened[0][1]))
        if (
            flattened is not None
            and flattened[1][-1:] == ("member",)
            and all(operation == "path" for operation in flattened[1][:-1])
            and len(flattened[0]) >= 2
            and flattened[0][-1] != "state"
        ):
            owner_expression = self.object_expression(flattened[0][:-1])
            owner = self.object_reference(owner_expression, module)
            if owner is not None:
                return values.get((owner, flattened[0][-1]))
        return self.object_reference(expression, module)

    def instantiate_signal(
        self,
        signal: ModelSignal,
        module: tuple[str, ...],
        source: tuple[str, ...] | None,
        bindings: dict[str, DerivationTerm | ModelExpression],
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]],
    ) -> DerivationEvent:
        arguments: list[ModelExpression] = []
        for argument in signal.arguments:
            if argument.kind == "identifier" and str(argument.value) in bindings:
                bound = bindings[str(argument.value)]
                if isinstance(bound, ModelExpression):
                    arguments.append(bound)
                elif bound.kind == "string":
                    arguments.append(ModelExpression("string", str(bound.value)))
                else:
                    assert isinstance(bound.value, tuple)
                    arguments.append(self.object_expression(bound.value))
                continue
            if argument.kind == "integer" or (
                argument.kind == "unary"
                and argument.value == "-"
                and argument.children[0].kind == "integer"
            ):
                arguments.append(argument)
                continue
            value = self.resolve_value(argument, module, source, bindings, values)
            if value is None:
                raise _UnsupportedExpression(
                    f"signal_argument:{_format_expression(argument)}"
                )
            arguments.append(self.object_expression(value))
        target = self.resolve_value(signal.target, module, source, bindings, values)
        if target is None:
            raise _UnsupportedExpression(
                f"signal_target:{_format_expression(signal.target)}"
            )
        return DerivationEvent(
            signal.source if source is None else source,
            target,
            signal.signal,
            signal.mode,
            tuple(arguments),
        )

    @staticmethod
    def state_reference(expression: ModelExpression) -> tuple[str, ...] | None:
        flattened = _access(expression)
        if flattened is None:
            return None
        raw, operations = flattened
        if len(raw) == 2 and raw[0] == "State" and operations == ("path",):
            return raw
        return None

    def normalize_term(
        self,
        expression: ModelExpression,
        module: tuple[str, ...],
        source: tuple[str, ...] | None = None,
        bindings: dict[str, DerivationTerm | ModelExpression] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> str:
        bindings = {} if bindings is None else bindings
        values = {} if values is None else values
        if expression.kind == "integer":
            return str(expression.value)
        if expression.kind == "string":
            return json.dumps(expression.value, ensure_ascii=False)
        if expression.kind == "identifier" and str(expression.value) in bindings:
            bound = bindings[str(expression.value)]
            if isinstance(bound, DerivationTerm):
                if bound.kind == "string":
                    return json.dumps(bound.value, ensure_ascii=False)
                assert isinstance(bound.value, tuple)
                return self.object_names[bound.value]
        if expression.kind == "identifier" and expression.value in {"true", "false"}:
            return str(expression.value)
        state = self.state_reference(expression)
        if state is not None:
            return "::".join(state)
        model_object = self.resolve_value(
            expression, module, source, bindings, values
        )
        if model_object is not None:
            return self.object_names[model_object]
        flattened = _access(expression)
        if flattened is not None and all(op == "path" for op in flattened[1]):
            return "::".join(flattened[0])
        if expression.kind == "identifier":
            return str(expression.value)
        raise _UnsupportedExpression(f"fact_argument:{expression.kind}")

    def normalize_fact(
        self,
        expression: ModelExpression,
        module: tuple[str, ...],
        source: tuple[str, ...] | None = None,
        bindings: dict[str, DerivationTerm | ModelExpression] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> DerivationFact:
        if expression.kind != "call":
            raise _UnsupportedExpression("establishes:non_predicate")
        flattened = _access(expression.children[0])
        if flattened is None or any(op == "member" for op in flattened[1]):
            raise _UnsupportedExpression("predicate_callee")
        predicate_name = self._resolve(flattened[0], self.predicates, module)
        if predicate_name is None:
            raise _UnsupportedExpression("predicate_resolution")
        predicate = self.predicates[predicate_name]
        arguments = tuple(
            self.normalize_term(argument, module, source, bindings, values)
            for argument in expression.children[1:]
        )
        if len(arguments) != len(predicate.parameters):
            raise _UnsupportedExpression("predicate_arity")
        return DerivationFact(predicate_name, arguments)

    def fact_text(self, fact: DerivationFact) -> str:
        name = self.predicate_names[fact.predicate]
        return f"{name}({', '.join(fact.arguments)})"

    def expression_text(
        self,
        expression: ModelExpression,
        module: tuple[str, ...],
        source: tuple[str, ...] | None = None,
        bindings: dict[str, DerivationTerm | ModelExpression] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> str:
        bindings = {} if bindings is None else bindings
        values = {} if values is None else values
        relation = self.relation_call(expression, module)
        if relation is not None:
            owner, method, arguments = relation
            rendered: list[str] = []
            _, key_type, value_type = self.containers[owner]
            for index, argument in enumerate(arguments):
                expected = key_type if index == 0 else value_type
                try:
                    rendered.append(
                        self.term_text(
                            self.typed_term(
                                argument,
                                expected,
                                module,
                                source,
                                bindings,
                                values,
                            )
                        )
                    )
                except _UnsupportedExpression:
                    rendered.append(_format_expression(argument))
            return (
                f"{self.object_names[owner]}.{method}({', '.join(rendered)})"
            )
        if expression.kind == "call":
            try:
                return self.fact_text(
                    self.normalize_fact(expression, module, source, bindings, values)
                )
            except _UnsupportedExpression:
                pass
        if expression.kind == "binary" and expression.value in {"==", "!="}:
            left, right = expression.children
            left_object = self.resolve_value(left, module, source, bindings, values)
            right_object = self.resolve_value(right, module, source, bindings, values)
            left_state = self.state_reference(left)
            right_state = self.state_reference(right)
            if left_object is not None and right_state is not None:
                return (
                    f"{self.object_names[left_object]} {expression.value} "
                    f"{'::'.join(right_state)}"
                )
            if right_object is not None and left_state is not None:
                return (
                    f"{'::'.join(left_state)} {expression.value} "
                    f"{self.object_names[right_object]}"
                )
        if expression.kind == "binary" and expression.value in {"&&", "||"}:
            return (
                f"({self.expression_text(expression.children[0], module, source, bindings, values)} "
                f"{expression.value} "
                f"{self.expression_text(expression.children[1], module, source, bindings, values)})"
            )
        if expression.kind == "unary" and expression.value == "!":
            return f"!{self.expression_text(expression.children[0], module, source, bindings, values)}"
        return _format_expression(expression)

    def evaluate(
        self,
        expression: ModelExpression,
        module: tuple[str, ...],
        states: dict[tuple[str, ...], tuple[str, ...] | None],
        facts: set[DerivationFact],
        source: tuple[str, ...] | None = None,
        bindings: dict[str, DerivationTerm | ModelExpression] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> bool:
        bindings = {} if bindings is None else bindings
        values = {} if values is None else values
        if expression.kind == "identifier" and expression.value in {"true", "false"}:
            return expression.value == "true"
        if expression.kind == "call":
            relation = self.relation_call(expression, module)
            if relation is not None:
                owner, method, arguments = relation
                container, key_type, value_type = self.containers[owner]
                key = self.typed_term(
                    arguments[0], key_type, module, source, bindings, values
                )
                matches = tuple(
                    item
                    for item in self.query_tuples
                    if item.owner == owner and item.key == key
                )
                if method == "has_key":
                    return bool(matches)
                if method == "contains":
                    value = self.typed_term(
                        arguments[1], value_type, module, source, bindings, values
                    )
                    return any(item.value == value for item in matches)
                raise _UnsupportedExpression(
                    f"{container}.{method}:witness_only"
                )
            return self.normalize_fact(
                expression, module, source, bindings, values
            ) in facts
        if expression.kind == "unary" and expression.value == "!":
            return not self.evaluate(
                expression.children[0], module, states, facts, source, bindings, values
            )
        if expression.kind == "binary" and expression.value in {"&&", "||"}:
            left = self.evaluate(
                expression.children[0], module, states, facts, source, bindings, values
            )
            right = self.evaluate(
                expression.children[1], module, states, facts, source, bindings, values
            )
            return left and right if expression.value == "&&" else left or right
        if expression.kind == "binary" and expression.value in {"==", "!="}:
            left, right = expression.children
            left_object = self.resolve_value(left, module, source, bindings, values)
            right_object = self.resolve_value(right, module, source, bindings, values)
            left_state = self.state_reference(left)
            right_state = self.state_reference(right)
            if left_object is not None and right_state is not None:
                equal = states[left_object] == right_state
                return equal if expression.value == "==" else not equal
            if right_object is not None and left_state is not None:
                equal = states[right_object] == left_state
                return equal if expression.value == "==" else not equal
            if left_object is not None and right_object is not None:
                equal = left_object == right_object
                return equal if expression.value == "==" else not equal
        feature = f"expression:{expression.kind}"
        if expression.kind in {"unary", "binary"}:
            feature += f":{expression.value}"
        raise _UnsupportedExpression(feature)

    def relation_call(
        self, expression: ModelExpression, module: tuple[str, ...]
    ) -> tuple[tuple[str, ...], str, tuple[ModelExpression, ...]] | None:
        if expression.kind != "call" or expression.children[0].kind != "member":
            return None
        callee = expression.children[0]
        owner = self.object_reference(callee.children[0], module)
        if owner not in self.containers:
            return None
        return owner, str(callee.value), expression.children[1:]

    def typed_term(
        self,
        expression: ModelExpression,
        expected_type: tuple[str, ...],
        module: tuple[str, ...],
        source: tuple[str, ...] | None,
        bindings: dict[str, DerivationTerm | ModelExpression],
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]],
    ) -> DerivationTerm:
        if expression.kind == "identifier" and str(expression.value) in bindings:
            bound = bindings[str(expression.value)]
            if isinstance(bound, DerivationTerm):
                if bound.type != expected_type:
                    # The compiler already proved assignment compatibility.  A
                    # witness is retagged with the receiving static type so tuple
                    # equality remains declared-container typed.
                    return DerivationTerm(bound.kind, expected_type, bound.value)
                return bound
        if expected_type == ("String",):
            if expression.kind != "string":
                raise _UnsupportedExpression(
                    f"String_term:{_format_expression(expression)}"
                )
            return DerivationTerm("string", expected_type, str(expression.value))
        value = self.resolve_value(expression, module, source, bindings, values)
        if value is None:
            raise _UnsupportedExpression(
                f"object_term:{_format_expression(expression)}"
            )
        return DerivationTerm("object", expected_type, value)

    def term_text(self, term: DerivationTerm) -> str:
        if term.kind == "string":
            return json.dumps(term.value, ensure_ascii=False)
        assert isinstance(term.value, tuple)
        return self.object_names[term.value]


class _Execution:
    def __init__(
        self,
        model: ModelIR,
        switch_choices: tuple[tuple[str, ...], ...] = (),
        user_runtime_signals: UserRuntimeSignalProgram | None = None,
    ) -> None:
        self.context = _Context(model)
        self.user_runtime_signal_program = (
            default_user_runtime_signals()
            if user_runtime_signals is None
            else user_runtime_signals
        )
        self.user_runtime_signal_cursors: dict[tuple[str, ...], int] = {}
        self.event_flows: list[DerivationEventFlow] = []
        self.states = _states(model)
        self.facts: set[DerivationFact] = set()
        self.tuples: set[DerivationTuple] = set()
        self.context.tuples = self.tuples
        self.context.query_tuples = self.tuples
        self.failure: DerivationFailure | None = None
        self.continuations: dict[tuple[str, ...], _ContinuationRuntime] = {}
        self.values: dict[
            tuple[tuple[str, ...], str], tuple[str, ...]
        ] = {}
        self.collections: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
        self.switch_choices = switch_choices
        self.switch_cursor = 0
        self.cycle_closed = False
        self.seen_snapshots: set[tuple[object, ...]] = set()
        self.schedulers: dict[tuple[str, ...], _SchedulerRuntime] = {}
        for model_object in model.objects:
            if model_object.idle_task is None:
                continue
            idle = self.context.object_reference(
                model_object.idle_task,
                self.context.object_module(model_object.name),
            )
            if idle is None:
                raise RuntimeError("compiled scheduler idle_task is unresolved")
            self.schedulers[model_object.name] = _SchedulerRuntime(
                model_object.name, idle
            )
        self.context.schedulers = self.schedulers
        self.current_task_ref: tuple[str, ...] | None = None
        self.current_cpu_ref: tuple[str, ...] | None = None
        self.context.current_task_resolver = self._current_task
        for model_object in model.objects:
            if model_object.base_type.name == ("Collection",):
                self.collections[model_object.name] = []
        for model_object in model.objects:
            for model_field in model_object.attrs or ():
                if model_field.default is None:
                    continue
                value = self.context.resolve_value(
                    model_field.default,
                    self.context.object_module(model_object.name),
                    model_object.name,
                    {},
                    self.values,
                )
                if value is None:
                    raise RuntimeError(
                        f"compiled field default {model_field.name!r} is unresolved"
                    )
                self.values[(model_object.name, model_field.name)] = value
        self.user_runtimes: dict[tuple[str, ...], tuple[str, ...]] = {}
        self.user_runtime_owners: dict[tuple[str, ...], tuple[str, ...]] = {}
        self.parked_user_tasks: dict[
            tuple[str, ...], tuple[str, ...]
        ] = {}
        self.parked_user_continuations: dict[
            tuple[str, ...], tuple[str, ...]
        ] = {}
        self.context.user_runtime_resolver = self._current_user_runtime
        self.task_flows: dict[tuple[str, ...], tuple[str, ...]] = {}
        for model_object in model.objects:
            if (
                not model_object.continuation
                or model_object.parent is None
                or not self.context.object_has_type(
                    model_object.name, "TaskFlow"
                )
            ):
                continue
            parent = self.context.object_reference(
                model_object.parent,
                self.context.object_module(model_object.name),
            )
            if parent is not None and parent in self.context.objects:
                parent_object = self.context.objects[parent]
                if self.context.object_has_type(parent_object.name, "Task"):
                    self.task_flows[parent] = model_object.name
        self.context.task_flow_resolver = self._task_flow
        self.context.resume_target_resolver = self._task_resume_target
        self.cpus: dict[tuple[str, ...], _CpuRuntime] = {}
        self.cpus_by_logical_id: dict[int, _CpuRuntime] = {}
        for model_object in model.objects:
            if not self.context.object_type_flag(model_object.name, "cpu_core"):
                continue
            if model_object.logical_id is None:
                raise RuntimeError("compiled cpu_core object has no logical_id")
            cpu = _CpuRuntime(model_object.name, model_object.logical_id)
            self.cpus[model_object.name] = cpu
            self.cpus_by_logical_id[model_object.logical_id] = cpu
        for scheduler_name in self.schedulers:
            scheduler_object = self.context.objects[scheduler_name]
            if scheduler_object.parent is None:
                continue
            parent = self.context.object_reference(
                scheduler_object.parent,
                self.context.object_module(scheduler_name),
            )
            if parent in self.cpus:
                self.cpus[parent].scheduler = scheduler_name
                if len(self.schedulers) == 1:
                    self.current_cpu_ref = parent
        self.interrupt_controls = {
            cpu_name + ("InterruptControl",): cpu_name
            for cpu_name in self.cpus
        }
        self.context.current_cpu_resolver = self._current_cpu
        self.context.interrupt_control_resolver = self._interrupt_control
        self.context.interrupt_flow_resolver = self._interrupt_flow
        self.context.exception_flow_resolver = self._exception_flow
        self.context.syscall_exit_flow_resolver = self._syscall_exit_flow
        self.active_event_flow: tuple[str, ...] | None = None
        self.allow_event_cpu_entry = False

    def _current_user_runtime(self) -> tuple[str, ...]:
        if self.current_task_ref is None or self.context.user_runtime_type is None:
            raise _UnsupportedExpression(
                "CurrentTaskRef.UserAppRuntimeRef:ambiguous_runtime_context"
            )
        task = self.current_task_ref
        existing = self.user_runtimes.get(task)
        if existing is not None:
            return existing
        runtime_type = self.context.user_runtime_type
        name = task + (runtime_type.name[-1],)
        if name in self.context.objects:
            raise _UnsupportedExpression(
                "CurrentTaskRef.UserAppRuntimeRef:runtime_identity_collision"
            )
        model_object = ModelObject(
            name=name,
            base_type=ModelTypeExpression(runtime_type.name),
            initial_state=runtime_type.initial_state,
            parent=self.context.object_expression(task),
            source=None,
            attrs=runtime_type.fields,
            states=runtime_type.states,
            references=(),
        )
        self.context.objects[name] = model_object
        self.states[name] = runtime_type.initial_state
        self.user_runtimes[task] = name
        self.user_runtime_owners[name] = task
        return name

    def _deliver_returning_event(
        self,
        cpu_name: tuple[str, ...],
        signal: UserRuntimeSignal,
        owner: tuple[str, ...],
        task_flow: tuple[str, ...],
        user_runtime: tuple[str, ...],
        path: str,
    ) -> DerivationUnit:
        flow = self._materialize_event_flow(cpu_name, signal.family)
        cpu = self.cpus[cpu_name]
        if signal.family == "interrupt":
            cpu.active_interrupt_flow = flow
            cpu_signal = ("Action", "OnInterrupt")
        else:
            cpu.active_exception_flow = flow
            cpu_signal = ("Action", "OnException")
        self.active_event_flow = flow
        self.allow_event_cpu_entry = True
        delivered = self.run_unit(
            DerivationEvent(
                user_runtime,
                cpu_name,
                cpu_signal,
                "drive",
            ),
            "drive",
            path,
        )
        self.allow_event_cpu_entry = False
        self.active_event_flow = None
        cpu.active_interrupt_flow = None
        cpu.active_exception_flow = None
        if self.failure is None:
            self.event_flows.append(
                DerivationEventFlow(
                    flow,
                    cpu_name,
                    task_flow,
                    user_runtime,
                    signal.qualified_name,
                    "returned",
                )
            )
        return delivered

    def _run_user_runtime(
        self, event: DerivationEvent, kind: str, path: str
    ) -> DerivationUnit:
        before = self.states[event.target]
        if (
            event.signal != ("Action", "Enter")
            or event.arguments
            or before != ("State", "Online")
        ):
            failure = self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                "user_runtime only handles parameterless Action::Enter in State::Online",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=None,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )

        owner = self.user_runtime_owners[event.target]
        if self.current_task_ref != owner or len(self.schedulers) != 1:
            failure = self._set_failure(
                "invalid_current_task_ref",
                f"{path}.handler",
                "user_runtime owner is not current on this CPU derivation line",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )

        continuation = self.continuations.get(event.source)
        first_entry = owner not in self.parked_user_tasks
        if not first_entry:
            if event.source != owner or event.mode != "resume":
                failure = self._set_failure(
                    "invalid_user_runtime_entry",
                    f"{path}.handler",
                    "an active user_runtime episode can only be resumed by its owner Task",
                )
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=event.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )
            return self._resume_ack(event, kind)
        if first_entry and (
            event.mode != "yield"
            or continuation is None
            or not continuation.owner_active
            or not continuation.suspended
            or not continuation.waiting_yield_target
        ):
            failure = self._set_failure(
                "invalid_user_runtime_entry",
                f"{path}.handler",
                "user_runtime Action::Enter must be reached through a continuation yield",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )

        if first_entry:
            assert continuation is not None
            self.parked_user_tasks[owner] = event.target
            self.parked_user_continuations[owner] = continuation.root

        cursor = self.user_runtime_signal_cursors.get(event.target, 0)
        signals = self.user_runtime_signal_program.signals
        if cursor >= len(signals):
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=before,
                emits=[],
                status="passed",
                failure=None,
            )

        signal = signals[cursor]
        self.user_runtime_signal_cursors[event.target] = cursor + 1

        def runtime_failure(code: str, message: str) -> DerivationUnit:
            failure = self._set_failure(
                code,
                f"{path}.runtime_signals[{cursor}]",
                (
                    f"{signal.source}:{signal.line}:{signal.column}: "
                    f"{message}"
                ),
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )

        try:
            local_cpu_name = self._current_cpu()
        except _UnsupportedExpression:
            return runtime_failure(
                "invalid_current_cpu_ref",
                "UserAppRuntime TaskFlow has no valid cpu_ref",
            )
        local_cpu = self.cpus[local_cpu_name]
        target_cpu = (
            local_cpu
            if signal.local
            else self.cpus_by_logical_id.get(signal.cpu_target)
        )
        if target_cpu is None:
            return runtime_failure(
                "unknown_cpu_target",
                f"logical CPU {signal.cpu_target} does not exist",
            )
        if signal.family == "syscall" and target_cpu.cpu != local_cpu.cpu:
            return runtime_failure(
                "invalid_syscall_cpu_target",
                "syscall signals must target the Runtime's local CPU",
            )
        if signal.family == "exception" and target_cpu.cpu != local_cpu.cpu:
            return runtime_failure(
                "invalid_exception_cpu_target",
                "exception signals must target the Runtime's local CPU",
            )
        if signal.family == "interrupt":
            if target_cpu.interrupt_mode == "Unknown":
                return runtime_failure(
                    "unknown_interrupt_mode",
                    "interrupt delivery requires MaskAll or Unmask to initialize the target CPU control gate",
                )
            if target_cpu.interrupt_mode == "Masked":
                target_cpu.pending_interrupts.append(
                    _PendingInterrupt(
                        signal,
                        owner,
                        self._task_flow(owner),
                        event.target,
                    )
                )
                next_cursor = self.user_runtime_signal_cursors[event.target]
                while next_cursor < len(signals):
                    next_signal = signals[next_cursor]
                    if next_signal.family != "interrupt":
                        break
                    next_target = (
                        local_cpu
                        if next_signal.local
                        else self.cpus_by_logical_id.get(next_signal.cpu_target)
                    )
                    if (
                        next_target is None
                        or next_target.interrupt_mode != "Masked"
                    ):
                        break
                    next_target.pending_interrupts.append(
                        _PendingInterrupt(
                            next_signal,
                            owner,
                            self._task_flow(owner),
                            event.target,
                        )
                    )
                    next_cursor += 1
                    self.user_runtime_signal_cursors[event.target] = next_cursor
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=event.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=before,
                    emits=[],
                    status="passed",
                    failure=None,
                )
            delivered = self._deliver_returning_event(
                target_cpu.cpu,
                signal,
                owner,
                self._task_flow(owner),
                event.target,
                f"{path}.drives[0]",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[delivered],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=before if self.failure is None else None,
                emits=[],
                status="passed" if self.failure is None else "stopped",
                failure=None,
            )
        if signal.family == "exception":
            delivered = self._deliver_returning_event(
                target_cpu.cpu,
                signal,
                owner,
                self._task_flow(owner),
                event.target,
                f"{path}.drives[0]",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[delivered],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=before if self.failure is None else None,
                emits=[],
                status="passed" if self.failure is None else "stopped",
                failure=None,
            )
        if (signal.family, signal.name) != ("syscall", "exit"):
            return runtime_failure(
                "unsupported_runtime_signal",
                f"runtime signal {signal.qualified_name} is not implemented",
            )

        try:
            flow = self._materialize_syscall_exit_flow(target_cpu.cpu)
        except _UnsupportedExpression:
            return runtime_failure(
                "unsupported_runtime_signal",
                "the model has no syscall_exit_flow protocol type",
            )
        target_cpu.active_syscall_exit_flow = flow
        self.active_event_flow = flow
        self.allow_event_cpu_entry = True
        status = self._integer_expression(signal.arguments[0])
        delivered = self.run_unit(
            DerivationEvent(
                event.target,
                target_cpu.cpu,
                ("Action", "OnSyscallExit"),
                "drive",
                (status,),
            ),
            "drive",
            f"{path}.drives[0]",
        )
        self.allow_event_cpu_entry = False
        self.active_event_flow = None
        target_cpu.active_syscall_exit_flow = None
        unexpected_return: DerivationFailure | None = None
        if self.failure is None:
            unexpected_return = self._set_failure(
                "unimplemented_task_exit",
                f"{path}.drives[0]",
                "Task.Action::Exit returned from a terminal syscall.exit flow",
            )
        self.event_flows.append(
            DerivationEventFlow(
                flow,
                target_cpu.cpu,
                self._task_flow(owner),
                event.target,
                signal.qualified_name,
                "terminal",
            )
        )
        if self.failure is not None:
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[delivered],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=(
                    unexpected_return.code
                    if unexpected_return is not None
                    else "stopped"
                ),
                failure=unexpected_return,
            )
        return self._unit(
            kind=kind,
            event=event,
            before=before,
            handler=event.signal,
            candidate=None,
            depends_on=[],
            drives=[delivered],
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=before,
            emits=[],
            status="passed",
            failure=None,
        )

    def final_values(self) -> tuple[DerivationValue, ...]:
        fields = tuple(
            DerivationValue(owner, name, (value,), False)
            for (owner, name), value in sorted(self.values.items())
        )
        collections = tuple(
            DerivationValue(owner, None, tuple(values), True)
            for owner, values in sorted(self.collections.items())
        )
        return fields + collections

    def tuple_snapshots(self) -> tuple[DerivationTuple, ...]:
        return tuple(self.tuples)

    def scheduler_snapshots(self) -> tuple[DerivationScheduler, ...]:
        return tuple(
            DerivationScheduler(
                runtime.scheduler,
                runtime.idle_task,
                tuple(runtime.runq),
            )
            for runtime in self.schedulers.values()
        )

    def interrupt_control_snapshots(
        self,
    ) -> tuple[DerivationInterruptControl, ...]:
        return tuple(
            DerivationInterruptControl(
                runtime.cpu,
                runtime.interrupt_mode,
                tuple(
                    item.signal.qualified_name
                    for item in runtime.pending_interrupts
                ),
            )
            for runtime in self.cpus.values()
        )

    def _current_task(self) -> tuple[str, ...]:
        if self.current_task_ref is None:
            raise _UnsupportedExpression("CurrentTaskRef:unavailable")
        return self.current_task_ref

    def _current_cpu(self) -> tuple[str, ...]:
        if self.current_cpu_ref is None or self.current_cpu_ref not in self.cpus:
            raise _UnsupportedExpression("CurrentCPU:invalid_current_cpu_ref")
        return self.current_cpu_ref

    def _interrupt_control(self) -> tuple[str, ...]:
        return self._current_cpu() + ("InterruptControl",)

    def _interrupt_flow(self, cpu: tuple[str, ...]) -> tuple[str, ...]:
        runtime = self.cpus.get(cpu)
        if runtime is None or runtime.active_interrupt_flow is None:
            raise _UnsupportedExpression("self.InterruptFlowRef:unavailable")
        return runtime.active_interrupt_flow

    def _exception_flow(self, cpu: tuple[str, ...]) -> tuple[str, ...]:
        runtime = self.cpus.get(cpu)
        if runtime is None or runtime.active_exception_flow is None:
            raise _UnsupportedExpression("self.ExceptionFlowRef:unavailable")
        return runtime.active_exception_flow

    def _syscall_exit_flow(self, cpu: tuple[str, ...]) -> tuple[str, ...]:
        runtime = self.cpus.get(cpu)
        if runtime is None or runtime.active_syscall_exit_flow is None:
            raise _UnsupportedExpression("self.SyscallExitFlowRef:unavailable")
        return runtime.active_syscall_exit_flow

    @staticmethod
    def _integer_expression(value: int) -> ModelExpression:
        if value >= 0:
            return ModelExpression("integer", value)
        return ModelExpression(
            "unary", "-", (ModelExpression("integer", -value),)
        )

    def _materialize_syscall_exit_flow(
        self, cpu: tuple[str, ...]
    ) -> tuple[str, ...]:
        runtime = self.cpus[cpu]
        flow_type = self.context.syscall_exit_flow_type
        if flow_type is None:
            raise _UnsupportedExpression("syscall_exit_flow:type_unavailable")
        name = cpu + (f"SyscallExitFlow{runtime.next_syscall_exit_flow}",)
        runtime.next_syscall_exit_flow += 1
        model_object = ModelObject(
            name=name,
            base_type=ModelTypeExpression(flow_type.name),
            initial_state=flow_type.initial_state,
            parent=self.context.object_expression(cpu),
            source=None,
            attrs=flow_type.fields,
            states=flow_type.states,
            references=(),
            continuation=True,
        )
        self.context.objects[name] = model_object
        self.context.object_names = _shortest_names(tuple(self.context.objects))
        self.states[name] = flow_type.initial_state
        return name

    def _materialize_event_flow(
        self, cpu: tuple[str, ...], family: str
    ) -> tuple[str, ...]:
        runtime = self.cpus[cpu]
        if family == "interrupt":
            flow_type = self.context.event_flow_types.get("InterruptFlow")
            number = runtime.next_interrupt_flow
            runtime.next_interrupt_flow += 1
            prefix = "InterruptFlow"
        elif family == "exception":
            flow_type = self.context.event_flow_types.get("ExceptionFlow")
            number = runtime.next_exception_flow
            runtime.next_exception_flow += 1
            prefix = "ExceptionFlow"
        else:
            raise _UnsupportedExpression(f"event_flow:{family}:unsupported")
        if flow_type is None:
            raise _UnsupportedExpression(f"{family}_flow:type_unavailable")
        name = cpu + (f"{prefix}{number}",)
        model_object = ModelObject(
            name=name,
            base_type=ModelTypeExpression(flow_type.name),
            initial_state=flow_type.initial_state,
            parent=self.context.object_expression(cpu),
            source=None,
            attrs=flow_type.fields,
            states=flow_type.states,
            references=(),
            continuation=True,
        )
        self.context.objects[name] = model_object
        self.context.object_names = _shortest_names(tuple(self.context.objects))
        self.states[name] = flow_type.initial_state
        return name

    def _bind_task_flow_cpu(
        self, task: tuple[str, ...], cpu: tuple[str, ...]
    ) -> None:
        flow = self._task_flow(task)
        self.values[(flow, "cpu_ref")] = cpu

    def _task_resume_target(self, task: tuple[str, ...]) -> tuple[str, ...]:
        parked = self.parked_user_tasks.get(task)
        if parked is not None:
            return parked
        return self._task_flow(task)

    def _task_flow(self, task: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return self.task_flows[task]
        except KeyError as exc:
            raise _UnsupportedExpression("self.TaskFlowRef:unavailable") from exc

    def _runtime_snapshot(self, position: tuple[object, ...]) -> tuple[object, ...]:
        continuations = tuple(
            (
                root,
                runtime.executing,
                runtime.suspended,
                runtime.completed,
                runtime.waiting_yield_target,
                runtime.resume_requested,
                tuple(
                    (
                        frame.object,
                        frame.handler,
                        frame.control_index,
                        frame.entered,
                        tuple(sorted(frame.bindings.items())),
                    )
                    for frame in runtime.frames
                ),
            )
            for root, runtime in sorted(self.continuations.items())
        )
        return (
            position,
            tuple(sorted(self.states.items())),
            tuple(sorted(self.facts, key=lambda item: (item.predicate, item.arguments))),
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
            tuple(sorted(self.values.items())),
            tuple((name, tuple(values)) for name, values in sorted(self.collections.items())),
            tuple(
                (
                    name,
                    runtime.idle_task,
                    tuple(runtime.runq),
                )
                for name, runtime in sorted(self.schedulers.items())
            ),
            self.current_task_ref,
            self.current_cpu_ref,
            tuple(sorted(self.parked_user_tasks.items())),
            tuple(sorted(self.parked_user_continuations.items())),
            tuple(sorted(self.user_runtime_signal_cursors.items())),
            tuple(
                (
                    name,
                    runtime.next_syscall_exit_flow,
                    runtime.active_syscall_exit_flow,
                    runtime.next_interrupt_flow,
                    runtime.active_interrupt_flow,
                    runtime.next_exception_flow,
                    runtime.active_exception_flow,
                    runtime.interrupt_mode,
                    tuple(runtime.pending_interrupts),
                )
                for name, runtime in sorted(self.cpus.items())
            ),
            tuple(self.event_flows),
            continuations,
        )

    def _bind_handler(
        self,
        event: DerivationEvent,
        handler: object,
    ) -> dict[str, DerivationTerm | ModelExpression]:
        parameters = handler.parameters
        if len(parameters) != len(event.arguments):
            return {}
        result: dict[str, DerivationTerm | ModelExpression] = {}
        for parameter, argument in zip(parameters, event.arguments, strict=True):
            if argument.kind == "string" and parameter.type.name == ("String",):
                result[parameter.name] = DerivationTerm(
                    "string", ("String",), str(argument.value)
                )
                continue
            value = self.context.resolve_value(
                argument,
                event.target[:-1],
                event.target,
                {},
                self.values,
            )
            if value is None:
                if argument.kind == "integer" or (
                    argument.kind == "unary"
                    and argument.value == "-"
                    and argument.children[0].kind == "integer"
                ):
                    result[parameter.name] = argument
                    continue
                raise _UnsupportedExpression(f"handler_argument:{parameter.name}")
            parameter_type = self.context.canonical_type(
                parameter.type, event.target[:-1]
            )
            result[parameter.name] = DerivationTerm(
                "object", parameter_type, value
            )
        return result

    def _signal_event(
        self,
        signal: ModelSignal,
        source: tuple[str, ...],
        bindings: dict[str, DerivationTerm | ModelExpression],
    ) -> DerivationEvent:
        return self.context.instantiate_signal(
            signal, source[:-1], source, bindings, self.values
        )

    def continuation_snapshots(self) -> tuple[DerivationContinuation, ...]:
        return tuple(
            DerivationContinuation(
                runtime.root,
                tuple(
                    DerivationFrame(
                        frame.object,
                        frame.handler,
                        frame.control_index,
                        tuple(
                            DerivationBinding(name, value)
                            for name, value in sorted(frame.bindings.items())
                            if isinstance(value, DerivationTerm)
                        ),
                    )
                    for frame in runtime.frames
                ),
            )
            for runtime in self.continuations.values()
            if runtime.suspended and runtime.frames
        )

    def _set_failure(
        self,
        code: str,
        path: str,
        message: str,
        features: tuple[str, ...] = (),
    ) -> DerivationFailure:
        failure = DerivationFailure(code, path, message, features)
        if self.failure is None:
            self.failure = failure
        return failure

    @staticmethod
    def _resolution_failure_code(feature: str) -> str:
        if feature.startswith("CurrentTaskRef"):
            return "invalid_current_task_ref"
        if feature.startswith("CurrentCPU"):
            return "invalid_current_cpu_ref"
        return "unsupported_feature"

    def _unit(
        self,
        *,
        kind: str,
        event: DerivationEvent,
        before: tuple[str, ...] | None,
        handler: tuple[str, ...] | None,
        candidate: tuple[str, ...] | None,
        depends_on: list[DerivationCheck],
        drives: list[DerivationUnit],
        ensures: list[DerivationCheck],
        establishes: list[DerivationCheck],
        invariants: list[DerivationCheck],
        state_after: tuple[str, ...] | None,
        emits: list[DerivationUnit],
        status: str,
        failure: DerivationFailure | None,
        yields: list[DerivationUnit] | None = None,
        directives: list[DerivationDirective] | None = None,
        resumes: list[DerivationUnit] | None = None,
        switches: list[DerivationSwitch] | None = None,
        bindings: list[DerivationBindingResult] | None = None,
        relation_effects: list[DerivationRelationEffect] | None = None,
    ) -> DerivationUnit:
        return DerivationUnit(
            kind=kind,
            event=event,
            state_before=before,
            handler=handler,
            candidate_state=candidate,
            depends_on=tuple(depends_on),
            drives=tuple(drives),
            ensures=tuple(ensures),
            establishes=tuple(establishes),
            invariants=tuple(invariants),
            state_after=state_after,
            emits=tuple(emits),
            status=status,
            failure=failure,
            yields=() if yields is None else tuple(yields),
            directives=() if directives is None else tuple(directives),
            resumes=() if resumes is None else tuple(resumes),
            switches=() if switches is None else tuple(switches),
            bindings=() if bindings is None else tuple(bindings),
            relation_effects=()
            if relation_effects is None
            else tuple(relation_effects),
        )

    def _clear_continuations_for_panic(self) -> None:
        for runtime in self.continuations.values():
            runtime.frames.clear()
            runtime.executing = False
            runtime.suspended = False
            runtime.completed = True
            runtime.waiting_yield_target = False
            runtime.resume_requested = False

    def _panic(
        self, path: str, directive: DerivationDirective
    ) -> DerivationFailure:
        return self._set_failure("panic", path, directive.message)

    def _candidate_values(
        self,
        model_object: ModelObject,
        handler: object,
        bindings: dict[str, DerivationTerm | ModelExpression],
    ) -> dict[tuple[tuple[str, ...], str], tuple[str, ...]]:
        candidate = dict(self.values)
        for block in handler.blocks:
            if block.kind != "updates":
                continue
            for update in block.updates:
                flattened = _access(update.target)
                if flattened is None or len(flattened[0]) != 2:
                    raise RuntimeError("compiled update target is invalid")
                value = self.context.resolve_value(
                    update.value,
                    self.context.object_module(model_object.name),
                    model_object.name,
                    bindings,
                    candidate,
                )
                if value is None:
                    raise _UnsupportedExpression(
                        f"update:{flattened[0][-1]}"
                    )
                candidate[(model_object.name, flattened[0][-1])] = value
        return candidate

    @staticmethod
    def _term_sort_key(term: DerivationTerm) -> tuple[object, ...]:
        return (term.kind, term.type, term.value)

    def _object_term(self, value: tuple[str, ...]) -> DerivationTerm:
        model_object = self.context.objects[value]
        return DerivationTerm(
            "object",
            self.context.canonical_type(
                model_object.base_type, self.context.object_module(value)
            ),
            value,
        )

    @staticmethod
    def _handler_bindings(handler: object) -> tuple[ModelBinding, ...]:
        return tuple(
            binding
            for block in handler.blocks
            if block.kind == "binds"
            for binding in block.bindings
        )

    @staticmethod
    def _uses_binding(
        expression: ModelExpression, names: frozenset[str]
    ) -> bool:
        return bool(
            expression.kind == "identifier" and str(expression.value) in names
            or any(
                _Execution._uses_binding(child, names)
                for child in expression.children
            )
        )

    def _evaluate_handler_bindings(
        self,
        model_object: ModelObject,
        handler: object,
        bindings: dict[str, DerivationTerm | ModelExpression],
        results: list[DerivationBindingResult],
        path: str,
    ) -> DerivationFailure | None:
        module = self.context.object_module(model_object.name)
        for index, binding in enumerate(self._handler_bindings(handler)):
            relation = self.context.relation_call(binding.expression, module)
            if relation is None:
                raise RuntimeError("compiled binding expression is not a relation lookup")
            owner, method, arguments = relation
            container, key_type, value_type = self.context.containers[owner]
            key = self.context.typed_term(
                arguments[0],
                key_type,
                module,
                model_object.name,
                bindings,
                self.values,
            )
            candidates = tuple(
                sorted(
                    {
                        item.value
                        for item in self.tuples
                        if item.owner == owner and item.key == key
                    },
                    key=self._term_sort_key,
                )
            )
            code: str | None = None
            if not candidates:
                code = (
                    "relation_key_missing"
                    if container == "Relation"
                    else "map_key_missing"
                )
            elif method == "unique_value" and len(candidates) != 1:
                code = "relation_key_ambiguous"
            if code is not None:
                results.append(
                    DerivationBindingResult(
                        binding.name,
                        self.context.canonical_type(binding.type, module),
                        binding.expression,
                        owner,
                        key,
                        None,
                        "failed",
                        code,
                        candidates,
                    )
                )
                candidate_text = ", ".join(
                    self.context.term_text(item) for item in candidates
                )
                detail = (
                    f"; candidates: {candidate_text}" if candidate_text else ""
                )
                return self._set_failure(
                    code,
                    f"{path}.bindings[{index}]",
                    f"{self.context.object_names[owner]}.{method} key "
                    f"{self.context.term_text(key)} failed{detail}",
                )
            value = candidates[0]
            # The tuple already carries the container's declared V type.
            if value.type != value_type:
                value = DerivationTerm(value.kind, value_type, value.value)
            bindings[binding.name] = value
            results.append(
                DerivationBindingResult(
                    binding.name,
                    self.context.canonical_type(binding.type, module),
                    binding.expression,
                    owner,
                    key,
                    value,
                    "passed",
                )
            )
        return None

    def _stage_relation_effects(
        self,
        model_object: ModelObject,
        handler: object,
        bindings: dict[str, DerivationTerm | ModelExpression],
        candidate_values: dict[tuple[tuple[str, ...], str], tuple[str, ...]],
        effects: list[DerivationRelationEffect],
        path: str,
    ) -> tuple[set[DerivationTuple], DerivationFailure | None]:
        staged: set[DerivationTuple] = set()
        parsed: list[DerivationTuple] = []
        module = self.context.object_module(model_object.name)
        for expression in (
            expression
            for block in handler.blocks
            if block.kind == "establishes"
            for expression in block.expressions
        ):
            relation = self.context.relation_call(expression, module)
            if relation is None:
                continue
            owner, method, arguments = relation
            if method != "contains":
                raise RuntimeError("compiled relation effect is not contains")
            container, key_type, value_type = self.context.containers[owner]
            key = self.context.typed_term(
                arguments[0],
                key_type,
                module,
                model_object.name,
                bindings,
                candidate_values,
            )
            value = self.context.typed_term(
                arguments[1],
                value_type,
                module,
                model_object.name,
                bindings,
                candidate_values,
            )
            item = DerivationTuple(owner, container, key, value)
            parsed.append(item)
            staged.add(item)

        conflict_groups: list[
            tuple[tuple[str, ...], DerivationTerm, tuple[DerivationTerm, ...]]
        ] = []
        map_keys = sorted(
            {
                (item.owner, item.key)
                for item in parsed
                if item.container == "Map"
            },
            key=lambda item: (item[0], self._term_sort_key(item[1])),
        )
        for owner, key in map_keys:
            values = tuple(
                sorted(
                    {
                        item.value
                        for item in self.tuples | staged
                        if item.owner == owner and item.key == key
                    },
                    key=self._term_sort_key,
                )
            )
            if len(values) > 1:
                conflict_groups.append((owner, key, values))

        conflict_keys = {(owner, key) for owner, key, _ in conflict_groups}
        conflict_values = {
            (owner, key): values for owner, key, values in conflict_groups
        }
        for item in parsed:
            conflicting = conflict_values.get((item.owner, item.key), ())
            effects.append(
                DerivationRelationEffect(
                    item.owner,
                    item.container,
                    item.key,
                    item.value,
                    "failed" if (item.owner, item.key) in conflict_keys else "established",
                    tuple(value for value in conflicting if value != item.value),
                )
            )
        effects.sort(
            key=lambda effect: (
                effect.owner,
                effect.container,
                self._term_sort_key(effect.key),
                self._term_sort_key(effect.value),
                effect.status,
            )
        )
        if conflict_groups:
            owner, key, values = conflict_groups[0]
            return staged, self._set_failure(
                "map_key_conflict",
                f"{path}.relation_effects[0]",
                f"{self.context.object_names[owner]} key "
                f"{self.context.term_text(key)} conflicts across "
                + ", ".join(self.context.term_text(term) for term in values),
            )
        return staged, None

    def undeclared_root(self, event: DerivationEvent, path: str) -> DerivationUnit:
        failure = self._set_failure(
            "undeclared_external_signal",
            path,
            "root signal is not declared by entry.origin",
        )
        return self._unit(
            kind="root",
            event=event,
            before=self.states.get(event.target),
            handler=None,
            candidate=None,
            depends_on=[],
            drives=[],
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=None,
            emits=[],
            status=failure.code,
            failure=failure,
        )

    def _find_handler(
        self, event: DerivationEvent
    ) -> tuple[ModelObject, object | None]:
        model_object = self.context.objects[event.target]
        before = self.states[event.target]
        current_state = next(
            (state for state in model_object.states if state.name == before), None
        )
        handlers = ()
        if current_state is not None:
            handlers = (
                current_state.transitions
                if event.signal[0] == "Transition"
                else current_state.actions
            )
        return model_object, next(
            (item for item in handlers if item.signal == event.signal), None
        )

    @staticmethod
    def _control_items(
        handler: ModelAction,
    ) -> tuple[ModelSignal | DerivationDirective, ...]:
        return tuple(
            item
            for block in handler.blocks
            if block.kind in {"drives", "yields", "print", "panic"}
            for item in (
                block.signals
                if block.kind in {"drives", "yields"}
                else (
                    DerivationDirective(
                        block.kind, str(block.expressions[0].value)
                    ),
                )
            )
        )

    def _continuation_failure_unit(
        self,
        event: DerivationEvent,
        kind: str,
        path: str,
        code: str,
        message: str,
        handler: tuple[str, ...] | None = None,
    ) -> DerivationUnit:
        failure = self._set_failure(code, path, message)
        return self._unit(
            kind=kind,
            event=event,
            before=self.states.get(event.target),
            handler=handler,
            candidate=None,
            depends_on=[],
            drives=[],
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=None,
            emits=[],
            status=code,
            failure=failure,
        )

    def _preflight_yield(
        self, event: DerivationEvent, path: str
    ) -> DerivationFailure | None:
        model_object, handler = self._find_handler(event)
        if model_object.continuation:
            if event.signal != ("Action", "Enter"):
                return self._set_failure(
                    "invalid_continuation_action",
                    f"{path}.handler",
                    "a suspended continuation can only be entered through Action::Enter",
                )
            runtime = self.continuations.get(event.target)
            if runtime is not None and (
                runtime.executing or runtime.resume_requested
            ):
                return self._set_failure(
                    "continuation_reentry",
                    f"{path}.handler",
                    "continuation cannot be re-entered before the current target settles",
                )
            if runtime is not None and runtime.waiting_yield_target:
                return self._set_failure(
                    "continuation_reentry",
                    f"{path}.handler",
                    "yield target cannot synchronously re-enter the suspended continuation",
                )
            if runtime is not None and runtime.completed:
                return self._set_failure(
                    "no_resumable_continuation",
                    f"{path}.handler",
                    "continuation has exited and has no resumable frame",
                )
        if handler is None:
            return self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                "yield target has no matching handler in its current state",
            )
        return None

    def _frame_unit(
        self,
        frame: _ResumeFrame,
        status: str,
        failure: DerivationFailure | None = None,
        *,
        drives: list[DerivationUnit] | None = None,
    ) -> DerivationUnit:
        return self._unit(
            kind=frame.kind,
            event=frame.event,
            before=frame.before,
            handler=frame.handler,
            candidate=None,
            depends_on=frame.depends_on,
            drives=frame.drives if drives is None else drives,
            ensures=frame.ensures,
            establishes=frame.establishes,
            invariants=frame.invariants,
            state_after=frame.before if status == "passed" else None,
            emits=frame.emits,
            status=status,
            failure=failure,
            yields=frame.yields,
            directives=frame.directives,
            resumes=frame.resumes,
            bindings=frame.binding_results,
            relation_effects=frame.relation_effects,
        )

    def _finish_runtime(
        self, runtime: _ContinuationRuntime, unit: DerivationUnit
    ) -> None:
        runtime.result_unit = unit
        runtime.executing = False
        runtime.suspended = False
        runtime.completed = True
        runtime.waiting_yield_target = False
        runtime.resume_requested = False

    def _abort_frame(
        self,
        runtime: _ContinuationRuntime,
        status: str,
        failure: DerivationFailure | None,
    ) -> None:
        self.context.query_tuples = self.tuples
        frame = runtime.frames.pop()
        unit = self._frame_unit(frame, status, failure)
        while runtime.frames:
            parent = runtime.frames.pop()
            parent.drives.append(unit)
            unit = self._frame_unit(parent, "stopped")
        self._finish_runtime(runtime, unit)

    def _suspended_unit(self, runtime: _ContinuationRuntime) -> DerivationUnit:
        active: DerivationUnit | None = None
        for frame in reversed(runtime.frames):
            drives = list(frame.drives)
            if active is not None:
                drives.append(active)
            active = self._frame_unit(frame, "yielded", drives=drives)
        if active is None:
            raise RuntimeError("suspended continuation has no frame")
        return active

    @staticmethod
    def _reset_runtime_segment(runtime: _ContinuationRuntime) -> None:
        for frame in runtime.frames:
            frame.reset_segment()

    def _resume_ack(
        self, event: DerivationEvent, kind: str
    ) -> DerivationUnit:
        before = self.states[event.target]
        return self._unit(
            kind=kind,
            event=event,
            before=before,
            handler=event.signal,
            candidate=None,
            depends_on=[],
            drives=[],
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=before,
            emits=[],
            status="passed",
            failure=None,
        )

    def _push_continuation_frame(
        self,
        runtime: _ContinuationRuntime,
        event: DerivationEvent,
        path: str,
    ) -> DerivationFailure | None:
        model_object, handler = self._find_handler(event)
        if event.signal[0] != "Action":
            return self._set_failure(
                "invalid_continuation_action",
                f"{path}.handler",
                "continuation frames can only execute Action handlers",
            )
        if handler is None:
            return self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                "continuation has no matching nested Action handler",
            )
        runtime.frames.append(
            _ResumeFrame(
                model_object.name,
                event,
                "drive",
                event.signal,
                before=self.states[event.target],
                bindings=self._bind_handler(event, handler),
            )
        )
        return None

    def _continue_runtime(
        self, runtime: _ContinuationRuntime, path: str
    ) -> None:
        while runtime.frames and runtime.executing and self.failure is None:
            frame = runtime.frames[-1]
            model_object = self.context.objects[frame.object]
            before = self.states[frame.object]
            state = next(
                item for item in model_object.states if item.name == before
            )
            handler = next(
                action for action in state.actions if action.signal == frame.handler
            )
            module = self.context.object_module(model_object.name)

            if not frame.entered:
                depends = tuple(
                    expression
                    for block in handler.blocks
                    if block.kind == "depends_on"
                    for expression in block.expressions
                )
                binding_names = frozenset(
                    binding.name for binding in self._handler_bindings(handler)
                )

                def check_depends(
                    selected: tuple[tuple[int, ModelExpression], ...]
                ) -> DerivationFailure | None:
                    for index, expression in selected:
                        text = self.context.expression_text(
                            expression,
                            module,
                            model_object.name,
                            frame.bindings,
                            self.values,
                        )
                        try:
                            passed = self.context.evaluate(
                                expression,
                                module,
                                self.states,
                                self.facts,
                                model_object.name,
                                frame.bindings,
                                self.values,
                            )
                        except _UnsupportedExpression as exc:
                            frame.depends_on.append(
                                DerivationCheck(text, "unsupported")
                            )
                            return self._set_failure(
                                "unsupported_feature",
                                f"{path}.depends_on[{index}]",
                                "depends_on expression is not supported",
                                (exc.feature,),
                            )
                        frame.depends_on.append(
                            DerivationCheck(text, "passed" if passed else "failed")
                        )
                        if not passed:
                            return self._set_failure(
                                "depends_on_failed",
                                f"{path}.depends_on[{index}]",
                                "depends_on condition is false",
                            )
                    return None

                indexed = tuple(enumerate(depends))
                independent = tuple(
                    item
                    for item in indexed
                    if not self._uses_binding(item[1], binding_names)
                )
                dependent = tuple(
                    item
                    for item in indexed
                    if self._uses_binding(item[1], binding_names)
                )
                failure = check_depends(independent)
                if failure is not None:
                    self._abort_frame(runtime, failure.code, failure)
                    return
                try:
                    failure = self._evaluate_handler_bindings(
                        model_object,
                        handler,
                        frame.bindings,
                        frame.binding_results,
                        path,
                    )
                except _UnsupportedExpression as exc:
                    failure = self._set_failure(
                        "unsupported_feature",
                        f"{path}.bindings[{len(frame.binding_results)}]",
                        "binding key cannot be resolved",
                        (exc.feature,),
                    )
                if failure is not None:
                    self._abort_frame(runtime, failure.code, failure)
                    return
                failure = check_depends(dependent)
                if failure is not None:
                    self._abort_frame(runtime, failure.code, failure)
                    return
                try:
                    preflight_values = self._candidate_values(
                        model_object, handler, frame.bindings
                    )
                    frame.staged_values = {
                        key: value
                        for key, value in preflight_values.items()
                        if self.values.get(key) != value
                    }
                    frame.staged_tuples, failure = self._stage_relation_effects(
                        model_object,
                        handler,
                        frame.bindings,
                        preflight_values,
                        frame.relation_effects,
                        path,
                    )
                except _UnsupportedExpression as exc:
                    failure = self._set_failure(
                        "unsupported_feature",
                        f"{path}.relation_effects[{len(frame.relation_effects)}]",
                        "handler preflight term cannot be resolved",
                        (exc.feature,),
                    )
                if failure is not None:
                    self._abort_frame(runtime, failure.code, failure)
                    return
                for effect in frame.relation_effects:
                    frame.establishes.append(
                        DerivationCheck(
                            f"{self.context.object_names[effect.owner]}.contains("
                            f"{self.context.term_text(effect.key)}, "
                            f"{self.context.term_text(effect.value)})",
                            "established",
                        )
                    )
                frame.entered = True

            controls = self._control_items(handler)
            if frame.control_index < len(controls):
                control = controls[frame.control_index]
                frame.control_index += 1
                if isinstance(control, DerivationDirective):
                    frame.directives.append(control)
                    if control.kind == "panic":
                        failure = self._set_failure(
                            "panic",
                            f"{path}.directives[{len(frame.directives) - 1}]",
                            control.message,
                        )
                        self._abort_frame(runtime, failure.code, failure)
                        return
                    continue

                child_event = self._signal_event(
                    control, model_object.name, frame.bindings
                )
                if control.mode == "yield":
                    child_path = f"{path}.yields[{len(frame.yields)}]"
                    preflight = self._preflight_yield(child_event, child_path)
                    if preflight is not None:
                        self._abort_frame(runtime, preflight.code, preflight)
                        return

                    runtime.executing = False
                    runtime.suspended = True
                    runtime.waiting_yield_target = True
                    yielded = self.run_unit(child_event, "yield", child_path)
                    frame.yields.append(yielded)
                    runtime.waiting_yield_target = False

                    if self.failure is not None:
                        if runtime.resume_requested:
                            runtime.resume_requested = False
                            runtime.suspended = False
                            self._abort_frame(runtime, "stopped", None)
                        return
                    if not runtime.resume_requested:
                        return
                    runtime.resume_requested = False
                    runtime.suspended = False
                    runtime.executing = True
                    continue

                child_path = f"{path}.drives[{len(frame.drives)}]"
                child_object = self.context.objects.get(child_event.target)
                if child_object is not None and child_object.continuation:
                    if (
                        child_event.target == runtime.root
                        and child_event.signal == ("Action", "Enter")
                    ):
                        failure = self._set_failure(
                            "continuation_reentry",
                            f"{child_path}.handler",
                            "a running continuation cannot synchronously call Action::Enter",
                        )
                        self._abort_frame(runtime, failure.code, failure)
                        return
                    failure = self._push_continuation_frame(
                        runtime, child_event, child_path
                    )
                    if failure is not None:
                        self._abort_frame(runtime, failure.code, failure)
                        return
                    continue

                frame.drives.append(self.run_unit(child_event, "drive", child_path))
                if self.failure is not None:
                    self._abort_frame(runtime, "stopped", None)
                    return
                continue

            candidate_values = dict(self.values)
            candidate_values.update(frame.staged_values or {})
            for staged_item in frame.staged_tuples:
                if staged_item.container != "Map":
                    continue
                late_conflicts = tuple(
                    sorted(
                        {
                            item.value
                            for item in self.tuples
                            if item.owner == staged_item.owner
                            and item.key == staged_item.key
                            and item.value != staged_item.value
                        },
                        key=self._term_sort_key,
                    )
                )
                if not late_conflicts:
                    continue
                frame.relation_effects[:] = [
                    replace(
                        effect,
                        status="failed",
                        conflict_values=late_conflicts,
                    )
                    if effect.owner == staged_item.owner
                    and effect.key == staged_item.key
                    and effect.value == staged_item.value
                    else effect
                    for effect in frame.relation_effects
                ]
                failure = self._set_failure(
                    "map_key_conflict",
                    f"{path}.relation_effects",
                    "Map key became conflicting while the continuation was suspended",
                )
                self._abort_frame(runtime, failure.code, failure)
                return
            self.context.query_tuples = self.tuples | frame.staged_tuples

            ensures = tuple(
                expression
                for block in handler.blocks
                if block.kind == "ensures"
                for expression in block.expressions
            )
            for index, expression in enumerate(ensures):
                text = self.context.expression_text(
                    expression,
                    module,
                    model_object.name,
                    frame.bindings,
                    candidate_values,
                )
                try:
                    passed = self.context.evaluate(
                        expression,
                        module,
                        self.states,
                        self.facts,
                        model_object.name,
                        frame.bindings,
                        candidate_values,
                    )
                except _UnsupportedExpression as exc:
                    frame.ensures.append(DerivationCheck(text, "unsupported"))
                    failure = self._set_failure(
                        "unsupported_feature",
                        f"{path}.ensures[{index}]",
                        "ensures expression is not supported",
                        (exc.feature,),
                    )
                    self._abort_frame(runtime, failure.code, failure)
                    return
                frame.ensures.append(
                    DerivationCheck(text, "passed" if passed else "failed")
                )
                if not passed:
                    failure = self._set_failure(
                        "ensures_failed",
                        f"{path}.ensures[{index}]",
                        "ensures condition is false",
                    )
                    self._abort_frame(runtime, failure.code, failure)
                    return

            staged_facts: set[DerivationFact] = set()
            establishes = tuple(
                expression
                for block in handler.blocks
                if block.kind == "establishes"
                for expression in block.expressions
            )
            for index, expression in enumerate(establishes):
                if self.context.relation_call(expression, module) is not None:
                    continue
                text = self.context.expression_text(
                    expression,
                    module,
                    model_object.name,
                    frame.bindings,
                    candidate_values,
                )
                try:
                    fact = self.context.normalize_fact(
                        expression,
                        module,
                        model_object.name,
                        frame.bindings,
                        candidate_values,
                    )
                except _UnsupportedExpression as exc:
                    frame.establishes.append(
                        DerivationCheck(text, "unsupported")
                    )
                    failure = self._set_failure(
                        "unsupported_feature",
                        f"{path}.establishes[{index}]",
                        "establishes only accepts normalizable positive predicate calls",
                        (exc.feature,),
                    )
                    self._abort_frame(runtime, failure.code, failure)
                    return
                frame.establishes.append(
                    DerivationCheck(self.context.fact_text(fact), "established")
                )
                staged_facts.add(fact)

            candidate_facts = self.facts | staged_facts
            invariant_expressions = tuple(
                expression for block in state.invariants for expression in block
            )
            for index, expression in enumerate(invariant_expressions):
                text = self.context.expression_text(
                    expression,
                    module,
                    model_object.name,
                    frame.bindings,
                    candidate_values,
                )
                try:
                    passed = self.context.evaluate(
                        expression,
                        module,
                        self.states,
                        candidate_facts,
                        model_object.name,
                        frame.bindings,
                        candidate_values,
                    )
                except _UnsupportedExpression as exc:
                    frame.invariants.append(DerivationCheck(text, "unsupported"))
                    failure = self._set_failure(
                        "unsupported_feature",
                        f"{path}.invariants[{index}]",
                        "current-state invariant expression is not supported",
                        (exc.feature,),
                    )
                    self._abort_frame(runtime, failure.code, failure)
                    return
                frame.invariants.append(
                    DerivationCheck(text, "passed" if passed else "failed")
                )
                if not passed:
                    failure = self._set_failure(
                        "invariant_failed",
                        f"{path}.invariants[{index}]",
                        "current-state invariant is false",
                    )
                    self._abort_frame(runtime, failure.code, failure)
                    return
            self.facts.update(staged_facts)
            self.tuples.update(frame.staged_tuples)
            self.context.query_tuples = self.tuples
            self.values = candidate_values

            completed = runtime.frames.pop()
            for block in handler.blocks:
                if self.failure is not None:
                    break
                if block.kind != "emits":
                    continue
                for signal in block.signals:
                    completed.emits.append(
                        self.run_unit(
                            self._signal_event(
                                signal, model_object.name, completed.bindings
                            ),
                            "emit",
                            f"{path}.emits[{len(completed.emits)}]",
                        )
                    )
                    if self.failure is not None:
                        break

            if self.failure is None:
                resume_yielded = False
                for block in handler.blocks:
                    if self.failure is not None:
                        break
                    if block.kind != "resumes":
                        continue
                    for signal in block.signals:
                        resumed = self.run_unit(
                            self._signal_event(
                                signal, model_object.name, completed.bindings
                            ),
                            "resume",
                            f"{path}.resumes[{len(completed.resumes)}]",
                        )
                        completed.resumes.append(resumed)
                        if self.failure is not None:
                            break
                        if resumed.status == "yielded":
                            resume_yielded = True
                            break
                    if resume_yielded:
                        break

            completed_unit = self._frame_unit(completed, "passed")
            if runtime.frames:
                runtime.frames[-1].drives.append(completed_unit)
                if self.failure is not None:
                    self._abort_frame(runtime, "stopped", None)
                    return
                continue
            self._finish_runtime(runtime, completed_unit)
            return

    def _run_continuation(
        self, event: DerivationEvent, kind: str, path: str
    ) -> DerivationUnit:
        before = self.states[event.target]
        if event.signal[0] != "Action":
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "invalid_continuation_action",
                "continuation entry must use an Action signal",
            )
        # A continuation may expose additional phase actions.  They are
        # independent synchronous entries rather than a resumed breakpoint;
        # discard the completed runtime so each action gets a fresh frame.
        existing = self.continuations.get(event.target)
        if existing is not None and existing.completed and event.signal != ("Action", "Enter"):
            self.continuations.pop(event.target, None)
        runtime = self.continuations.get(event.target)
        if (
            runtime is not None
            and runtime.executing
            and runtime.owner_active
            and event.mode == "resume"
        ):
            suspended_children = tuple(
                candidate
                for candidate in self.continuations.values()
                if candidate is not runtime
                and candidate.owner_active
                and candidate.suspended
                and candidate.waiting_yield_target
                and candidate.root not in self.parked_user_continuations.values()
            )
            if suspended_children:
                child = suspended_children[-1]
                child.suspended = False
                child.resume_requested = True
                return self._resume_ack(event, kind)
        if runtime is not None and (
            runtime.executing or runtime.resume_requested
        ):
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "continuation_reentry",
                "continuation is already executing",
            )
        if (
            runtime is not None
            and runtime.waiting_yield_target
            and event.mode != "resume"
        ):
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "continuation_reentry",
                "yield target cannot synchronously re-enter the suspended continuation",
            )
        if runtime is not None and (runtime.completed or not runtime.frames):
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "no_resumable_continuation",
                "continuation has exited and has no resumable frame",
            )
        if runtime is None:
            model_object, handler = self._find_handler(event)
            if handler is None:
                return self._continuation_failure_unit(
                    event,
                    kind,
                    f"{path}.handler",
                    "unhandled_signal",
                    "continuation has no handler for the requested Action",
                )
            runtime = _ContinuationRuntime(event.target)
            runtime.frames.append(
                _ResumeFrame(
                    model_object.name,
                    event,
                    kind,
                    event.signal,
                    before=before,
                    bindings=self._bind_handler(event, handler),
                )
            )
            self.continuations[event.target] = runtime
        else:
            if not runtime.suspended:
                return self._continuation_failure_unit(
                    event,
                    kind,
                    f"{path}.handler",
                    "continuation_reentry",
                    "continuation has no stable suspended breakpoint",
                )
            if runtime.owner_active:
                runtime.suspended = False
                runtime.resume_requested = True
                return self._resume_ack(event, kind)
            root_frame = runtime.frames[0]
            root_frame.event = event
            root_frame.kind = kind
            root_frame.before = before

        runtime.owner_active = True
        runtime.executing = True
        runtime.suspended = False
        runtime.result_unit = None
        try:
            self._continue_runtime(runtime, path)
            if runtime.result_unit is not None:
                return runtime.result_unit
            if runtime.suspended:
                unit = self._suspended_unit(runtime)
                self._reset_runtime_segment(runtime)
                return unit
            raise RuntimeError("continuation execution exhausted without a result")
        finally:
            if event.signal != ("Action", "Enter"):
                self.continuations.pop(event.target, None)
            runtime.owner_active = False

    def _run_collection(
        self, event: DerivationEvent, kind: str, path: str
    ) -> DerivationUnit:
        before = self.states[event.target]
        if event.signal != ("Action", "Enqueue") or len(event.arguments) != 1:
            failure = self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                "Collection only handles Action::Enqueue(item)",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=None,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )

        item = self.context.resolve_value(
            event.arguments[0], event.target[:-1], event.target, {}, self.values
        )
        if item is None:
            failure = self._set_failure(
                "unsupported_feature",
                f"{path}.event.arguments[0]",
                "Collection enqueue argument cannot be resolved",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )
        contents = self.collections[event.target]
        if item in contents:
            failure = self._set_failure(
                "duplicate_collection_item",
                f"{path}.event.arguments[0]",
                "Collection rejects duplicate elements",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=event.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )
        contents.append(item)
        return self._unit(
            kind=kind,
            event=event,
            before=before,
            handler=event.signal,
            candidate=None,
            depends_on=[],
            drives=[],
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=before,
            emits=[],
            status="passed",
            failure=None,
        )

    def _run_scheduler_core(
        self, event: DerivationEvent, kind: str, path: str
    ) -> DerivationUnit:
        before = self.states[event.target]
        runtime = self.schedulers[event.target]
        if (
            before != ("State", "Online")
            or event.signal not in {
                ("Action", "Enqueue"),
                ("Action", "Dequeue"),
            }
            or event.arguments
        ):
            failure = self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                "sched_core queue actions are only handled without arguments in State::Online",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=None,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )
        task = event.source
        if event.signal == ("Action", "Enqueue"):
            if task == runtime.idle_task:
                failure = self._set_failure(
                    "idle_task_not_queueable",
                    f"{path}.handler",
                    "The scheduler idle Task cannot be added to the runq",
                )
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=event.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )
            if task in runtime.runq:
                failure = self._set_failure(
                    "duplicate_runq_task",
                    f"{path}.handler",
                    "Task is already present in the scheduler runq",
                )
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=event.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )
            runtime.runq.append(task)
        else:
            if task not in runtime.runq:
                failure = self._set_failure(
                    "task_not_queued",
                    f"{path}.handler",
                    "Task is not present in the scheduler runq",
                )
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=event.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )
            runtime.runq.remove(task)
        return self._unit(
            kind=kind,
            event=event,
            before=before,
            handler=event.signal,
            candidate=None,
            depends_on=[],
            drives=[],
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=before,
            emits=[],
            status="passed",
            failure=None,
        )

    def _run_interrupt_control(
        self, event: DerivationEvent, kind: str, path: str
    ) -> DerivationUnit:
        cpu_name = self.interrupt_controls[event.target]
        runtime = self.cpus[cpu_name]
        before = ("State", "Online")
        supported = {
            ("Action", "MaskAll"),
            ("Action", "ClearPending"),
            ("Action", "Unmask"),
        }
        if event.signal not in supported or event.arguments:
            failure = self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                "InterruptControl only accepts parameterless MaskAll, ClearPending, and Unmask actions",
            )
            return self._unit(
                kind=kind, event=event, before=before, handler=None,
                candidate=None, depends_on=[], drives=[], ensures=[],
                establishes=[], invariants=[], state_after=None, emits=[],
                status=failure.code, failure=failure,
            )

        drives: list[DerivationUnit] = []
        if event.signal == ("Action", "MaskAll"):
            runtime.interrupt_mode = "Masked"
        elif event.signal == ("Action", "ClearPending"):
            runtime.pending_interrupts.clear()
        else:
            runtime.interrupt_mode = "Unmasked"
            pending = tuple(runtime.pending_interrupts)
            runtime.pending_interrupts.clear()
            for index, item in enumerate(pending):
                drives.append(
                    self._deliver_returning_event(
                        cpu_name,
                        item.signal,
                        item.owner,
                        item.task_flow,
                        item.user_runtime,
                        f"{path}.drives[{index}]",
                    )
                )
                if self.failure is not None:
                    break
        return self._unit(
            kind=kind,
            event=event,
            before=before,
            handler=event.signal,
            candidate=None,
            depends_on=[],
            drives=drives,
            ensures=[],
            establishes=[],
            invariants=[],
            state_after=before if self.failure is None else None,
            emits=[],
            status="passed" if self.failure is None else "stopped",
            failure=None,
        )

    def run_unit(
        self,
        event: DerivationEvent,
        kind: str,
        path: str,
        *,
        defer_resumes: bool = False,
    ) -> DerivationUnit:
        if (
            event.target in self.cpus
            and event.signal
            in {
                ("Action", "OnInterrupt"),
                ("Action", "OnException"),
                ("Action", "OnSyscallExit"),
            }
            and self.active_event_flow is not None
        ):
            if self.allow_event_cpu_entry:
                self.allow_event_cpu_entry = False
            else:
                failure = self._set_failure(
                    "nested_event_flow",
                    f"{path}.handler",
                    "an EventFlow cannot enter another EventFlow",
                )
                return self._unit(
                    kind=kind,
                    event=event,
                    before=self.states.get(event.target),
                    handler=None,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )
        if event.target in self.interrupt_controls:
            return self._run_interrupt_control(event, kind, path)
        if (
            event.target in self.user_runtime_owners
            and event.signal == ("Action", "Enter")
        ):
            return self._run_user_runtime(event, kind, path)
        if (
            event.target in self.schedulers
            and event.signal
            in {("Action", "Enqueue"), ("Action", "Dequeue")}
        ):
            return self._run_scheduler_core(event, kind, path)
        if event.target in self.collections:
            return self._run_collection(event, kind, path)
        if self.context.objects[event.target].continuation:
            return self._run_continuation(event, kind, path)
        model_object: ModelObject = self.context.objects[event.target]
        before = self.states[event.target]
        current_state = next(
            (state for state in model_object.states if state.name == before), None
        )
        handlers = ()
        if current_state is not None:
            handlers = (
                current_state.transitions
                if event.signal[0] == "Transition"
                else current_state.actions
            )
        handler = next((item for item in handlers if item.signal == event.signal), None)
        checks: dict[str, list[DerivationCheck]] = {
            "depends_on": [],
            "ensures": [],
            "establishes": [],
            "invariants": [],
        }
        drives: list[DerivationUnit] = []
        emits: list[DerivationUnit] = []
        resumes: list[DerivationUnit] = []
        directives: list[DerivationDirective] = []
        switches: list[DerivationSwitch] = []
        binding_results: list[DerivationBindingResult] = []
        relation_effects: list[DerivationRelationEffect] = []
        switched_task: tuple[str, ...] | None = None
        deferred_resume_units: list[
            tuple[
                int,
                tuple[ModelSignal, ...],
                dict[str, DerivationTerm | ModelExpression],
            ]
        ] = []
        if handler is None:
            signal_kind = event.signal[0].lower()
            failure = self._set_failure(
                "unhandled_signal",
                f"{path}.handler",
                f"target object has no matching {signal_kind} handler in its current state",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=None,
                candidate=None,
                depends_on=checks["depends_on"],
                drives=drives,
                ensures=checks["ensures"],
                establishes=checks["establishes"],
                invariants=checks["invariants"],
                state_after=None,
                emits=emits,
                status=failure.code,
                failure=failure,
                bindings=binding_results,
                relation_effects=relation_effects,
            )

        reset_current = event.signal == ("Action", "ResetCurrent")
        if reset_current:
            invalid_reset: tuple[str, str] | None = None
            if self.current_task_ref is not None:
                invalid_reset = (
                    "invalid_current_task_ref",
                    "ResetCurrent may initialize CurrentTaskRef only once",
                )
            else:
                try:
                    task_flow = self._task_flow(event.target)
                    bound_cpu = self.values.get((task_flow, "cpu_ref"))
                    if bound_cpu != self._current_cpu():
                        invalid_reset = (
                            "invalid_current_cpu_ref",
                            "ResetCurrent requires BootTask TaskFlow to be bound to CurrentCPU",
                        )
                except _UnsupportedExpression:
                    invalid_reset = (
                        "invalid_current_cpu_ref",
                        "ResetCurrent requires BootTask TaskFlow to be bound to CurrentCPU",
                    )
            if invalid_reset is not None:
                code, message = invalid_reset
                failure = self._set_failure(code, f"{path}.handler", message)
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=handler.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )

        if (
            self.cpus
            and event.target in self.schedulers
            and event.signal == ("Action", "Schedule")
        ):
            try:
                self._current_cpu()
            except _UnsupportedExpression:
                failure = self._set_failure(
                    "invalid_current_cpu_ref",
                    f"{path}.handler",
                    "Schedule requires the current TaskFlow to have a valid cpu_ref",
                )
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=handler.signal,
                    candidate=None,
                    depends_on=[],
                    drives=[],
                    ensures=[],
                    establishes=[],
                    invariants=[],
                    state_after=None,
                    emits=[],
                    status=failure.code,
                    failure=failure,
                )

        if (
            event.target in self.schedulers
            and event.signal == ("Action", "Schedule")
            and (
                self.current_task_ref is None
                or self.states.get(self.current_task_ref) != ("State", "OnCpu")
            )
        ):
            failure = self._set_failure(
                "invalid_current_task_ref",
                f"{path}.handler",
                "Schedule requires CurrentTaskRef to identify a Task in State::OnCpu",
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=handler.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )

        module = self.context.object_module(model_object.name)
        try:
            bindings = self._bind_handler(event, handler)
        except _UnsupportedExpression as exc:
            failure = self._set_failure(
                self._resolution_failure_code(exc.feature),
                f"{path}.event.arguments",
                "handler argument cannot be resolved",
                (exc.feature,),
            )
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=handler.signal,
                candidate=None,
                depends_on=[],
                drives=[],
                ensures=[],
                establishes=[],
                invariants=[],
                state_after=None,
                emits=[],
                status=failure.code,
                failure=failure,
            )
        blocks = handler.blocks
        expressions = {
            name: tuple(
                expression
                for block in blocks
                if block.kind == name
                for expression in block.expressions
            )
            for name in ("depends_on", "ensures", "establishes")
        }
        emit_signals = tuple(
            signal
            for block in blocks
            if block.kind == "emits"
            for signal in block.signals
        )
        resume_signals = tuple(
            signal
            for block in blocks
            if block.kind == "resumes"
            for signal in block.signals
        )
        candidate_state = (
            handler.target_state if event.signal[0] == "Transition" else None
        )

        def failed_unit(failure: DerivationFailure) -> DerivationUnit:
            self.context.query_tuples = self.tuples
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=handler.signal,
                candidate=candidate_state,
                depends_on=checks["depends_on"],
                drives=drives,
                ensures=checks["ensures"],
                establishes=checks["establishes"],
                invariants=checks["invariants"],
                state_after=None,
                emits=emits,
                status=failure.code,
                failure=failure,
                directives=directives,
                resumes=resumes,
                switches=switches,
                bindings=binding_results,
                relation_effects=relation_effects,
            )

        relation_binding_names = frozenset(
            binding.name for binding in self._handler_bindings(handler)
        )

        def evaluate_depends(
            selected: tuple[tuple[int, ModelExpression], ...]
        ) -> DerivationFailure | None:
            for index, expression in selected:
                text = self.context.expression_text(
                    expression, module, model_object.name, bindings, self.values
                )
                try:
                    passed = self.context.evaluate(
                        expression,
                        module,
                        self.states,
                        self.facts,
                        model_object.name,
                        bindings,
                        self.values,
                    )
                except _UnsupportedExpression as exc:
                    checks["depends_on"].append(DerivationCheck(text, "unsupported"))
                    return self._set_failure(
                        "unsupported_feature",
                        f"{path}.depends_on[{index}]",
                        "depends_on expression is not supported",
                        (exc.feature,),
                    )
                checks["depends_on"].append(
                    DerivationCheck(text, "passed" if passed else "failed")
                )
                if not passed:
                    return self._set_failure(
                        "depends_on_failed",
                        f"{path}.depends_on[{index}]",
                        "depends_on condition is false",
                    )
            return None

        indexed_depends = tuple(enumerate(expressions["depends_on"]))
        independent = tuple(
            item
            for item in indexed_depends
            if not self._uses_binding(item[1], relation_binding_names)
        )
        dependent = tuple(
            item
            for item in indexed_depends
            if self._uses_binding(item[1], relation_binding_names)
        )
        dependency_failure = evaluate_depends(independent)
        if dependency_failure is not None:
            return failed_unit(dependency_failure)

        try:
            binding_failure = self._evaluate_handler_bindings(
                model_object, handler, bindings, binding_results, path
            )
        except _UnsupportedExpression as exc:
            return failed_unit(
                self._set_failure(
                    "unsupported_feature",
                    f"{path}.bindings[{len(binding_results)}]",
                    "binding key cannot be resolved",
                    (exc.feature,),
                )
            )
        if binding_failure is not None:
            return failed_unit(binding_failure)

        dependency_failure = evaluate_depends(dependent)
        if dependency_failure is not None:
            return failed_unit(dependency_failure)

        try:
            preflight_values = self._candidate_values(
                model_object, handler, bindings
            )
        except _UnsupportedExpression as exc:
            return failed_unit(
                self._set_failure(
                    self._resolution_failure_code(exc.feature),
                    f"{path}.updates",
                    "updates value cannot be resolved",
                    (exc.feature,),
                )
            )
        staged_value_updates = {
            key: value
            for key, value in preflight_values.items()
            if self.values.get(key) != value
        }
        try:
            staged_tuples, relation_failure = self._stage_relation_effects(
                model_object,
                handler,
                bindings,
                preflight_values,
                relation_effects,
                path,
            )
        except _UnsupportedExpression as exc:
            return failed_unit(
                self._set_failure(
                    "unsupported_feature",
                    f"{path}.relation_effects[{len(relation_effects)}]",
                    "relation effect term cannot be resolved",
                    (exc.feature,),
                )
            )
        if relation_failure is not None:
            return failed_unit(relation_failure)
        for effect in relation_effects:
            checks["establishes"].append(
                DerivationCheck(
                    f"{self.context.object_names[effect.owner]}.contains("
                    f"{self.context.term_text(effect.key)}, "
                    f"{self.context.term_text(effect.value)})",
                    "established",
                )
            )

        controls: list[
            ModelSignal | ModelHandlerBlock | DerivationDirective
        ] = []
        for block in blocks:
            if block.kind == "drives":
                controls.extend(block.signals)
            elif block.kind == "switches":
                controls.append(block)
            elif block.kind in {"print", "panic"}:
                controls.append(
                    DerivationDirective(
                        block.kind, str(block.expressions[0].value)
                    )
                )
        for control in controls:
            if isinstance(control, DerivationDirective):
                directives.append(control)
                if control.kind == "panic":
                    return failed_unit(
                        self._panic(
                            f"{path}.directives[{len(directives) - 1}]",
                            control,
                        )
                    )
                continue
            if isinstance(control, ModelHandlerBlock):
                assert control.switches is not None
                scheduler = self.schedulers[event.target]
                candidates = (
                    tuple(scheduler.runq)
                    if scheduler.runq
                    else (scheduler.idle_task,)
                )
                if self.switch_cursor >= len(self.switch_choices):
                    raise _SwitchNeeded(candidates)
                switched_task = self.switch_choices[self.switch_cursor]
                self.switch_cursor += 1
                if switched_task not in candidates:
                    raise RuntimeError("scheduler replay selected a stale candidate")
                bindings[control.switches] = self._object_term(switched_task)
                switches.append(
                    DerivationSwitch(
                        control.switches,
                        switched_task,
                        not scheduler.runq,
                        False,
                        len(drives),
                    )
                )
                continue
            try:
                child_event = self._signal_event(
                    control, model_object.name, bindings
                )
            except _UnsupportedExpression as exc:
                return failed_unit(
                    self._set_failure(
                        self._resolution_failure_code(exc.feature),
                        f"{path}.drives[{len(drives)}].event.arguments",
                        "signal argument cannot be resolved",
                        (exc.feature,),
                    )
                )
            defer_child_resumes = (
                switched_task is not None
                and child_event.target == switched_task
                and child_event.signal == ("Transition", "Resume")
            )
            drive_index = len(drives)
            deferred_signals: tuple[ModelSignal, ...] = ()
            deferred_bindings: dict[str, DerivationTerm | ModelExpression] = {}
            if defer_child_resumes:
                _, child_handler = self._find_handler(child_event)
                if child_handler is not None:
                    deferred_signals = tuple(
                        signal
                        for block in child_handler.blocks
                        if block.kind == "resumes"
                        for signal in block.signals
                    )
                    deferred_bindings = self._bind_handler(
                        child_event, child_handler
                    )
            drives.append(
                self.run_unit(
                    child_event,
                    "drive",
                    f"{path}.drives[{drive_index}]",
                    defer_resumes=defer_child_resumes,
                )
            )
            if defer_child_resumes:
                deferred_resume_units.append(
                    (drive_index, deferred_signals, deferred_bindings)
                )
            if self.failure is not None:
                return self._unit(
                    kind=kind,
                    event=event,
                    before=before,
                    handler=handler.signal,
                    candidate=candidate_state,
                    depends_on=checks["depends_on"],
                    drives=drives,
                    ensures=checks["ensures"],
                    establishes=checks["establishes"],
                    invariants=checks["invariants"],
                    state_after=None,
                    emits=emits,
                    status="stopped",
                    failure=None,
                    directives=directives,
                    resumes=resumes,
                    switches=switches,
                    bindings=binding_results,
                    relation_effects=relation_effects,
                )

        candidate_states = dict(self.states)
        if candidate_state is not None:
            candidate_states[event.target] = candidate_state
        candidate_values = dict(self.values)
        candidate_values.update(staged_value_updates)
        for staged_item in staged_tuples:
            if staged_item.container != "Map":
                continue
            late_conflicts = tuple(
                sorted(
                    {
                        item.value
                        for item in self.tuples
                        if item.owner == staged_item.owner
                        and item.key == staged_item.key
                        and item.value != staged_item.value
                    },
                    key=self._term_sort_key,
                )
            )
            if late_conflicts:
                relation_effects[:] = [
                    replace(
                        effect,
                        status="failed",
                        conflict_values=late_conflicts,
                    )
                    if effect.owner == staged_item.owner
                    and effect.key == staged_item.key
                    and effect.value == staged_item.value
                    else effect
                    for effect in relation_effects
                ]
                return failed_unit(
                    self._set_failure(
                        "map_key_conflict",
                        f"{path}.relation_effects",
                        "Map key became conflicting during nested drives",
                    )
                )
        self.context.query_tuples = self.tuples | staged_tuples

        for index, expression in enumerate(expressions["ensures"]):
            text = self.context.expression_text(
                expression, module, model_object.name, bindings, candidate_values
            )
            try:
                passed = self.context.evaluate(
                    expression,
                    module,
                    candidate_states,
                    self.facts,
                    model_object.name,
                    bindings,
                    candidate_values,
                )
            except _UnsupportedExpression as exc:
                checks["ensures"].append(DerivationCheck(text, "unsupported"))
                failure = self._set_failure(
                    "unsupported_feature",
                    f"{path}.ensures[{index}]",
                    "ensures expression is not supported",
                    (exc.feature,),
                )
                return failed_unit(failure)
            checks["ensures"].append(
                DerivationCheck(text, "passed" if passed else "failed")
            )
            if not passed:
                failure = self._set_failure(
                    "ensures_failed",
                    f"{path}.ensures[{index}]",
                    "ensures condition is false",
                )
                return failed_unit(failure)

        staged_facts: set[DerivationFact] = set()
        for index, expression in enumerate(expressions["establishes"]):
            if self.context.relation_call(expression, module) is not None:
                continue
            text = self.context.expression_text(
                expression, module, model_object.name, bindings, candidate_values
            )
            try:
                fact = self.context.normalize_fact(
                    expression,
                    module,
                    model_object.name,
                    bindings,
                    candidate_values,
                )
            except _UnsupportedExpression as exc:
                checks["establishes"].append(DerivationCheck(text, "unsupported"))
                failure = self._set_failure(
                    "unsupported_feature",
                    f"{path}.establishes[{index}]",
                    "establishes only accepts normalizable positive predicate calls",
                    (exc.feature,),
                )
                return failed_unit(failure)
            checks["establishes"].append(
                DerivationCheck(self.context.fact_text(fact), "established")
            )
            staged_facts.add(fact)

        candidate_facts = self.facts | staged_facts
        checked_state_name = (
            self.states[event.target]
            if candidate_state is None
            else candidate_state
        )
        checked_state = next(
            state for state in model_object.states if state.name == checked_state_name
        )
        invariant_expressions = tuple(
            expression for block in checked_state.invariants for expression in block
        )
        for index, expression in enumerate(invariant_expressions):
            text = self.context.expression_text(
                expression, module, model_object.name, bindings, candidate_values
            )
            try:
                passed = self.context.evaluate(
                    expression,
                    module,
                    candidate_states,
                    candidate_facts,
                    model_object.name,
                    bindings,
                    candidate_values,
                )
            except _UnsupportedExpression as exc:
                checks["invariants"].append(DerivationCheck(text, "unsupported"))
                failure = self._set_failure(
                    "unsupported_feature",
                    f"{path}.invariants[{index}]",
                    (
                        "target-state invariant expression is not supported"
                        if candidate_state is not None
                        else "current-state invariant expression is not supported"
                    ),
                    (exc.feature,),
                )
                return failed_unit(failure)
            checks["invariants"].append(
                DerivationCheck(text, "passed" if passed else "failed")
            )
            if not passed:
                failure = self._set_failure(
                    "invariant_failed",
                    f"{path}.invariants[{index}]",
                    (
                        "target-state invariant is false"
                        if candidate_state is not None
                        else "current-state invariant is false"
                    ),
                )
                return failed_unit(failure)

        if candidate_state is not None:
            self.states[event.target] = candidate_state
        self.facts.update(staged_facts)
        self.tuples.update(staged_tuples)
        self.context.query_tuples = self.tuples
        self.values = candidate_values
        for index, signal in enumerate(emit_signals):
            child_event = self._signal_event(signal, model_object.name, bindings)
            emits.append(self.run_unit(child_event, "emit", f"{path}.emits[{index}]"))
            if self.failure is not None:
                break
        if self.failure is None and not defer_resumes:
            for index, signal in enumerate(resume_signals):
                child_event = self._signal_event(signal, model_object.name, bindings)
                resumes.append(
                    self.run_unit(
                        child_event, "resume", f"{path}.resumes[{index}]"
                    )
                )
                if self.failure is not None:
                    break
        if self.failure is None and reset_current:
            self.current_task_ref = event.target
        if self.failure is None and switched_task is not None:
            if self.cpus:
                switch_cpu = self._current_cpu()
                self._bind_task_flow_cpu(switched_task, switch_cpu)
            self.current_task_ref = switched_task
            snapshot = self._runtime_snapshot(
                (
                    "switched",
                    event.source,
                    event.target,
                    event.signal,
                    switched_task,
                )
            )
            if snapshot in self.seen_snapshots:
                self.cycle_closed = True
                last = switches[-1]
                switches[-1] = DerivationSwitch(
                    last.binding,
                    last.task,
                    last.idle_fallback,
                    True,
                    last.after_drives,
                )
            else:
                self.seen_snapshots.add(snapshot)
                for (
                    drive_index,
                    deferred_signals,
                    deferred_bindings,
                ) in deferred_resume_units:
                    resume_unit = drives[drive_index]
                    resumed_units = list(resume_unit.resumes)
                    for resume_index, signal in enumerate(deferred_signals):
                        child_event = self._signal_event(
                            signal,
                            resume_unit.event.target,
                            deferred_bindings,
                        )
                        resumed_units.append(
                            self.run_unit(
                                child_event,
                                "resume",
                                f"{path}.drives[{drive_index}].resumes[{resume_index}]",
                            )
                        )
                        if self.failure is not None:
                            break
                    drives[drive_index] = replace(
                        resume_unit,
                        resumes=tuple(resumed_units),
                    )
                    if self.failure is not None:
                        break
        return self._unit(
            kind=kind,
            event=event,
            before=before,
            handler=handler.signal,
            candidate=candidate_state,
            depends_on=checks["depends_on"],
            drives=drives,
            ensures=checks["ensures"],
            establishes=checks["establishes"],
            invariants=checks["invariants"],
            state_after=candidate_state if candidate_state is not None else before,
            emits=emits,
            status="cycle_closed" if self.cycle_closed else "passed",
            failure=None,
            directives=directives,
            resumes=resumes,
            switches=switches,
            bindings=binding_results,
            relation_effects=relation_effects,
        )


def derive(
    model: ModelIR,
    sequence: DerivationSequence,
    *,
    user_runtime_signals: UserRuntimeSignalProgram | None = None,
) -> DerivationResult:
    """Expand scheduler choices into isolated deterministic derivation paths."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    if not isinstance(sequence, DerivationSequence):
        raise TypeError("sequence must be a DerivationSequence")
    if user_runtime_signals is not None and not isinstance(
        user_runtime_signals, UserRuntimeSignalProgram
    ):
        raise TypeError(
            "user_runtime_signals must be a UserRuntimeSignalProgram or None"
        )

    prefixes: list[tuple[tuple[str, ...], ...]] = [()]
    paths: list[DerivationPath] = []
    while prefixes:
        choices = prefixes.pop()
        execution = _Execution(model, choices, user_runtime_signals)
        unsupported = _unsupported_features(model)
        units: list[DerivationUnit] = []
        if len(execution.schedulers) != 1:
            execution._set_failure(
                "invalid_derivation_line",
                "model",
                "a CPU derivation line requires exactly one sched_core instance",
            )
        elif execution.current_cpu_ref is None:
            execution._set_failure(
                "invalid_derivation_line",
                "model",
                "a CPU derivation line requires its sched_core instance to be owned by one cpu_core",
            )
        elif unsupported:
            execution._set_failure(
                "unsupported_feature",
                "model",
                "model uses semantics that derive cannot execute",
                unsupported,
            )
        else:
            origin = next(
                item for item in model.externals if item.name == model.entry.origin
            )
            declared = tuple(
                DerivationEvent(
                    signal.source,
                    execution.context.resolve_value(
                        signal.target,
                        signal.source[:-1],
                        signal.source,
                        {},
                        execution.values,
                    ),
                    signal.signal,
                    signal.mode,
                    signal.arguments,
                )
                for signal in origin.signals
            )
            try:
                for index, selected in enumerate(sequence.events):
                    unit_path = f"units[{index}]"
                    if selected not in declared:
                        units.append(execution.undeclared_root(selected, unit_path))
                        break
                    units.append(execution.run_unit(selected, "root", unit_path))
                    if execution.failure is not None or execution.cycle_closed:
                        break
            except _SwitchNeeded as needed:
                for candidate in reversed(needed.candidates):
                    prefixes.append(choices + (candidate,))
                continue

        outcome = "passed"
        if execution.cycle_closed:
            outcome = "cycle_closed"
        elif execution.failure is None and any(
            runtime.suspended for runtime in execution.continuations.values()
        ):
            outcome = "yielded"
        elif execution.failure is not None:
            outcome = execution.failure.code
        if execution.failure is not None and execution.failure.code == "panic":
            execution._clear_continuations_for_panic()
        current_cpu_ref = (
            None
            if outcome == "invalid_derivation_line"
            else execution.current_cpu_ref
        )
        paths.append(
            DerivationPath(
                status=outcome,
                units=tuple(units),
                final_state=_final_state(execution.states),
                facts=tuple(execution.facts),
                failure=execution.failure,
                continuations=execution.continuation_snapshots(),
                final_values=execution.final_values(),
                schedulers=()
                if outcome == "invalid_derivation_line"
                else execution.scheduler_snapshots(),
                current_task_ref=execution.current_task_ref,
                current_cpu_ref=current_cpu_ref,
                event_flows=tuple(execution.event_flows),
                interrupt_controls=()
                if outcome == "invalid_derivation_line"
                else execution.interrupt_control_snapshots(),
                tuples=execution.tuple_snapshots(),
            )
        )

    aggregate = (
        "failed"
        if any(path.failure is not None for path in paths)
        else "yielded"
        if any(path.status == "yielded" for path in paths)
        else "passed"
    )
    return DerivationResult(RESULT_SCHEMA_VERSION, aggregate, tuple(paths))
