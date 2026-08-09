"""Single-threaded deterministic state derivation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from model_ir import ModelAction, ModelExpression, ModelIR, ModelObject, ModelSignal

from .model import (
    RESULT_SCHEMA_VERSION,
    DerivationCheck,
    DerivationContinuation,
    DerivationEvent,
    DerivationFact,
    DerivationFailure,
    DerivationFrame,
    DerivationResult,
    DerivationSequence,
    DerivationState,
    DerivationUnit,
    DerivationYieldToken,
)


@dataclass(slots=True)
class _ResumeFrame:
    handler: tuple[str, ...]
    control_index: int = 0
    entered: bool = False


@dataclass(slots=True)
class _ContinuationRuntime:
    object: tuple[str, ...]
    frames: list[_ResumeFrame] = field(default_factory=list)
    generation: int = 0
    token: DerivationYieldToken | None = None
    executing: bool = False
    completed: bool = False
    waiting_yield_target: bool = False


class _UnsupportedExpression(Exception):
    def __init__(self, feature: str) -> None:
        super().__init__(feature)
        self.feature = feature


def _event(signal: ModelSignal) -> DerivationEvent:
    return DerivationEvent(signal.source, signal.target, signal.signal, signal.mode)


def _unsupported_features(model: ModelIR) -> tuple[str, ...]:
    features: set[str] = set()
    for model_object in model.objects:
        if model_object.attrs:
            features.add("attrs")
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
        predicates = tuple(
            item for module in model.modules for item in module.predicates
        )
        self.predicates = {item.name: item for item in predicates}
        self.object_names = _shortest_names(tuple(self.objects))
        self.predicate_names = _shortest_names(tuple(self.predicates))

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
        self, expression: ModelExpression, module: tuple[str, ...]
    ) -> str:
        if expression.kind == "integer":
            return str(expression.value)
        if expression.kind == "string":
            return json.dumps(expression.value, ensure_ascii=False)
        if expression.kind == "identifier" and expression.value in {"true", "false"}:
            return str(expression.value)
        state = self.state_reference(expression)
        if state is not None:
            return "::".join(state)
        model_object = self.object_reference(expression, module)
        if model_object is not None:
            return self.object_names[model_object]
        flattened = _access(expression)
        if flattened is not None and all(op == "path" for op in flattened[1]):
            return "::".join(flattened[0])
        if expression.kind == "identifier":
            return str(expression.value)
        raise _UnsupportedExpression(f"fact_argument:{expression.kind}")

    def normalize_fact(
        self, expression: ModelExpression, module: tuple[str, ...]
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
            self.normalize_term(argument, module) for argument in expression.children[1:]
        )
        if len(arguments) != len(predicate.parameters):
            raise _UnsupportedExpression("predicate_arity")
        return DerivationFact(predicate_name, arguments)

    def fact_text(self, fact: DerivationFact) -> str:
        name = self.predicate_names[fact.predicate]
        return f"{name}({', '.join(fact.arguments)})"

    def expression_text(
        self, expression: ModelExpression, module: tuple[str, ...]
    ) -> str:
        if expression.kind == "call":
            try:
                return self.fact_text(self.normalize_fact(expression, module))
            except _UnsupportedExpression:
                pass
        if expression.kind == "binary" and expression.value in {"==", "!="}:
            left, right = expression.children
            left_object = self.object_reference(left, module)
            right_object = self.object_reference(right, module)
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
                f"({self.expression_text(expression.children[0], module)} "
                f"{expression.value} "
                f"{self.expression_text(expression.children[1], module)})"
            )
        if expression.kind == "unary" and expression.value == "!":
            return f"!{self.expression_text(expression.children[0], module)}"
        return _format_expression(expression)

    def evaluate(
        self,
        expression: ModelExpression,
        module: tuple[str, ...],
        states: dict[tuple[str, ...], tuple[str, ...] | None],
        facts: set[DerivationFact],
    ) -> bool:
        if expression.kind == "identifier" and expression.value in {"true", "false"}:
            return expression.value == "true"
        if expression.kind == "call":
            return self.normalize_fact(expression, module) in facts
        if expression.kind == "unary" and expression.value == "!":
            return not self.evaluate(expression.children[0], module, states, facts)
        if expression.kind == "binary" and expression.value in {"&&", "||"}:
            left = self.evaluate(expression.children[0], module, states, facts)
            right = self.evaluate(expression.children[1], module, states, facts)
            return left and right if expression.value == "&&" else left or right
        if expression.kind == "binary" and expression.value in {"==", "!="}:
            left, right = expression.children
            left_object = self.object_reference(left, module)
            right_object = self.object_reference(right, module)
            left_state = self.state_reference(left)
            right_state = self.state_reference(right)
            if left_object is not None and right_state is not None:
                equal = states[left_object] == right_state
                return equal if expression.value == "==" else not equal
            if right_object is not None and left_state is not None:
                equal = states[right_object] == left_state
                return equal if expression.value == "==" else not equal
        feature = f"expression:{expression.kind}"
        if expression.kind in {"unary", "binary"}:
            feature += f":{expression.value}"
        raise _UnsupportedExpression(feature)


class _Execution:
    def __init__(self, model: ModelIR) -> None:
        self.context = _Context(model)
        self.states = _states(model)
        self.facts: set[DerivationFact] = set()
        self.failure: DerivationFailure | None = None
        self.continuations: dict[tuple[str, ...], _ContinuationRuntime] = {}
        for model_object in model.objects:
            if model_object.continuation:
                self.continuations[model_object.name] = _ContinuationRuntime(
                    model_object.name,
                    [_ResumeFrame(("Action", "Enter"))],
                )

    def continuation_snapshots(self) -> tuple[DerivationContinuation, ...]:
        return tuple(
            DerivationContinuation(
                runtime.object,
                runtime.generation,
                tuple(
                    DerivationFrame(
                        runtime.object,
                        frame.handler,
                        frame.control_index,
                        runtime.generation,
                    )
                    for frame in runtime.frames
                ),
                runtime.token,
            )
            for runtime in self.continuations.values()
            if runtime.frames
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
        yield_token_created: DerivationYieldToken | None = None,
        yield_token_consumed: DerivationYieldToken | None = None,
    ) -> DerivationUnit:
        return DerivationUnit(
            kind,
            event,
            before,
            handler,
            candidate,
            tuple(depends_on),
            tuple(drives),
            tuple(ensures),
            tuple(establishes),
            tuple(invariants),
            state_after,
            tuple(emits),
            status,
            failure,
            () if yields is None else tuple(yields),
            yield_token_created,
            yield_token_consumed,
        )

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
    def _control_signals(handler: ModelAction) -> tuple[ModelSignal, ...]:
        return tuple(
            signal
            for block in handler.blocks
            if block.kind in {"drives", "yields"}
            for signal in block.signals
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
            runtime = self.continuations[event.target]
            if event.signal != ("Action", "Enter"):
                return self._set_failure(
                    "invalid_continuation_action",
                    f"{path}.handler",
                    "a suspended continuation can only be entered through Action::Enter",
                )
            if runtime.executing:
                return self._set_failure(
                    "continuation_reentry",
                    f"{path}.handler",
                    "continuation cannot be re-entered before the current target settles",
                )
            if runtime.waiting_yield_target:
                return self._set_failure(
                    "continuation_reentry",
                    f"{path}.handler",
                    "yield target cannot synchronously re-enter the suspended continuation",
                )
            if not runtime.frames:
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

    def _run_continuation(
        self, event: DerivationEvent, kind: str, path: str
    ) -> DerivationUnit:
        runtime = self.continuations[event.target]
        before = self.states[event.target]
        if event.signal != ("Action", "Enter"):
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "invalid_continuation_action",
                "only Action::Enter can externally start or resume a continuation",
            )
        if runtime.executing:
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "continuation_reentry",
                "continuation is already executing",
            )
        if runtime.waiting_yield_target and kind != "emit":
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "continuation_reentry",
                "yield target cannot synchronously re-enter the suspended continuation",
            )
        if not runtime.frames:
            return self._continuation_failure_unit(
                event,
                kind,
                f"{path}.handler",
                "no_resumable_continuation",
                "continuation has exited and has no resumable frame",
            )

        consumed = runtime.token
        runtime.token = None
        runtime.executing = True
        root_frame = runtime.frames[0]
        root_handler = root_frame.handler
        drives: list[DerivationUnit] = []
        yielded_units: list[DerivationUnit] = []
        emits: list[DerivationUnit] = []
        response_checks: dict[
            int, dict[str, list[DerivationCheck]]
        ] = {}

        def checks_for(frame: _ResumeFrame) -> dict[str, list[DerivationCheck]]:
            return response_checks.setdefault(
                id(frame),
                {
                    "depends_on": [],
                    "ensures": [],
                    "establishes": [],
                    "invariants": [],
                },
            )

        def failed(
            frame: _ResumeFrame,
            handler: ModelAction,
            failure: DerivationFailure,
        ) -> DerivationUnit:
            runtime.executing = False
            checks = checks_for(frame)
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=root_handler,
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
                yield_token_consumed=consumed,
            )

        while runtime.frames:
            frame = runtime.frames[-1]
            model_object = self.context.objects[runtime.object]
            state = model_object.states[0]
            handler = next(
                action for action in state.actions if action.signal == frame.handler
            )
            checks = checks_for(frame)
            module = model_object.name[:-1]
            if not frame.entered:
                depends = tuple(
                    expression
                    for block in handler.blocks
                    if block.kind == "depends_on"
                    for expression in block.expressions
                )
                for index, expression in enumerate(depends):
                    text = self.context.expression_text(expression, module)
                    try:
                        passed = self.context.evaluate(
                            expression, module, self.states, self.facts
                        )
                    except _UnsupportedExpression as exc:
                        checks["depends_on"].append(
                            DerivationCheck(text, "unsupported")
                        )
                        return failed(
                            frame,
                            handler,
                            self._set_failure(
                                "unsupported_feature",
                                f"{path}.depends_on[{index}]",
                                "depends_on expression is not supported",
                                (exc.feature,),
                            ),
                        )
                    checks["depends_on"].append(
                        DerivationCheck(text, "passed" if passed else "failed")
                    )
                    if not passed:
                        return failed(
                            frame,
                            handler,
                            self._set_failure(
                                "depends_on_failed",
                                f"{path}.depends_on[{index}]",
                                "depends_on condition is false",
                            ),
                        )
                frame.entered = True

            controls = self._control_signals(handler)
            if frame.control_index < len(controls):
                signal = controls[frame.control_index]
                child_event = _event(signal)
                child_path = (
                    f"{path}.yields[{len(yielded_units)}]"
                    if signal.mode == "yield"
                    else f"{path}.drives[{len(drives)}]"
                )
                if signal.mode == "yield":
                    preflight = self._preflight_yield(child_event, child_path)
                    if preflight is not None:
                        return failed(frame, handler, preflight)
                    frame.control_index += 1
                    runtime.generation += 1
                    token = DerivationYieldToken(
                        runtime.object, runtime.generation
                    )
                    runtime.token = token
                    # The suspension is stable before the target begins executing.
                    runtime.executing = False
                    runtime.waiting_yield_target = True
                    yielded_units.append(self.run_unit(child_event, "yield", child_path))
                    runtime.waiting_yield_target = False
                    root_checks = checks_for(root_frame)
                    return self._unit(
                        kind=kind,
                        event=event,
                        before=before,
                        handler=root_handler,
                        candidate=None,
                        depends_on=root_checks["depends_on"],
                        drives=drives,
                        ensures=root_checks["ensures"],
                        establishes=root_checks["establishes"],
                        invariants=root_checks["invariants"],
                        state_after=None,
                        emits=emits,
                        status="yielded",
                        failure=None,
                        yields=yielded_units,
                        yield_token_created=token,
                        yield_token_consumed=consumed,
                    )

                frame.control_index += 1
                if (
                    child_event.target == runtime.object
                    and child_event.signal[0] == "Action"
                ):
                    if child_event.signal == ("Action", "Enter"):
                        return failed(
                            frame,
                            handler,
                            self._set_failure(
                                "continuation_reentry",
                                f"{child_path}.handler",
                                "a running continuation cannot synchronously call Action::Enter",
                            ),
                        )
                    child_handler = next(
                        (
                            action
                            for action in state.actions
                            if action.signal == child_event.signal
                        ),
                        None,
                    )
                    if child_handler is None:
                        return failed(
                            frame,
                            handler,
                            self._set_failure(
                                "unhandled_signal",
                                f"{child_path}.handler",
                                "continuation has no matching nested Action handler",
                            ),
                        )
                    runtime.frames.append(_ResumeFrame(child_event.signal))
                    continue
                drives.append(self.run_unit(child_event, "drive", child_path))
                if self.failure is not None:
                    runtime.executing = False
                    return self._unit(
                        kind=kind,
                        event=event,
                        before=before,
                        handler=root_handler,
                        candidate=None,
                        depends_on=checks["depends_on"],
                        drives=drives,
                        ensures=checks["ensures"],
                        establishes=checks["establishes"],
                        invariants=checks["invariants"],
                        state_after=None,
                        emits=emits,
                        status="stopped",
                        failure=None,
                        yield_token_consumed=consumed,
                    )
                continue

            ensures = tuple(
                expression
                for block in handler.blocks
                if block.kind == "ensures"
                for expression in block.expressions
            )
            for index, expression in enumerate(ensures):
                text = self.context.expression_text(expression, module)
                try:
                    passed = self.context.evaluate(
                        expression, module, self.states, self.facts
                    )
                except _UnsupportedExpression as exc:
                    checks["ensures"].append(DerivationCheck(text, "unsupported"))
                    return failed(
                        frame,
                        handler,
                        self._set_failure(
                            "unsupported_feature",
                            f"{path}.ensures[{index}]",
                            "ensures expression is not supported",
                            (exc.feature,),
                        ),
                    )
                checks["ensures"].append(
                    DerivationCheck(text, "passed" if passed else "failed")
                )
                if not passed:
                    return failed(
                        frame,
                        handler,
                        self._set_failure(
                            "ensures_failed",
                            f"{path}.ensures[{index}]",
                            "ensures condition is false",
                        ),
                    )

            staged_facts: set[DerivationFact] = set()
            establishes = tuple(
                expression
                for block in handler.blocks
                if block.kind == "establishes"
                for expression in block.expressions
            )
            for index, expression in enumerate(establishes):
                text = self.context.expression_text(expression, module)
                try:
                    fact = self.context.normalize_fact(expression, module)
                except _UnsupportedExpression as exc:
                    checks["establishes"].append(
                        DerivationCheck(text, "unsupported")
                    )
                    return failed(
                        frame,
                        handler,
                        self._set_failure(
                            "unsupported_feature",
                            f"{path}.establishes[{index}]",
                            "establishes only accepts normalizable positive predicate calls",
                            (exc.feature,),
                        ),
                    )
                checks["establishes"].append(
                    DerivationCheck(self.context.fact_text(fact), "established")
                )
                staged_facts.add(fact)

            candidate_facts = self.facts | staged_facts
            invariant_expressions = tuple(
                expression for block in state.invariants for expression in block
            )
            for index, expression in enumerate(invariant_expressions):
                text = self.context.expression_text(expression, module)
                try:
                    passed = self.context.evaluate(
                        expression, module, self.states, candidate_facts
                    )
                except _UnsupportedExpression as exc:
                    checks["invariants"].append(
                        DerivationCheck(text, "unsupported")
                    )
                    return failed(
                        frame,
                        handler,
                        self._set_failure(
                            "unsupported_feature",
                            f"{path}.invariants[{index}]",
                            "current-state invariant expression is not supported",
                            (exc.feature,),
                        ),
                    )
                checks["invariants"].append(
                    DerivationCheck(text, "passed" if passed else "failed")
                )
                if not passed:
                    return failed(
                        frame,
                        handler,
                        self._set_failure(
                            "invariant_failed",
                            f"{path}.invariants[{index}]",
                            "current-state invariant is false",
                        ),
                    )
            self.facts.update(staged_facts)

            completed_frame = runtime.frames.pop()
            completed_event = DerivationEvent(
                runtime.object,
                runtime.object,
                completed_frame.handler,
                "drive",
            )
            completed_emits: list[DerivationUnit] = []
            for block in handler.blocks:
                if block.kind != "emits":
                    continue
                for signal in block.signals:
                    emitted = self.run_unit(
                        _event(signal),
                        "emit",
                        f"{path}.emits[{len(completed_emits)}]",
                    )
                    completed_emits.append(emitted)
                    if self.failure is not None:
                        break
            if runtime.frames:
                drives.append(
                    self._unit(
                        kind="drive",
                        event=completed_event,
                        before=before,
                        handler=completed_frame.handler,
                        candidate=None,
                        depends_on=checks["depends_on"],
                        drives=[],
                        ensures=checks["ensures"],
                        establishes=checks["establishes"],
                        invariants=checks["invariants"],
                        state_after=before,
                        emits=completed_emits,
                        status="passed",
                        failure=None,
                    )
                )
                continue

            runtime.completed = True
            runtime.executing = False
            return self._unit(
                kind=kind,
                event=event,
                before=before,
                handler=root_handler,
                candidate=None,
                depends_on=checks["depends_on"],
                drives=drives,
                ensures=checks["ensures"],
                establishes=checks["establishes"],
                invariants=checks["invariants"],
                state_after=before,
                emits=completed_emits,
                status="passed",
                failure=None,
                yield_token_consumed=consumed,
            )

        raise RuntimeError("continuation execution exhausted without a result")

    def run_unit(self, event: DerivationEvent, kind: str, path: str) -> DerivationUnit:
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
        drive_events = tuple(
            _event(signal)
            for block in blocks
            if block.kind == "drives"
            for signal in block.signals
        )
        emit_events = tuple(
            _event(signal)
            for block in blocks
            if block.kind == "emits"
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
            )

        for index, expression in enumerate(expressions["depends_on"]):
            text = self.context.expression_text(expression, module)
            try:
                passed = self.context.evaluate(
                    expression, module, self.states, self.facts
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

        for index, child_event in enumerate(drive_events):
            drives.append(self.run_unit(child_event, "drive", f"{path}.drives[{index}]"))
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
                )

        for index, expression in enumerate(expressions["ensures"]):
            text = self.context.expression_text(expression, module)
            try:
                passed = self.context.evaluate(
                    expression, module, self.states, self.facts
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
            text = self.context.expression_text(expression, module)
            try:
                fact = self.context.normalize_fact(expression, module)
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

        candidate_states = dict(self.states)
        if candidate_state is not None:
            candidate_states[event.target] = candidate_state
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
            text = self.context.expression_text(expression, module)
            try:
                passed = self.context.evaluate(
                    expression, module, candidate_states, candidate_facts
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
        for index, child_event in enumerate(emit_events):
            emits.append(self.run_unit(child_event, "emit", f"{path}.emits[{index}]"))
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
            status="passed",
            failure=None,
        )


def derive(model: ModelIR, sequence: DerivationSequence) -> DerivationResult:
    """Execute selected external roots and automatically schedule their causal units."""

    if not isinstance(model, ModelIR):
        raise TypeError("model must be a ModelIR")
    if not isinstance(sequence, DerivationSequence):
        raise TypeError("sequence must be a DerivationSequence")

    execution = _Execution(model)
    unsupported = _unsupported_features(model)
    if unsupported:
        failure = execution._set_failure(
            "unsupported_feature",
            "model",
            "model uses semantics that derive cannot execute",
            unsupported,
        )
        return DerivationResult(
            RESULT_SCHEMA_VERSION,
            failure.code,
            (),
            _final_state(execution.states),
            (),
            failure,
            execution.continuation_snapshots(),
        )

    origin = next(item for item in model.externals if item.name == model.entry.origin)
    declared = tuple(_event(signal) for signal in origin.signals)
    units: list[DerivationUnit] = []
    for index, selected in enumerate(sequence.events):
        path = f"units[{index}]"
        if selected not in declared:
            units.append(execution.undeclared_root(selected, path))
            break
        units.append(execution.run_unit(selected, "root", path))
        if execution.failure is not None:
            break

    outcome = "passed"
    if execution.failure is None and any(
        runtime.token is not None for runtime in execution.continuations.values()
    ):
        outcome = "yielded"
    return DerivationResult(
        RESULT_SCHEMA_VERSION,
        outcome if execution.failure is None else execution.failure.code,
        tuple(units),
        _final_state(execution.states),
        tuple(execution.facts),
        execution.failure,
        execution.continuation_snapshots(),
    )
