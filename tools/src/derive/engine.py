"""Single-threaded deterministic state derivation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json

from model_ir import (
    ModelAction,
    ModelExpression,
    ModelHandlerBlock,
    ModelIR,
    ModelObject,
    ModelSignal,
)

from .model import (
    RESULT_SCHEMA_VERSION,
    DerivationCheck,
    DerivationBinding,
    DerivationContinuation,
    DerivationDirective,
    DerivationEvent,
    DerivationFact,
    DerivationFailure,
    DerivationFrame,
    DerivationPath,
    DerivationResult,
    DerivationScheduler,
    DerivationSwitch,
    DerivationSequence,
    DerivationState,
    DerivationUnit,
    DerivationValue,
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
    bindings: dict[str, tuple[str, ...]] = field(default_factory=dict)
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
    current_task: tuple[str, ...]
    runq: list[tuple[str, ...]] = field(default_factory=list)


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
        current = self._type_name(model_object.base_type.name, name[:-1])
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
        bindings: dict[str, tuple[str, ...]],
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]],
    ) -> tuple[str, ...] | None:
        if expression.kind == "identifier":
            identifier = str(expression.value)
            if identifier in bindings:
                return bindings[identifier]
            if identifier == "self":
                return source
            if identifier == "CurrentTaskRef":
                schedulers = (
                    (source,)
                    if source in self.schedulers
                    else tuple(self.schedulers)
                )
                if len(schedulers) != 1:
                    raise _UnsupportedExpression("CurrentTaskRef:ambiguous_scheduler")
                return self.schedulers[schedulers[0]].current_task
        flattened = _access(expression)
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
        bindings: dict[str, tuple[str, ...]],
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]],
    ) -> DerivationEvent:
        arguments: list[ModelExpression] = []
        for argument in signal.arguments:
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
            signal.source,
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
        bindings: dict[str, tuple[str, ...]] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> str:
        bindings = {} if bindings is None else bindings
        values = {} if values is None else values
        if expression.kind == "integer":
            return str(expression.value)
        if expression.kind == "string":
            return json.dumps(expression.value, ensure_ascii=False)
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
        bindings: dict[str, tuple[str, ...]] | None = None,
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
        bindings: dict[str, tuple[str, ...]] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> str:
        bindings = {} if bindings is None else bindings
        values = {} if values is None else values
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
        bindings: dict[str, tuple[str, ...]] | None = None,
        values: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None,
    ) -> bool:
        bindings = {} if bindings is None else bindings
        values = {} if values is None else values
        if expression.kind == "identifier" and expression.value in {"true", "false"}:
            return expression.value == "true"
        if expression.kind == "call":
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


class _Execution:
    def __init__(
        self,
        model: ModelIR,
        switch_choices: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        self.context = _Context(model)
        self.states = _states(model)
        self.facts: set[DerivationFact] = set()
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
        for model_object in model.objects:
            if model_object.base_type.name == ("Collection",):
                self.collections[model_object.name] = []
        for model_object in model.objects:
            for model_field in model_object.attrs or ():
                if model_field.default is None:
                    continue
                value = self.context.resolve_value(
                    model_field.default,
                    model_object.name[:-1],
                    model_object.name,
                    {},
                    self.values,
                )
                if value is None:
                    raise RuntimeError(
                        f"compiled field default {model_field.name!r} is unresolved"
                    )
                self.values[(model_object.name, model_field.name)] = value
        self.schedulers: dict[tuple[str, ...], _SchedulerRuntime] = {}
        for model_object in model.objects:
            if model_object.idle_task is None:
                continue
            idle = self.context.object_reference(
                model_object.idle_task, model_object.name[:-1]
            )
            if idle is None:
                raise RuntimeError("compiled scheduler idle_task is unresolved")
            self.schedulers[model_object.name] = _SchedulerRuntime(
                model_object.name, idle, idle
            )
        self.context.schedulers = self.schedulers
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
                model_object.parent, model_object.name[:-1]
            )
            if parent is not None and parent in self.context.objects:
                parent_object = self.context.objects[parent]
                if self.context.object_has_type(parent_object.name, "Task"):
                    self.task_flows[parent] = model_object.name

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

    def scheduler_snapshots(self) -> tuple[DerivationScheduler, ...]:
        return tuple(
            DerivationScheduler(
                runtime.scheduler,
                runtime.idle_task,
                runtime.current_task,
                tuple(runtime.runq),
            )
            for runtime in self.schedulers.values()
        )

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
            tuple(sorted(self.values.items())),
            tuple((name, tuple(values)) for name, values in sorted(self.collections.items())),
            tuple(
                (
                    name,
                    runtime.idle_task,
                    runtime.current_task,
                    tuple(runtime.runq),
                )
                for name, runtime in sorted(self.schedulers.items())
            ),
            continuations,
        )

    def _bind_handler(
        self,
        event: DerivationEvent,
        handler: object,
    ) -> dict[str, tuple[str, ...]]:
        parameters = handler.parameters
        if len(parameters) != len(event.arguments):
            return {}
        result: dict[str, tuple[str, ...]] = {}
        for parameter, argument in zip(parameters, event.arguments, strict=True):
            value = self.context.resolve_value(
                argument,
                event.target[:-1],
                event.target,
                {},
                self.values,
            )
            if value is None:
                raise _UnsupportedExpression(
                    f"handler_argument:{parameter.name}"
                )
            result[parameter.name] = value
        return result

    def _signal_event(
        self,
        signal: ModelSignal,
        source: tuple[str, ...],
        bindings: dict[str, tuple[str, ...]],
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
        bindings: dict[str, tuple[str, ...]],
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
                    model_object.name[:-1],
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
            module = model_object.name[:-1]

            if not frame.entered:
                depends = tuple(
                    expression
                    for block in handler.blocks
                    if block.kind == "depends_on"
                    for expression in block.expressions
                )
                for index, expression in enumerate(depends):
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
                        frame.depends_on.append(DerivationCheck(text, "unsupported"))
                        failure = self._set_failure(
                            "unsupported_feature",
                            f"{path}.depends_on[{index}]",
                            "depends_on expression is not supported",
                            (exc.feature,),
                        )
                        self._abort_frame(runtime, failure.code, failure)
                        return
                    frame.depends_on.append(
                        DerivationCheck(text, "passed" if passed else "failed")
                    )
                    if not passed:
                        failure = self._set_failure(
                            "depends_on_failed",
                            f"{path}.depends_on[{index}]",
                            "depends_on condition is false",
                        )
                        self._abort_frame(runtime, failure.code, failure)
                        return
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
                if self.context.objects[child_event.target].continuation:
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

            try:
                candidate_values = self._candidate_values(
                    model_object, handler, frame.bindings
                )
            except _UnsupportedExpression as exc:
                failure = self._set_failure(
                    "invalid_current_task_ref"
                    if exc.feature.startswith("CurrentTaskRef")
                    else "unsupported_feature",
                    f"{path}.updates",
                    "updates value cannot be resolved",
                    (exc.feature,),
                )
                self._abort_frame(runtime, failure.code, failure)
                return

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
                for block in handler.blocks:
                    if self.failure is not None:
                        break
                    if block.kind != "resumes":
                        continue
                    for signal in block.signals:
                        completed.resumes.append(
                            self.run_unit(
                                self._signal_event(
                                    signal, model_object.name, completed.bindings
                                ),
                                "resume",
                                f"{path}.resumes[{len(completed.resumes)}]",
                            )
                        )
                        if self.failure is not None:
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
        if event.signal != ("Action", "Enter"):
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "invalid_continuation_action",
                "only Action::Enter can externally start or resume a continuation",
            )
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
                    "continuation has no Action::Enter handler",
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

    def run_unit(
        self,
        event: DerivationEvent,
        kind: str,
        path: str,
        *,
        defer_task_flow: bool = False,
    ) -> DerivationUnit:
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
        switched_task: tuple[str, ...] | None = None
        deferred_task_resumes: list[int] = []
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
            )

        module = model_object.name[:-1]
        try:
            bindings = self._bind_handler(event, handler)
        except _UnsupportedExpression as exc:
            failure = self._set_failure(
                "invalid_current_task_ref"
                if exc.feature.startswith("CurrentTaskRef")
                else "unsupported_feature",
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
            )

        for index, expression in enumerate(expressions["depends_on"]):
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
                failure = self._set_failure(
                    "unsupported_feature",
                    f"{path}.depends_on[{index}]",
                    "depends_on expression is not supported",
                    (exc.feature,),
                )
                return failed_unit(failure)
            checks["depends_on"].append(
                DerivationCheck(text, "passed" if passed else "failed")
            )
            if not passed:
                failure = self._set_failure(
                    "depends_on_failed",
                    f"{path}.depends_on[{index}]",
                    "depends_on condition is false",
                )
                return failed_unit(failure)

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
                bindings[control.switches] = switched_task
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
                        "invalid_current_task_ref"
                        if exc.feature.startswith("CurrentTaskRef")
                        else "unsupported_feature",
                        f"{path}.drives[{len(drives)}].event.arguments",
                        "signal argument cannot be resolved",
                        (exc.feature,),
                    )
                )
            defer_child_task_flow = (
                switched_task is not None
                and child_event.target == switched_task
                and child_event.signal == ("Transition", "Resume")
            )
            drive_index = len(drives)
            drives.append(
                self.run_unit(
                    child_event,
                    "drive",
                    f"{path}.drives[{drive_index}]",
                    defer_task_flow=defer_child_task_flow,
                )
            )
            if defer_child_task_flow:
                deferred_task_resumes.append(drive_index)
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
                )

        candidate_states = dict(self.states)
        if candidate_state is not None:
            candidate_states[event.target] = candidate_state
        try:
            candidate_values = self._candidate_values(
                model_object, handler, bindings
            )
        except _UnsupportedExpression as exc:
            return failed_unit(
                self._set_failure(
                    "invalid_current_task_ref"
                    if exc.feature.startswith("CurrentTaskRef")
                    else "unsupported_feature",
                    f"{path}.updates",
                    "updates value cannot be resolved",
                    (exc.feature,),
                )
            )

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
        self.values = candidate_values
        if switched_task is not None:
            self.schedulers[event.target].current_task = switched_task
        for index, signal in enumerate(emit_signals):
            child_event = self._signal_event(signal, model_object.name, bindings)
            emits.append(self.run_unit(child_event, "emit", f"{path}.emits[{index}]"))
            if self.failure is not None:
                break
        if self.failure is None:
            for index, signal in enumerate(resume_signals):
                child_event = self._signal_event(signal, model_object.name, bindings)
                resumes.append(
                    self.run_unit(
                        child_event, "resume", f"{path}.resumes[{index}]"
                    )
                )
                if self.failure is not None:
                    break
        if self.failure is None and switched_task is not None:
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
                for drive_index in deferred_task_resumes:
                    flow = self.task_flows[switched_task]
                    flow_unit = self.run_unit(
                        DerivationEvent(
                            switched_task,
                            flow,
                            ("Action", "Enter"),
                            "resume",
                        ),
                        "resume",
                        f"{path}.drives[{drive_index}].resumes[0]",
                    )
                    drives[drive_index] = replace(
                        drives[drive_index],
                        resumes=drives[drive_index].resumes + (flow_unit,),
                    )
                    if self.failure is not None:
                        break
        if (
            self.failure is None
            and not defer_task_flow
            and event.signal == ("Transition", "Resume")
            and self.context.object_has_type(event.target, "Task")
        ):
            flow = self.task_flows[event.target]
            resumes.append(
                self.run_unit(
                    DerivationEvent(
                        event.target,
                        flow,
                        ("Action", "Enter"),
                        "resume",
                    ),
                    "resume",
                    f"{path}.resumes[{len(resumes)}]",
                )
            )
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
        )


def derive(model: ModelIR, sequence: DerivationSequence) -> DerivationResult:
    """Expand scheduler choices into isolated deterministic derivation paths."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    if not isinstance(sequence, DerivationSequence):
        raise TypeError("sequence must be a DerivationSequence")

    prefixes: list[tuple[tuple[str, ...], ...]] = [()]
    paths: list[DerivationPath] = []
    while prefixes:
        choices = prefixes.pop()
        execution = _Execution(model, choices)
        unsupported = _unsupported_features(model)
        units: list[DerivationUnit] = []
        if unsupported:
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
        paths.append(
            DerivationPath(
                outcome,
                tuple(units),
                _final_state(execution.states),
                tuple(execution.facts),
                execution.failure,
                execution.continuation_snapshots(),
                execution.final_values(),
                execution.scheduler_snapshots(),
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
