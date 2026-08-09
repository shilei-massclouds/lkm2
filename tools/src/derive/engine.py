"""Single-threaded deterministic state derivation."""

from __future__ import annotations

import json

from model_ir import ModelExpression, ModelIR, ModelObject, ModelSignal

from .model import (
    RESULT_SCHEMA_VERSION,
    DerivationCheck,
    DerivationEvent,
    DerivationFact,
    DerivationFailure,
    DerivationResult,
    DerivationSequence,
    DerivationState,
    DerivationUnit,
)


class _UnsupportedExpression(Exception):
    def __init__(self, feature: str) -> None:
        super().__init__(feature)
        self.feature = feature


def _event(signal: ModelSignal) -> DerivationEvent:
    return DerivationEvent(signal.source, signal.target, signal.signal, signal.mode)


def _unsupported_features(model: ModelIR) -> tuple[str, ...]:
    features: set[str] = set()
    for model_object in model.objects:
        if model_object.attrs is not None:
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

    def run_unit(self, event: DerivationEvent, kind: str, path: str) -> DerivationUnit:
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

    return DerivationResult(
        RESULT_SCHEMA_VERSION,
        "passed" if execution.failure is None else execution.failure.code,
        tuple(units),
        _final_state(execution.states),
        tuple(execution.facts),
        execution.failure,
    )
