"""Repository-level design checks that are stricter than Model IR validity."""

from __future__ import annotations

from dataclasses import dataclass

from model_ir import ModelExpression, ModelIR


FactKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ClosedSelfValidation:
    """A transition whose claimed output is only checked inside its own object."""

    object: tuple[str, ...]
    signal: tuple[str, ...]
    target_state: tuple[str, ...]
    facts: tuple[FactKey, ...]


def _walk(expression: ModelExpression):
    yield expression
    for child in expression.children:
        yield from _walk(child)


def _receiver_name(expression: ModelExpression) -> str | None:
    cursor = expression
    while cursor.kind in {"member", "path"}:
        cursor = cursor.children[0]
    if cursor.kind != "identifier":
        return None
    return str(cursor.value)


def _fact_keys(
    expression: ModelExpression, predicate_names: frozenset[str]
) -> frozenset[FactKey]:
    keys: set[FactKey] = set()
    for nested in _walk(expression):
        if nested.kind != "call" or not nested.children:
            continue
        callee = nested.children[0]
        if callee.kind == "identifier" and callee.value in predicate_names:
            keys.add(("predicate", str(callee.value)))
            continue
        if callee.kind != "member" or callee.value != "contains":
            continue
        receiver = _receiver_name(callee.children[0])
        if receiver is not None:
            keys.add(("contains", receiver))
    return frozenset(keys)


def find_closed_self_validations(model: ModelIR) -> tuple[ClosedSelfValidation, ...]:
    """Find producer/consumer loops with no independently owned fact boundary.

    A finding requires a transition to establish a fact that its target-state
    invariant reads. It is suppressed only when that invariant is also backed
    by a fact produced by another object, or when another object consumes the
    transition's fact. This intentionally remains a repository design lint:
    small engine fixtures may model self-validation to exercise rollback.
    """

    predicate_names = frozenset(
        predicate.name[-1]
        for module in model.modules
        for predicate in module.predicates
    )
    producers: dict[FactKey, set[tuple[str, ...]]] = {}
    consumers: dict[FactKey, set[tuple[str, ...]]] = {}

    for model_object in model.objects:
        for state in model_object.states:
            for invariant_block in state.invariants:
                for expression in invariant_block:
                    for key in _fact_keys(expression, predicate_names):
                        consumers.setdefault(key, set()).add(model_object.name)
            for handler in (*state.transitions, *state.actions):
                for block in handler.blocks:
                    if block.kind == "establishes":
                        for expression in block.expressions:
                            for key in _fact_keys(expression, predicate_names):
                                producers.setdefault(key, set()).add(model_object.name)
                    elif block.kind in {"depends_on", "ensures"}:
                        for expression in block.expressions:
                            for key in _fact_keys(expression, predicate_names):
                                consumers.setdefault(key, set()).add(model_object.name)

    findings: list[ClosedSelfValidation] = []
    for model_object in model.objects:
        states = {state.name: state for state in model_object.states}
        for source_state in model_object.states:
            for transition in source_state.transitions:
                target_state = states[transition.target_state]
                established = frozenset(
                    key
                    for block in transition.blocks
                    if block.kind == "establishes"
                    for expression in block.expressions
                    for key in _fact_keys(expression, predicate_names)
                )
                invariant_reads = frozenset(
                    key
                    for invariant_block in target_state.invariants
                    for expression in invariant_block
                    for key in _fact_keys(expression, predicate_names)
                )
                self_checked = established & invariant_reads
                if not self_checked:
                    continue
                has_external_backing = any(
                    producer != model_object.name
                    for key in invariant_reads - established
                    for producer in producers.get(key, ())
                )
                has_external_consumer = any(
                    consumer != model_object.name
                    for key in self_checked
                    for consumer in consumers.get(key, ())
                )
                if has_external_backing or has_external_consumer:
                    continue
                findings.append(
                    ClosedSelfValidation(
                        object=model_object.name,
                        signal=transition.signal,
                        target_state=transition.target_state,
                        facts=tuple(sorted(self_checked)),
                    )
                )
    return tuple(findings)
