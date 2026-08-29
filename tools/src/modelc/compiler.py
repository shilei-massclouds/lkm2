"""Compilation pipeline from a model-root specification to Model IR v13."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from lark import Token, Tree

from model_ir import (
    SCHEMA_VERSION,
    ModelAction,
    ModelBinding,
    ModelDeferred,
    ModelEntry,
    ModelExpression,
    ModelExternal,
    ModelField,
    ModelHandlerBlock,
    ModelIR,
    ModelIRValidationError,
    ModelModule,
    ModelObject,
    ModelParameter,
    ModelPredicate,
    ModelReference,
    ModelReferenceAssignment,
    ModelSignal,
    ModelState,
    ModelTransition,
    ModelType,
    ModelTypeExpression,
    ModelUpdate,
    canonicalize_signal_name,
)

from .ast import ModelSpec
from .diagnostics import error
from .module_loader import LoadedModule, load_module_sources, resolve_use_name
from .parser import parse_spec


def _read_entry(path: Path) -> tuple[ModelSpec, tuple[LoadedModule, ...]]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise error(path, 1, 1, "source is not valid UTF-8") from exc
    except OSError as exc:
        raise error(path, 1, 1, exc.strerror or str(exc)) from exc
    document = parse_spec(source, path)
    modules = load_module_sources(path, document)
    origin_module = document.origin.name.parts[:-1]
    if origin_module not in {module.name for module in modules}:
        span = document.origin.name.span
        rendered = ".".join(origin_module) or "<model>"
        raise error(
            path,
            span.start_line,
            span.start_column,
            f"origin module {rendered!r} is not declared",
        )
    return document, modules


def _semantic_error(module: LoadedModule, node: Tree | Token, message: str) -> Exception:
    if isinstance(node, Tree):
        line = node.meta.line
        column = node.meta.column
    else:
        line = node.line
        column = node.column
    return error(module.path, line, column, message)


def _tree_children(node: Tree, rule: str) -> list[Tree]:
    return [child for child in node.children if isinstance(child, Tree) and child.data == rule]


def _only(
    module: LoadedModule, owner: Tree, rule: str, label: str
) -> Tree | None:
    values = _tree_children(owner, rule)
    if len(values) > 1:
        raise _semantic_error(module, values[1], f"duplicate {label}")
    return values[0] if values else None


def _qualified_identifier(node: Tree) -> tuple[str, ...]:
    return tuple(str(child) for child in node.children if isinstance(child, Token))


def _type_expression(node: Tree) -> ModelTypeExpression:
    name_node = next(child for child in node.children if isinstance(child, Tree) and child.data == "qualified_identifier")
    arguments_node = next(
        (child for child in node.children if isinstance(child, Tree) and child.data == "type_arguments"),
        None,
    )
    arguments = () if arguments_node is None else tuple(
        _type_expression(child)
        for child in arguments_node.children
        if isinstance(child, Tree) and child.data == "type_expression"
    )
    return ModelTypeExpression(_qualified_identifier(name_node), arguments)


def _token_expression(token: Token) -> ModelExpression:
    if token.type == "IDENTIFIER":
        return ModelExpression("identifier", str(token))
    if token.type == "DECIMAL_INTEGER":
        return ModelExpression("integer", int(str(token)))
    if token.type == "STRING":
        return ModelExpression("string", json.loads(str(token)))
    raise RuntimeError(f"unexpected expression token {token.type}")


def _lower_expression(node: Tree | Token) -> ModelExpression:
    # Lark's unary node needs special handling before the general dispatcher.
    if isinstance(node, Tree) and node.data == "unary":
        return ModelExpression("unary", str(node.children[0]), (_lower_expression(node.children[1]),))
    if isinstance(node, Tree) and node.data in {"postfix_expression", "assignable_expression"}:
        result = _lower_expression(node.children[0])
        for operation in node.children[1:]:
            assert isinstance(operation, Tree)
            if operation.data == "member_operation":
                result = ModelExpression("member", str(operation.children[0]), (result,))
            elif operation.data == "path_operation":
                result = ModelExpression("path", str(operation.children[0]), (result,))
            elif operation.data == "index_operation":
                result = ModelExpression("index", None, (result, _lower_expression(operation.children[0])))
            elif operation.data == "call_operation":
                arguments: tuple[ModelExpression, ...] = ()
                if operation.children:
                    arguments_node = operation.children[0]
                    assert isinstance(arguments_node, Tree)
                    arguments = tuple(_lower_expression(child) for child in arguments_node.children)
                result = ModelExpression("call", None, (result, *arguments))
            else:  # pragma: no cover - grammar guarantees the alternatives
                raise RuntimeError(f"unexpected postfix rule {operation.data}")
        return result
    if isinstance(node, Tree) and node.data in {"disjunction", "conjunction", "comparison", "sum", "product"}:
        result = _lower_expression(node.children[0])
        for index in range(1, len(node.children), 2):
            result = ModelExpression(
                "binary",
                str(node.children[index]),
                (result, _lower_expression(node.children[index + 1])),
            )
        return result
    if isinstance(node, Tree) and node.data in {"expression_statement", "primary"}:
        return _lower_expression(node.children[0])
    if isinstance(node, Token):
        return _token_expression(node)
    raise RuntimeError(f"unexpected expression node {node!r}")


def _walk_expression(expression: ModelExpression):
    yield expression
    for child in expression.children:
        yield from _walk_expression(child)


def _expression_block(node: Tree) -> tuple[ModelExpression, ...]:
    block = node if node.data == "expression_block" else next(
        child for child in node.children if isinstance(child, Tree) and child.data == "expression_block"
    )
    return tuple(_lower_expression(statement) for statement in block.children)


def _special_name(
    module: LoadedModule, node: Tree | Token, prefix: str, label: str
) -> tuple[str, ...]:
    expression = _lower_expression(node)
    if (
        expression.kind != "path"
        or expression.value is None
        or expression.children[0].kind != "identifier"
        or expression.children[0].value != prefix
    ):
        raise _semantic_error(module, node, f"{label} must have the form {prefix}::<Name>")
    return (prefix, str(expression.value))


def _flatten_access(expression: ModelExpression) -> tuple[list[str], list[str]] | None:
    if expression.kind == "identifier":
        return [str(expression.value)], []
    if expression.kind not in {"member", "path"}:
        return None
    base = _flatten_access(expression.children[0])
    if base is None:
        return None
    segments, operations = base
    return segments + [str(expression.value)], operations + [expression.kind]


def _resolve_target(
    raw: tuple[str, ...], module_name: tuple[str, ...], imports: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    if raw[0] in imports:
        return imports[raw[0]] + raw[1:]
    cursor = 0
    if raw[0] == "model":
        cursor = 1
        return raw[cursor:]
    if raw[0] == "self":
        cursor = 1
        return module_name + raw[cursor:]
    if raw[0] == "super":
        while cursor < len(raw) and raw[cursor] == "super":
            cursor += 1
        return module_name[: len(module_name) - cursor] + raw[cursor:]
    if len(raw) == 1:
        return module_name + raw
    return raw


def _object_expression(name: tuple[str, ...]) -> ModelExpression:
    result = ModelExpression("identifier", name[0])
    for part in name[1:]:
        result = ModelExpression("path", part, (result,))
    return result


def _signal(
    module: LoadedModule,
    node: Tree | Token,
    source: tuple[str, ...],
    mode: str,
    imports: dict[str, tuple[str, ...]],
    bindings: frozenset[str] = frozenset(),
    switch_names: frozenset[str] = frozenset(),
) -> ModelSignal:
    expression = _lower_expression(node)
    arguments: tuple[ModelExpression, ...] = ()
    if expression.kind == "call":
        arguments = expression.children[1:]
        expression = expression.children[0]
    access = _flatten_access(expression)
    expected = "signal must have the form Object.Transition::<Name> or Object.Action::<Name>"
    if access is None:
        raise _semantic_error(module, node, expected)
    segments, operations = access
    if (
        len(segments) < 3
        or segments[-2] not in {"Transition", "Action"}
        or operations[-2:] != ["member", "path"]
    ):
        raise _semantic_error(module, node, expected)
    raw_target = tuple(segments[:-2])
    if (
        len(raw_target) == 1
        and raw_target[0] in switch_names
        and raw_target[0] not in bindings
    ):
        raise _semantic_error(
            module,
            node,
            f"switches binding {raw_target[0]!r} is not in scope before its declaration",
        )
    task_owned_selector = (
        raw_target in {
            ("self", "TaskFlowRef"),
            ("self", "ResumeTargetRef"),
        }
        and operations[:-2] == ["member"]
    )
    event_flow_selector = (
        raw_target
        in {
            ("self", "InterruptFlowRef"),
            ("self", "ExceptionFlowRef"),
            ("self", "SyscallExitFlowRef"),
        }
        and operations[:-2] == ["member"]
    )
    if len(raw_target) == 1 and raw_target[0] in {
        *bindings,
        "CurrentTaskRef",
        "CurrentCPU",
    }:
        target = ModelExpression("identifier", raw_target[0])
    elif task_owned_selector or event_flow_selector:
        target = ModelExpression(
            "member",
            raw_target[1],
            (ModelExpression("identifier", "self"),),
        )
    elif (
        raw_target == ("CurrentTaskRef", "UserAppRuntimeRef")
        and operations[:-2] == ["member"]
    ):
        target = ModelExpression(
            "member",
            "UserAppRuntimeRef",
            (ModelExpression("identifier", "CurrentTaskRef"),),
        )
    elif (
        raw_target == ("CurrentCPU", "InterruptControlRef")
        and operations[:-2] == ["member"]
    ):
        target = ModelExpression(
            "member",
            "InterruptControlRef",
            (ModelExpression("identifier", "CurrentCPU"),),
        )
    else:
        target = _object_expression(
            _resolve_target(raw_target, module.name, imports)
        )
    signal = canonicalize_signal_name((segments[-2], segments[-1]))
    return ModelSignal(source, target, signal, mode, arguments)


def _transition_handler_signal(
    module: LoadedModule, node: Tree | Token
) -> tuple[str, ...]:
    signal = _special_name(module, node, "Transition", "accepted signal")
    canonical = canonicalize_signal_name(signal)
    if canonical != signal:
        raise _semantic_error(
            module,
            node,
            (
                f"transition handler signal {'::'.join(signal)} is non-canonical; "
                f"use {'::'.join(canonical)}"
            ),
        )
    return signal


def _handler_signature(
    module: LoadedModule, node: Tree, prefix: str
) -> tuple[tuple[str, ...], tuple[ModelParameter, ...]]:
    name_node = next(
        child
        for child in node.children
        if isinstance(child, Tree) and child.data == "qualified_identifier"
    )
    name = _qualified_identifier(name_node)
    if len(name) != 2 or name[0] != prefix:
        raise _semantic_error(
            module, node, f"accepted signal must have the form {prefix}::<Name>"
        )
    parameters_node = next(
        (
            child
            for child in node.children
            if isinstance(child, Tree) and child.data == "parameters"
        ),
        None,
    )
    parameters = () if parameters_node is None else tuple(
        ModelParameter(str(child.children[0]), _type_expression(child.children[1]))
        for child in parameters_node.children
        if isinstance(child, Tree) and child.data == "parameter"
    )
    canonical = canonicalize_signal_name(name)
    if canonical != name:
        raise _semantic_error(
            module,
            node,
            f"{'transition handler signal' if prefix == 'Transition' else 'action handler signal'} "
            f"{'::'.join(name)} is non-canonical; use {'::'.join(canonical)}",
        )
    return name, parameters


def _fields(node: Tree) -> tuple[ModelField, ...]:
    fields = []
    for child in node.children:
        if isinstance(child, Tree) and child.data == "field_declaration":
            mutable = any(
                isinstance(item, Tree) and item.data == "mutable_marker"
                for item in child.children
            )
            name = next(item for item in child.children if isinstance(item, Token))
            type_node = next(
                item
                for item in child.children
                if isinstance(item, Tree) and item.data == "type_expression"
            )
            default_node = next(
                (
                    item
                    for item in child.children
                    if isinstance(item, Tree) and item.data == "field_default"
                ),
                None,
            )
            fields.append(
                ModelField(
                    str(name),
                    _type_expression(type_node),
                    mutable,
                    None
                    if default_node is None
                    else _lower_expression(default_node.children[0]),
                )
            )
    return tuple(fields)


def _signal_nodes(node: Tree) -> tuple[Tree | Token, ...]:
    body = next(child for child in node.children if isinstance(child, Tree))
    if body.data == "expression_block":
        return tuple(body.children)
    if body.data == "single_signal_body":
        return (body.children[0],)
    raise RuntimeError(f"unexpected signal body {body.data}")


def _deferred(module: LoadedModule, node: Tree) -> ModelDeferred:
    values: dict[str, Tree] = {}
    for child in node.children[2:]:
        assert isinstance(child, Tree)
        key = str(child.data)
        if key in values:
            raise _semantic_error(module, child, f"duplicate deferred {key.removesuffix('_property').removesuffix('_block')}")
        values[key] = child
    required = {"category_property", "summary_property", "evidence_block", "close_when_property"}
    missing = sorted(required - set(values))
    if missing:
        raise _semantic_error(module, node, f"deferred declaration is missing {missing[0].split('_')[0]}")
    return ModelDeferred(
        name=str(node.children[0]),
        number=str(node.children[1]),
        category=_lower_expression(values["category_property"].children[0]),
        summary=json.loads(str(values["summary_property"].children[0])),
        evidence=_expression_block(values["evidence_block"]),
        close_when=json.loads(str(values["close_when_property"].children[0])),
    )


def _handler_blocks(
    module: LoadedModule,
    owner: Tree,
    source: tuple[str, ...],
    imports: dict[str, tuple[str, ...]],
) -> tuple[ModelHandlerBlock, ...]:
    result: list[ModelHandlerBlock] = []
    bindings: set[str] = set()
    binding_blocks = tuple(
        child
        for child in owner.children
        if isinstance(child, Tree) and child.data == "binds_block"
    )
    if len(binding_blocks) > 1:
        raise _semantic_error(module, binding_blocks[1], "duplicate binds block")
    binding_names = tuple(
        str(statement.children[0])
        for block in binding_blocks
        for statement in block.children
        if isinstance(statement, Tree) and statement.data == "binding_statement"
    )
    duplicate_binding = next(
        (name for index, name in enumerate(binding_names) if name in binding_names[:index]),
        None,
    )
    if duplicate_binding is not None:
        raise _semantic_error(
            module, binding_blocks[0], f"duplicate binding {duplicate_binding!r}"
        )
    switch_names = frozenset(
        str(child.children[0])
        for child in owner.children
        if isinstance(child, Tree) and child.data == "switches_statement"
    )
    expression_kinds = {
        "depends_on_block": "depends_on",
        "may_change_block": "may_change",
        "ensures_block": "ensures",
        "establishes_block": "establishes",
    }
    for child in owner.children:
        if not isinstance(child, Tree):
            continue
        rule = str(child.data)
        if rule in expression_kinds:
            result.append(ModelHandlerBlock(expression_kinds[rule], _expression_block(child)))
        elif rule in {"drives_block", "emits_block"}:
            mode = "drive" if rule == "drives_block" else "emit"
            signals = tuple(
                _signal(module, statement, source, mode, imports, frozenset(bindings), switch_names)
                for statement in _signal_nodes(child)
            )
            result.append(ModelHandlerBlock(rule.removesuffix("_block"), signals=signals))
        elif rule == "yields_statement":
            result.append(
                ModelHandlerBlock(
                    "yields",
                    signals=(
                        _signal(module, child.children[0], source, "yield", imports, frozenset(bindings), switch_names),
                    ),
                )
            )
        elif rule == "resumes_statement":
            result.append(
                ModelHandlerBlock(
                    "resumes",
                    signals=(
                        _signal(module, child.children[0], source, "resume", imports, frozenset(bindings), switch_names),
                    ),
                )
            )
        elif rule == "updates_block":
            result.append(
                ModelHandlerBlock(
                    "updates",
                    updates=tuple(
                        ModelUpdate(
                            _lower_expression(statement.children[0]),
                            _lower_expression(statement.children[1]),
                        )
                        for statement in child.children
                        if isinstance(statement, Tree)
                        and statement.data == "update_statement"
                    ),
                )
            )
        elif rule == "binds_block":
            declared: list[ModelBinding] = []
            seen: set[str] = set()
            all_names = set(binding_names)
            for statement in child.children:
                assert isinstance(statement, Tree)
                name = str(statement.children[0])
                expression = _lower_expression(statement.children[1])
                referenced = {
                    str(node.value)
                    for node in _walk_expression(expression)
                    if node.kind == "identifier"
                }
                forward = sorted((referenced & all_names) - seen)
                if forward:
                    raise _semantic_error(
                        module,
                        statement,
                        f"binding {name!r} references later binding {forward[0]!r}",
                    )
                declared.append(
                    ModelBinding(
                        name,
                        ModelTypeExpression(("UnresolvedBinding",)),
                        expression,
                    )
                )
                seen.add(name)
            result.append(ModelHandlerBlock("binds", bindings=tuple(declared)))
        elif rule in {"print_statement", "panic_statement"}:
            expression = _lower_expression(child.children[0])
            kind = rule.removesuffix("_statement")
            if expression.kind != "string":
                raise _semantic_error(
                    module,
                    child.children[0],
                    f"{kind} requires exactly one string literal",
                )
            result.append(ModelHandlerBlock(kind, expressions=(expression,)))
        elif rule == "switches_statement":
            binding = str(child.children[0])
            if binding in bindings:
                raise _semantic_error(
                    module, child, f"duplicate switches binding {binding!r}"
                )
            result.append(ModelHandlerBlock("switches", switches=binding))
            bindings.add(binding)
        elif rule == "deferred_declaration":
            result.append(ModelHandlerBlock("deferred", deferred=_deferred(module, child)))
    return tuple(result)


def _state(
    module: LoadedModule,
    node: Tree,
    object_name: tuple[str, ...],
    imports: dict[str, tuple[str, ...]],
) -> ModelState:
    name = _qualified_identifier(node.children[0])
    invariants: list[tuple[ModelExpression, ...]] = []
    transitions: list[ModelTransition] = []
    actions: list[ModelAction] = []
    for child in node.children[1:]:
        assert isinstance(child, Tree)
        if child.data == "invariant_block":
            invariants.append(_expression_block(child))
        elif child.data == "transitions_block":
            for handler in child.children:
                assert isinstance(handler, Tree)
                override = any(
                    isinstance(item, Tree) and item.data == "override_marker"
                    for item in handler.children
                )
                signal_node = next(
                    item
                    for item in handler.children
                    if isinstance(item, Tree)
                    and item.data
                    not in {
                        "override_marker",
                        "concrete_transition_handler",
                        "abstract_transition_handler",
                    }
                )
                tail = next(
                    item
                    for item in handler.children
                    if isinstance(item, Tree)
                    and item.data
                    in {"concrete_transition_handler", "abstract_transition_handler"}
                )
                abstract = tail.data == "abstract_transition_handler"
                target_node = None if abstract else tail.children[0]
                body = None if abstract else tail.children[1]
                assert target_node is None or isinstance(target_node, Tree)
                assert body is None or isinstance(body, Tree)
                signal, parameters = _handler_signature(
                    module, signal_node, "Transition"
                )
                transitions.append(
                    ModelTransition(
                        signal=signal,
                        target_state=None
                        if target_node is None
                        else _special_name(module, target_node, "State", "target state"),
                        blocks=()
                        if body is None
                        else _handler_blocks(module, body, object_name, imports),
                        abstract=abstract,
                        override=override,
                        parameters=parameters,
                    )
                )
        elif child.data == "actions_block":
            for handler in child.children:
                assert isinstance(handler, Tree)
                override = any(
                    isinstance(item, Tree) and item.data == "override_marker"
                    for item in handler.children
                )
                signal_node = next(
                    item
                    for item in handler.children
                    if isinstance(item, Tree)
                    and item.data
                    not in {
                        "override_marker",
                        "concrete_action_handler",
                        "abstract_action_handler",
                    }
                )
                tail = next(
                    item
                    for item in handler.children
                    if isinstance(item, Tree)
                    and item.data
                    in {"concrete_action_handler", "abstract_action_handler"}
                )
                abstract = tail.data == "abstract_action_handler"
                body = None if abstract else tail.children[0]
                assert body is None or isinstance(body, Tree)
                signal, parameters = _handler_signature(module, signal_node, "Action")
                if body is not None and not body.children:
                    raise _semantic_error(
                        module,
                        handler,
                        "concrete Action handler body must declare at least one block",
                    )
                actions.append(
                    ModelAction(
                        signal=signal,
                        blocks=()
                        if body is None
                        else _handler_blocks(module, body, object_name, imports),
                        abstract=abstract,
                        override=override,
                        parameters=parameters,
                    )
                )
    return ModelState(name, tuple(invariants), tuple(transitions), tuple(actions))


def _object(
    module: LoadedModule, node: Tree, imports: dict[str, tuple[str, ...]]
) -> ModelObject:
    name = module.name + (str(node.children[0]),)
    initial_node = _only(module, node, "initial_state_property", "initial_state")
    parent_node = _only(module, node, "parent_property", "parent")
    source_node = _only(module, node, "source_property", "source")
    idle_task_node = _only(module, node, "idle_task_property", "idle_task")
    logical_id_node = _only(module, node, "logical_id_property", "logical_id")
    continuation_node = _only(module, node, "continuation_property", "continuation")
    if continuation_node is not None:
        raise _semantic_error(
            module,
            continuation_node,
            "continuation may only be declared by a type; objects inherit it",
        )
    attrs_nodes = _tree_children(node, "attrs_declaration")
    if len(attrs_nodes) > 1:
        raise _semantic_error(module, attrs_nodes[1], "duplicate attrs declaration")
    states = tuple(
        _state(module, child, name, imports)
        for child in node.children
        if isinstance(child, Tree) and child.data == "state_declaration"
    )
    initial_state = (
        _special_name(
            module,
            initial_node.children[0],
            "State",
            "initial_state",
        )
        if initial_node is not None
        else None
    )
    references = []
    for reference in _tree_children(node, "reference_declaration"):
        assignments = tuple(
            ModelReferenceAssignment(
                _lower_expression(assignment.children[0]),
                _lower_expression(assignment.children[1]),
            )
            for assignment in _tree_children(reference, "reference_assignment")
        )
        references.append(ModelReference(str(reference.children[0]), assignments))
    direct_fields = _fields(node)
    declared_fields = direct_fields + (
        () if not attrs_nodes else _fields(attrs_nodes[0])
    )
    return ModelObject(
        name=name,
        base_type=_type_expression(node.children[1]),
        initial_state=initial_state,
        parent=None if parent_node is None else _lower_expression(parent_node.children[0]),
        source=None if source_node is None else _lower_expression(source_node.children[0]),
        attrs=None if not declared_fields else declared_fields,
        states=states,
        references=tuple(references),
        idle_task=None
        if idle_task_node is None
        else _lower_expression(idle_task_node.children[0]),
        logical_id=None
        if logical_id_node is None
        else int(str(logical_id_node.children[0]), 10),
    )


def _predicate(module: LoadedModule, node: Tree) -> ModelPredicate:
    name = module.name + (str(node.children[0]),)
    generic_node = next((child for child in node.children if isinstance(child, Tree) and child.data == "generic_parameters"), None)
    parameters_node = next((child for child in node.children if isinstance(child, Tree) and child.data == "parameters"), None)
    return_node = next(child for child in node.children if isinstance(child, Tree) and child.data == "type_expression")
    body_node = next((child for child in node.children if isinstance(child, Tree) and child.data == "predicate_body"), None)
    parameters = () if parameters_node is None else tuple(
        ModelParameter(str(child.children[0]), _type_expression(child.children[1]))
        for child in parameters_node.children
        if isinstance(child, Tree)
    )
    return ModelPredicate(
        name,
        () if generic_node is None else tuple(str(child) for child in generic_node.children),
        parameters,
        _type_expression(return_node),
        None if body_node is None else tuple(_lower_expression(child) for child in body_node.children),
    )


def _type_items(node: Tree) -> tuple[Tree, ...]:
    result: list[Tree] = []
    for child in node.children[1:]:
        if not isinstance(child, Tree) or child.data == "type_base":
            continue
        if child.data == "type_tail":
            result.extend(
                item for item in child.children if isinstance(item, Tree)
            )
        else:
            result.append(child)
    return tuple(result)


def _model_type(
    module: LoadedModule,
    node: Tree,
    imports: dict[str, tuple[str, ...]],
) -> ModelType:
    name = module.name + (str(node.children[0]),)
    items = _type_items(node)
    initial_nodes = tuple(
        item for item in items if item.data == "initial_state_property"
    )
    if len(initial_nodes) > 1:
        raise _semantic_error(module, initial_nodes[1], "duplicate initial_state")
    continuation_nodes = tuple(
        item for item in items if item.data == "continuation_property"
    )
    if len(continuation_nodes) > 1:
        raise _semantic_error(module, continuation_nodes[1], "duplicate continuation")
    continuation = False
    if continuation_nodes:
        value = str(continuation_nodes[0].children[0])
        if value != "true":
            raise _semantic_error(
                module,
                continuation_nodes[0],
                "continuation can only be declared as true and cannot be cancelled",
            )
        continuation = True
    sched_core_nodes = tuple(
        item for item in items if item.data == "sched_core_property"
    )
    if len(sched_core_nodes) > 1:
        raise _semantic_error(module, sched_core_nodes[1], "duplicate sched_core")
    sched_core = False
    if sched_core_nodes:
        value = str(sched_core_nodes[0].children[0])
        if value != "true":
            raise _semantic_error(
                module,
                sched_core_nodes[0],
                "sched_core can only be declared as true and cannot be cancelled",
            )
        sched_core = True
    user_runtime_nodes = tuple(
        item for item in items if item.data == "user_runtime_property"
    )
    if len(user_runtime_nodes) > 1:
        raise _semantic_error(module, user_runtime_nodes[1], "duplicate user_runtime")
    user_runtime = False
    if user_runtime_nodes:
        value = str(user_runtime_nodes[0].children[0])
        if value != "true":
            raise _semantic_error(
                module,
                user_runtime_nodes[0],
                "user_runtime can only be declared as true and cannot be cancelled",
            )
        user_runtime = True
    cpu_core_nodes = tuple(
        item for item in items if item.data == "cpu_core_property"
    )
    if len(cpu_core_nodes) > 1:
        raise _semantic_error(module, cpu_core_nodes[1], "duplicate cpu_core")
    cpu_core = False
    if cpu_core_nodes:
        value = str(cpu_core_nodes[0].children[0])
        if value != "true":
            raise _semantic_error(
                module,
                cpu_core_nodes[0],
                "cpu_core can only be declared as true and cannot be cancelled",
            )
        cpu_core = True
    event_flow_nodes = tuple(
        item for item in items if item.data == "event_flow_property"
    )
    if len(event_flow_nodes) > 1:
        raise _semantic_error(module, event_flow_nodes[1], "duplicate event_flow")
    event_flow = False
    if event_flow_nodes:
        value = str(event_flow_nodes[0].children[0])
        if value != "true":
            raise _semantic_error(
                module,
                event_flow_nodes[0],
                "event_flow can only be declared as true and cannot be cancelled",
            )
        event_flow = True
    syscall_exit_flow_nodes = tuple(
        item for item in items if item.data == "syscall_exit_flow_property"
    )
    if len(syscall_exit_flow_nodes) > 1:
        raise _semantic_error(
            module,
            syscall_exit_flow_nodes[1],
            "duplicate syscall_exit_flow",
        )
    syscall_exit_flow = False
    if syscall_exit_flow_nodes:
        value = str(syscall_exit_flow_nodes[0].children[0])
        if value != "true":
            raise _semantic_error(
                module,
                syscall_exit_flow_nodes[0],
                "syscall_exit_flow can only be declared as true and cannot be cancelled",
            )
        syscall_exit_flow = True
    base_node = next(
        (
            child
            for child in node.children
            if isinstance(child, Tree) and child.data == "type_base"
        ),
        None,
    )
    states = tuple(
        _state(module, item, name, imports)
        for item in items
        if item.data == "state_declaration"
    )
    field_container = Tree("field_container", list(items))
    fields = _fields(field_container)
    return ModelType(
        name=name,
        fields=None if not items and base_node is None else fields,
        base_type=None
        if base_node is None
        else _type_expression(base_node.children[0]),
        continuation=continuation,
        initial_state=None
        if not initial_nodes
        else _special_name(
            module,
            initial_nodes[0].children[0],
            "State",
            "initial_state",
        ),
        states=states,
        sched_core=sched_core,
        user_runtime=user_runtime,
        cpu_core=cpu_core,
        syscall_exit_flow=syscall_exit_flow,
        event_flow=event_flow,
    )


def _external(
    module: LoadedModule, node: Tree, imports: dict[str, tuple[str, ...]]
) -> ModelExternal:
    name = module.name + (str(node.children[0]),)
    signals: list[ModelSignal] = []
    for block in node.children[1:]:
        assert isinstance(block, Tree)
        mode = {
            "drives_block": "drive",
            "emits_block": "emit",
            "resumes_statement": "resume",
        }[block.data]
        signal_nodes = (
            (block.children[0],)
            if block.data == "resumes_statement"
            else _signal_nodes(block)
        )
        signals.extend(
            _signal(module, statement, name, mode, imports)
            for statement in signal_nodes
        )
    return ModelExternal(name, tuple(signals))


def _lower_module(module: LoadedModule) -> ModelModule:
    imports = dict(resolve_use_name(module.name, declaration) for declaration in module.uses)
    predicates: list[ModelPredicate] = []
    types: list[ModelType] = []
    objects: list[ModelObject] = []
    externals: list[ModelExternal] = []
    for item in module.tree.children:
        if not isinstance(item, Tree):
            continue
        if item.data == "predicate_declaration":
            predicates.append(_predicate(module, item))
        elif item.data == "type_declaration":
            types.append(_model_type(module, item, imports))
        elif item.data == "object_declaration":
            objects.append(_object(module, item, imports))
        elif item.data == "external_declaration":
            externals.append(_external(module, item, imports))
    try:
        return ModelModule(module.name, tuple(predicates), tuple(types), tuple(objects), tuple(externals))
    except ModelIRValidationError as exc:
        raise _semantic_error(module, module.tree, str(exc)) from exc


def _merge_fields(
    inherited: tuple[ModelField, ...] | None,
    declared: tuple[ModelField, ...] | None,
) -> tuple[ModelField, ...] | None:
    if inherited is None and declared is None:
        return None
    result = list(inherited or ())
    positions = {field.name: index for index, field in enumerate(result)}
    for field in declared or ():
        if field.name in positions:
            result[positions[field.name]] = field
        else:
            positions[field.name] = len(result)
            result.append(field)
    return tuple(result)


def _merge_handler_group(
    module: LoadedModule,
    owner: Tree,
    inherited: tuple[ModelTransition, ...] | tuple[ModelAction, ...],
    declared: tuple[ModelTransition, ...] | tuple[ModelAction, ...],
    label: str,
) -> tuple[ModelTransition, ...] | tuple[ModelAction, ...]:
    result = list(inherited)
    positions = {handler.signal: index for index, handler in enumerate(result)}
    for handler in declared:
        exists = handler.signal in positions
        if exists and not handler.override:
            raise _semantic_error(
                module,
                owner,
                f"inherited {label} handler {'::'.join(handler.signal)!r} "
                "must be declared with override",
            )
        if not exists and handler.override:
            raise _semantic_error(
                module,
                owner,
                f"override {label} handler {'::'.join(handler.signal)!r} "
                "has no inherited handler",
            )
        if exists:
            inherited_handler = result[positions[handler.signal]]
            if inherited_handler.parameters != handler.parameters:
                raise _semantic_error(
                    module,
                    owner,
                    f"override {label} handler {'::'.join(handler.signal)!r} "
                    "must preserve its parameter signature",
                )
            result[positions[handler.signal]] = handler
        else:
            positions[handler.signal] = len(result)
            result.append(handler)
    return tuple(result)


def _merge_states(
    module: LoadedModule,
    owner: Tree,
    inherited: tuple[ModelState, ...],
    declared: tuple[ModelState, ...],
) -> tuple[ModelState, ...]:
    result = list(inherited)
    positions = {state.name: index for index, state in enumerate(result)}
    for state in declared:
        if state.name not in positions:
            # A new state cannot override a handler because it has no inherited scope.
            transitions = _merge_handler_group(
                module, owner, (), state.transitions, "transition"
            )
            actions = _merge_handler_group(module, owner, (), state.actions, "action")
            positions[state.name] = len(result)
            result.append(
                ModelState(state.name, state.invariants, transitions, actions)
            )
            continue
        index = positions[state.name]
        base = result[index]
        result[index] = ModelState(
            state.name,
            base.invariants + state.invariants,
            _merge_handler_group(
                module,
                owner,
                base.transitions,
                state.transitions,
                "transition",
            ),
            _merge_handler_group(
                module, owner, base.actions, state.actions, "action"
            ),
        )
    return tuple(result)


def _rebind_signal(signal: ModelSignal, source: tuple[str, ...]) -> ModelSignal:
    return ModelSignal(
        source, signal.target, signal.signal, signal.mode, signal.arguments
    )


def _rebind_states(
    states: tuple[ModelState, ...], source: tuple[str, ...]
) -> tuple[ModelState, ...]:
    def blocks(values: tuple[ModelHandlerBlock, ...]) -> tuple[ModelHandlerBlock, ...]:
        return tuple(
            ModelHandlerBlock(
                block.kind,
                block.expressions,
                tuple(_rebind_signal(signal, source) for signal in block.signals),
                block.deferred,
                block.updates,
                block.switches,
                block.bindings,
            )
            for block in values
        )

    return tuple(
        ModelState(
            state.name,
            state.invariants,
            tuple(
                ModelTransition(
                    handler.signal,
                    handler.target_state,
                    blocks(handler.blocks),
                    handler.abstract,
                    handler.override,
                    handler.parameters,
                )
                for handler in state.transitions
            ),
            tuple(
                ModelAction(
                    handler.signal,
                    blocks(handler.blocks),
                    handler.abstract,
                    handler.override,
                    handler.parameters,
                )
                for handler in state.actions
            ),
        )
        for state in states
    )


def _expand_inheritance(
    lowered: tuple[ModelModule, ...],
    modules: tuple[LoadedModule, ...],
) -> tuple[ModelModule, ...]:
    loaded = {module.name: module for module in modules}
    imports = {
        module.name: dict(
            resolve_use_name(module.name, declaration) for declaration in module.uses
        )
        for module in modules
    }
    type_nodes: dict[tuple[str, ...], Tree] = {}
    object_nodes: dict[tuple[str, ...], Tree] = {}
    for module in modules:
        for item in module.tree.children:
            if not isinstance(item, Tree):
                continue
            if item.data == "type_declaration":
                type_nodes[module.name + (str(item.children[0]),)] = item
            elif item.data == "object_declaration":
                object_nodes[module.name + (str(item.children[0]),)] = item

    raw_types = {
        item.name: item for module in lowered for item in module.types
    }
    expanded_types: dict[tuple[str, ...], ModelType] = {}
    visiting: list[tuple[str, ...]] = []

    def resolve_base(
        expression: ModelTypeExpression, module_name: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _resolve_target(expression.name, module_name, imports[module_name])

    def expand_type(name: tuple[str, ...]) -> ModelType:
        if name in expanded_types:
            return expanded_types[name]
        raw = raw_types[name]
        module = loaded[name[:-1]]
        node = type_nodes[name]
        if name in visiting:
            cycle = visiting[visiting.index(name) :] + [name]
            raise _semantic_error(
                module,
                node,
                "type inheritance cycle: "
                + " -> ".join("::".join(item) for item in cycle),
            )
        visiting.append(name)
        base: ModelType | None = None
        if raw.base_type is not None:
            base_name = resolve_base(raw.base_type, name[:-1])
            if base_name not in raw_types:
                raise _semantic_error(
                    module,
                    node,
                    f"base type {'::'.join(raw.base_type.name)!r} is not declared",
                )
            base = expand_type(base_name)
        fields = _merge_fields(None if base is None else base.fields, raw.fields)
        states = _merge_states(
            module,
            node,
            () if base is None else base.states,
            raw.states,
        )
        initial_state = raw.initial_state
        if initial_state is None and base is not None:
            initial_state = base.initial_state
        if initial_state is None and states:
            initial_state = ("State", "Base")
        continuation = raw.continuation or (base is not None and base.continuation)
        sched_core = raw.sched_core or (base is not None and base.sched_core)
        user_runtime = raw.user_runtime or (base is not None and base.user_runtime)
        cpu_core = raw.cpu_core or (base is not None and base.cpu_core)
        event_flow = raw.event_flow or (base is not None and base.event_flow)
        syscall_exit_flow = raw.syscall_exit_flow or (
            base is not None and base.syscall_exit_flow
        )
        expanded = ModelType(
            raw.name,
            fields,
            raw.base_type,
            continuation,
            initial_state,
            states,
            sched_core,
            user_runtime,
            cpu_core,
            syscall_exit_flow,
            event_flow,
        )
        state_names = {state.name for state in states}
        if initial_state is not None and initial_state not in state_names:
            raise _semantic_error(
                module,
                node,
                f"invalid initial_state {'::'.join(initial_state)!r}",
            )
        if continuation and (
            initial_state != ("State", "Online")
            or tuple(state.name for state in states) != (("State", "Online"),)
            or states[0].transitions
        ):
            raise _semantic_error(
                module,
                node,
                "continuation type must have exactly initial_state State::Online, "
                "one State::Online, and no transitions",
            )
        visiting.pop()
        expanded_types[name] = expanded
        return expanded

    for name in raw_types:
        expand_type(name)

    expanded_objects: dict[tuple[str, ...], ModelObject] = {}
    for source_module in lowered:
        module = loaded[source_module.name]
        for raw in source_module.objects:
            node = object_nodes[raw.name]
            if raw.name[-1] in {
                "CurrentTaskRef",
                "CurrentCPU",
                "TaskFlowRef",
                "ResumeTargetRef",
                "InterruptFlowRef",
                "ExceptionFlowRef",
                "SyscallExitFlowRef",
                "InterruptControlRef",
            }:
                raise _semantic_error(
                    module,
                    node,
                    f"{raw.name[-1]} is a reserved runtime selector and must not be "
                    "declared as an object",
                )
            base_name = resolve_base(raw.base_type, source_module.name)
            base = expanded_types.get(base_name)
            states = _merge_states(
                module,
                node,
                () if base is None else base.states,
                raw.states,
            )
            initial_state = raw.initial_state
            if initial_state is None and base is not None:
                initial_state = base.initial_state
            if initial_state is None and states:
                initial_state = ("State", "Base")
            continuation = False if base is None else base.continuation
            states = _rebind_states(states, raw.name)
            fields = _merge_fields(None if base is None else base.fields, raw.attrs)
            abstract = tuple(
                (state.name, handler.signal)
                for state in states
                for handler in (*state.transitions, *state.actions)
                if handler.abstract
            )
            user_runtime = base is not None and base.user_runtime
            syscall_exit_flow = base is not None and base.syscall_exit_flow
            event_flow = base is not None and base.event_flow
            if abstract and not user_runtime:
                state_name, signal = abstract[0]
                raise _semantic_error(
                    module,
                    node,
                    f"object {'::'.join(raw.name)!r} does not implement abstract handler "
                    f"{'::'.join(state_name)} + {'::'.join(signal)}",
                )
            if continuation:
                state_names = tuple(state.name for state in states)
                if (
                    initial_state != ("State", "Online")
                    or state_names != (("State", "Online"),)
                    or states[0].transitions
                ):
                    raise _semantic_error(
                        module,
                        node,
                        "continuation object must have exactly initial_state State::Online, "
                        "one State::Online, and no transitions",
                    )
                if not any(
                    action.signal == ("Action", "Enter")
                    for action in states[0].actions
                ):
                    raise _semantic_error(
                        module, node, "continuation object requires Action::Enter"
                    )
            if user_runtime:
                raise _semantic_error(
                    module,
                    node,
                    "user_runtime instances are inference-owned Task children and must not be declared as model objects",
                )
            if event_flow:
                raise _semantic_error(
                    module,
                    node,
                    "event_flow instances are inference-owned CPU children and must not be declared as model objects",
                )
            for state in states:
                for transition in state.transitions:
                    if transition.target_state not in {item.name for item in states}:
                        assert transition.target_state is not None
                        raise _semantic_error(
                            module,
                            node,
                            f"transition targets unknown state "
                            f"{'::'.join(transition.target_state)!r}",
                        )
                for handler in (*state.transitions, *state.actions):
                    for block in handler.blocks:
                        if block.kind == "yields" and (
                            not continuation
                            or not isinstance(handler, ModelAction)
                        ):
                            raise _semantic_error(
                                module,
                                node,
                                "yields is only allowed in an Action handler of a "
                                "continuation object",
                            )
            expanded_objects[raw.name] = ModelObject(
                raw.name,
                raw.base_type,
                initial_state,
                raw.parent,
                raw.source,
                fields,
                states,
                raw.references,
                continuation,
                raw.idle_task,
                raw.logical_id,
            )

    continuation_names = {
        name for name, model_object in expanded_objects.items() if model_object.continuation
    }

    task_owned_selectors = (
        (["self", "TaskFlowRef"], ["member"]),
        (["self", "ResumeTargetRef"], ["member"]),
    )
    interrupt_flow_selector = (["self", "InterruptFlowRef"], ["member"])
    exception_flow_selector = (["self", "ExceptionFlowRef"], ["member"])
    syscall_flow_selector = (["self", "SyscallExitFlowRef"], ["member"])
    event_flow_selectors = (
        interrupt_flow_selector,
        exception_flow_selector,
        syscall_flow_selector,
    )

    object_names = set(expanded_objects)

    def resolve_object_expression(
        expression: ModelExpression,
        module_name: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        access = _flatten_access(expression)
        if access is None:
            return None
        segments, operations = access
        if any(operation == "member" for operation in operations):
            return None
        candidate = _resolve_target(tuple(segments), module_name, imports[module_name])
        if candidate in object_names:
            return candidate
        matches = tuple(
            name for name in object_names if name[-len(segments) :] == tuple(segments)
        )
        return matches[0] if len(matches) == 1 else None

    def validate_continuation_target(
        module: LoadedModule, owner: Tree, signal: ModelSignal
    ) -> None:
        if _flatten_access(signal.target) in (
            *task_owned_selectors,
            *event_flow_selectors,
        ):
            return
        target_name = resolve_object_expression(signal.target, module.name)
        if target_name is None:
            if signal.mode == "resume":
                raise _semantic_error(
                    module,
                    owner,
                    "resumes requires a statically resolvable continuation target",
                )
            return
        is_continuation = target_name in continuation_names
        if signal.mode == "resume" and (
            not is_continuation or signal.signal[0] != "Action"
        ):
            raise _semantic_error(
                module,
                owner,
                "resumes must target an Action on a continuation object",
            )
        if not is_continuation:
            return
        if signal.mode == "resume" and signal.signal[0] == "Action":
            return
        if not (
            signal.signal[0] == "Action"
            and signal.source == target_name
            and signal.mode == "drive"
        ):
            raise _semantic_error(
                module,
                owner,
                "continuation entry from outside must use resumes Action; "
                "other Actions must be synchronous calls from the same continuation",
            )

    for module in lowered:
        loaded_module = loaded[module.name]
        for external in module.externals:
            owner = next(
                item
                for item in loaded_module.tree.children
                if isinstance(item, Tree)
                and item.data == "external_declaration"
                and str(item.children[0]) == external.name[-1]
            )
            for signal in external.signals:
                validate_continuation_target(loaded_module, owner, signal)
        for model_object in module.objects:
            effective = expanded_objects[model_object.name]
            owner = object_nodes[model_object.name]
            for state in effective.states:
                for handler in (*state.transitions, *state.actions):
                    for block in handler.blocks:
                        for signal in block.signals:
                            validate_continuation_target(
                                loaded_module, owner, signal
                            )

    TypeKey = tuple[tuple[str, ...], tuple[object, ...]]

    def resolve_type(
        expression: ModelTypeExpression, module_name: tuple[str, ...]
    ) -> TypeKey:
        if expression.name in {("Collection",), ("Relation",), ("Map",)}:
            arity = 1 if expression.name == ("Collection",) else 2
            if len(expression.arguments) != arity:
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    f"{expression.name[0]} requires exactly {arity} type argument(s)",
                )
            arguments = tuple(
                resolve_type(item, module_name) for item in expression.arguments
            )
            if expression.name in {("Relation",), ("Map",)}:
                for argument in arguments:
                    if argument[1] or (
                        argument[0] not in raw_types
                        and argument[0] != ("String",)
                    ):
                        raise _semantic_error(
                            loaded[module_name],
                            loaded[module_name].tree,
                            f"{expression.name[0]} only supports String or object types",
                        )
            return (
                expression.name,
                arguments,
            )
        if expression.name == ("String",):
            if expression.arguments:
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    "String does not accept type arguments",
                )
            return (("String",), ())
        name = resolve_base(expression, module_name)
        if expression.arguments:
            raise _semantic_error(
                loaded[module_name],
                loaded[module_name].tree,
                f"non-generic type {'::'.join(expression.name)!r} does not accept arguments",
            )
        return (name, ())

    def object_type(name: tuple[str, ...]) -> TypeKey:
        model_object = expanded_objects[name]
        return resolve_type(model_object.base_type, name[:-1])

    def compatible(actual: TypeKey, expected: TypeKey) -> bool:
        if actual == expected:
            return True
        actual_name, actual_arguments = actual
        expected_name, _ = expected
        if actual_arguments or actual_name in {("Collection",), ("Relation",), ("Map",), ("String",)}:
            return False
        cursor = actual_name
        seen: set[tuple[str, ...]] = set()
        while cursor in raw_types and cursor not in seen:
            seen.add(cursor)
            base_expression = raw_types[cursor].base_type
            if base_expression is None:
                return False
            base = resolve_type(base_expression, cursor[:-1])
            if base == expected:
                return True
            cursor, arguments = base
            if arguments:
                return False
        return False

    for type_name, model_type in expanded_types.items():
        for field in model_type.fields or ():
            if field.mutable and resolve_type(field.type, type_name[:-1]) == (
                ("String",),
                (),
            ):
                raise _semantic_error(
                    loaded[type_name[:-1]],
                    type_nodes[type_name],
                    "mutable String fields are not supported",
                )

    task_types = tuple(name for name in raw_types if name[-1] == "Task")
    task_flow_types = tuple(name for name in raw_types if name[-1] == "TaskFlow")
    user_runtime_types = tuple(
        name for name, model_type in expanded_types.items() if model_type.user_runtime
    )
    cpu_core_types = tuple(
        name for name, model_type in expanded_types.items() if model_type.cpu_core
    )
    syscall_exit_flow_types = tuple(
        name
        for name, model_type in expanded_types.items()
        if model_type.syscall_exit_flow
    )
    event_flow_types = tuple(
        name for name, model_type in expanded_types.items() if model_type.event_flow
    )
    interrupt_flow_types = tuple(
        name
        for name in event_flow_types
        if name[-1] == "InterruptFlow" and expanded_types[name].continuation
    )
    exception_flow_types = tuple(
        name
        for name in event_flow_types
        if name[-1] == "ExceptionFlow" and expanded_types[name].continuation
    )
    interrupt_control_types = tuple(
        name for name in expanded_types if name[-1] == "InterruptControl"
    )

    if len(cpu_core_types) > 1:
        name = cpu_core_types[1]
        raise _semantic_error(
            loaded[name[:-1]],
            type_nodes[name],
            "the model may declare at most one cpu_core type",
        )
    if len(syscall_exit_flow_types) > 1:
        name = syscall_exit_flow_types[1]
        raise _semantic_error(
            loaded[name[:-1]],
            type_nodes[name],
            "the model may declare at most one syscall_exit_flow type",
        )
    for family, candidates in (
        ("interrupt", interrupt_flow_types),
        ("exception", exception_flow_types),
    ):
        if len(candidates) > 1:
            name = candidates[1]
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                f"the model may declare at most one {family}_flow type",
            )

    def protocol_action(
        model_type: ModelType, signal: tuple[str, ...]
    ) -> ModelAction | None:
        online = next(
            (state for state in model_type.states if state.name == ("State", "Online")),
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

    for name in cpu_core_types:
        for signal_name, selector, flow_types in (
            ("OnInterrupt", interrupt_flow_selector, interrupt_flow_types),
            ("OnException", exception_flow_selector, exception_flow_types),
        ):
            if not flow_types:
                continue
            event_action = protocol_action(
                expanded_types[name], ("Action", signal_name)
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
                or _flatten_access(event_signals[0].target) != selector
                or event_signals[0].signal != ("Action", "Enter")
                or event_signals[0].mode != "resume"
                or event_signals[0].arguments
            ):
                raise _semantic_error(
                    loaded[name[:-1]],
                    type_nodes[name],
                    f"cpu_core requires Online Action::{signal_name} forwarding to self.{selector[0][-1]}.Action::Enter",
                )
        if not syscall_exit_flow_types:
            continue
        action = protocol_action(
            expanded_types[name], ("Action", "OnSyscallExit")
        )
        signals = () if action is None else tuple(
            signal
            for block in action.blocks
            if block.kind == "resumes"
            for signal in block.signals
        )
        if (
            not one_i32_parameter(action, "status")
            or len(signals) != 1
            or _flatten_access(signals[0].target) != syscall_flow_selector
            or signals[0].signal != ("Action", "Enter")
            or signals[0].mode != "resume"
            or signals[0].arguments != (ModelExpression("identifier", "status"),)
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "cpu_core requires Online Action::OnSyscallExit(status: i32) forwarding to self.SyscallExitFlowRef.Action::Enter(status)",
            )

    for name in syscall_exit_flow_types:
        if not expanded_types[name].event_flow:
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "syscall_exit_flow types must inherit the event_flow protocol",
            )
        action = protocol_action(expanded_types[name], ("Action", "Enter"))
        signals = () if action is None else tuple(
            signal
            for block in action.blocks
            if block.kind == "drives"
            for signal in block.signals
        )
        if (
            not one_i32_parameter(action, "status")
            or len(signals) != 1
            or signals[0].target != ModelExpression("identifier", "CurrentTaskRef")
            or signals[0].signal != ("Action", "Exit")
            or signals[0].mode != "drive"
            or signals[0].arguments != (ModelExpression("identifier", "status"),)
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "syscall_exit_flow requires Online Action::Enter(status: i32) driving CurrentTaskRef.Action::Exit(status)",
            )

    for name in (*interrupt_flow_types, *exception_flow_types):
        action = protocol_action(expanded_types[name], ("Action", "Enter"))
        if action is None or action.abstract or action.parameters:
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "returning event_flow types require a concrete parameterless Online Action::Enter",
            )

    if cpu_core_types and task_flow_types:
        fields = {
            field.name: field
            for field in expanded_types[task_flow_types[0]].fields or ()
        }
        cpu_ref = fields.get("cpu_ref")
        if (
            cpu_ref is None
            or not cpu_ref.mutable
            or resolve_base(cpu_ref.type, task_flow_types[0][:-1])
            != cpu_core_types[0]
        ):
            name = task_flow_types[0]
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "TaskFlow requires mutable cpu_ref: CPU when cpu_core is modeled",
            )

    if len(user_runtime_types) > 1:
        name = user_runtime_types[1]
        raise _semantic_error(
            loaded[name[:-1]],
            type_nodes[name],
            "the model may declare at most one user_runtime type",
        )

    for name in user_runtime_types:
        model_type = expanded_types[name]
        online = next(
            (state for state in model_type.states if state.name == ("State", "Online")),
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
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "user_runtime type requires the Base/Prepared/Ready/Online lifecycle "
                "and an abstract, parameterless Online Action::Enter",
            )

    def is_task_object(name: tuple[str, ...]) -> bool:
        return len(task_types) == 1 and compatible(
            object_type(name), (task_types[0], ())
        )

    def is_task_type(name: tuple[str, ...]) -> bool:
        return len(task_types) == 1 and compatible(
            (name, ()), (task_types[0], ())
        )

    def is_task_flow_object(name: tuple[str, ...]) -> bool:
        return len(task_flow_types) == 1 and compatible(
            object_type(name), (task_flow_types[0], ())
        )

    def is_sched_core_object(name: tuple[str, ...]) -> bool:
        type_name = object_type(name)[0]
        return type_name in expanded_types and expanded_types[type_name].sched_core

    def is_cpu_core_object(name: tuple[str, ...]) -> bool:
        type_name = object_type(name)[0]
        return type_name in expanded_types and expanded_types[type_name].cpu_core

    def is_event_flow_object(name: tuple[str, ...]) -> bool:
        type_name = object_type(name)[0]
        return type_name in expanded_types and expanded_types[type_name].event_flow

    bootstrap_idle_tasks = {
        idle
        for scheduler_name, scheduler in expanded_objects.items()
        if is_sched_core_object(scheduler_name) and scheduler.idle_task is not None
        for idle in (
            resolve_object_expression(scheduler.idle_task, scheduler_name[:-1]),
        )
        if idle is not None
    }

    core_actions = {("Action", "Enqueue"), ("Action", "Dequeue")}
    for name, model_type in expanded_types.items():
        for state in model_type.states:
            for handler in (*state.transitions, *state.actions):
                for block in handler.blocks:
                    for signal in block.signals:
                        access = _flatten_access(signal.target)
                        mentions_task_selector = (
                            access is not None
                            and any(
                                segment in {"TaskFlowRef", "ResumeTargetRef"}
                                for segment in access[0]
                            )
                        )
                        if mentions_task_selector and access not in task_owned_selectors:
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "TaskFlowRef and ResumeTargetRef require the exact self-owned selector form",
                            )
                        if access in event_flow_selectors:
                            expected_handler = {
                                "InterruptFlowRef": ("Action", "OnInterrupt"),
                                "ExceptionFlowRef": ("Action", "OnException"),
                                "SyscallExitFlowRef": ("Action", "OnSyscallExit"),
                            }[access[0][-1]]
                            invalid_arguments = (
                                bool(signal.arguments)
                                if access[0][-1] != "SyscallExitFlowRef"
                                else len(signal.arguments) != len(handler.parameters)
                            )
                            if (
                                name not in cpu_core_types
                                or handler.signal != expected_handler
                                or block.kind != "resumes"
                                or signal.mode != "resume"
                                or signal.signal != ("Action", "Enter")
                                or invalid_arguments
                            ):
                                raise _semantic_error(
                                    loaded[name[:-1]],
                                    type_nodes[name],
                                    "Event FlowRef selectors are only available in their matching CPU receive handler",
                                )
                        target_name = resolve_object_expression(
                            signal.target, name[:-1]
                        )
                        if (
                            target_name is not None
                            and is_task_flow_object(target_name)
                            and signal.signal == ("Action", "Enter")
                        ):
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "TaskFlow Action::Enter must use a Task-owned TaskFlowRef or ResumeTargetRef selector",
                            )
                        if (
                            is_task_type(name)
                            and isinstance(handler, ModelTransition)
                            and handler.signal
                            in {
                                ("Transition", "Suspend"),
                                ("Transition", "Resume"),
                            }
                            and target_name is not None
                            and is_sched_core_object(target_name)
                            and signal.signal in core_actions
                        ):
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "Task Suspend/Resume handlers must not call sched_core Enqueue/Dequeue",
                            )
                        if access not in task_owned_selectors:
                            continue
                        if not is_task_type(name):
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "TaskFlowRef and ResumeTargetRef are only available in Task handlers",
                            )
                        if signal.arguments or signal.signal != ("Action", "Enter"):
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "Task-owned resume selectors only accept parameterless Action::Enter",
                            )
                        if signal.mode != "resume" or block.kind != "resumes":
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "Task-owned resume selector Action::Enter must use resumes",
                            )
                    for update in block.updates:
                        access = _flatten_access(update.target)
                        if access is not None and any(
                            segment in {"TaskFlowRef", "ResumeTargetRef"}
                            for segment in access[0]
                        ):
                            raise _semantic_error(
                                loaded[name[:-1]],
                                type_nodes[name],
                                "TaskFlowRef and ResumeTargetRef are read-only and cannot be updated",
                            )
        if not is_task_type(name):
            continue
        for state in model_type.states:
            for transition in state.transitions:
                if transition.signal != ("Transition", "Resume"):
                    continue
                if (
                    state.name != ("State", "Online")
                    or transition.target_state != ("State", "OnCpu")
                ):
                    raise _semantic_error(
                        loaded[name[:-1]],
                        type_nodes[name],
                        "Task Transition::Resume is only allowed from State::Online "
                        "to State::OnCpu",
                    )
    for name, model_type in raw_types.items():
        effective = expanded_types[name]
        for state in model_type.states:
            for action in state.actions:
                if action.signal == ("Action", "ResetCurrent"):
                    raise _semantic_error(
                        loaded[name[:-1]],
                        type_nodes[name],
                        "Action::ResetCurrent may only be declared by BootTask in State::OnCpu",
                    )
        if name[-1] in {
            "CurrentCPU",
            "TaskFlowRef",
            "ResumeTargetRef",
            "InterruptFlowRef",
            "ExceptionFlowRef",
            "SyscallExitFlowRef",
            "InterruptControlRef",
        }:
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "TaskFlowRef and ResumeTargetRef are reserved read-only Task selectors and cannot be declared",
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
            for field in model_type.fields or ()
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "TaskFlowRef and ResumeTargetRef are reserved read-only Task selectors and cannot be declared",
            )
        if effective.cpu_core and (
            effective.continuation
            or effective.sched_core
            or effective.user_runtime
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "cpu_core cannot also be a continuation, sched_core, or user_runtime",
            )
        if effective.event_flow and (
            effective.cpu_core or effective.sched_core or effective.user_runtime
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "event_flow cannot also be cpu_core, sched_core, or user_runtime",
            )
        if effective.syscall_exit_flow and not effective.continuation:
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "syscall_exit_flow types must also be continuations",
            )
        if not effective.sched_core:
            continue
        if any(
            action.signal in core_actions
            for state in model_type.states
            for action in state.actions
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                type_nodes[name],
                "sched_core types must not declare or override Action::Enqueue or Action::Dequeue",
            )

    for name, model_object in expanded_objects.items():
        owner = object_nodes[name]
        sched_core = is_sched_core_object(name)
        cpu_core = is_cpu_core_object(name)
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
            reference.name in {
                "TaskFlowRef", "ResumeTargetRef", "InterruptFlowRef",
                "ExceptionFlowRef", "SyscallExitFlowRef", "InterruptControlRef"
            }
            for reference in model_object.references
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                owner,
                "TaskFlowRef and ResumeTargetRef are reserved read-only Task selectors and cannot be declared",
            )
        if any(
            (_flatten_access(assignment.target) or ([], []))[0][:1]
            in (
                ["CurrentTaskRef"],
                ["CurrentCPU"],
                ["TaskFlowRef"],
                ["ResumeTargetRef"],
                ["InterruptFlowRef"],
                ["ExceptionFlowRef"],
                ["SyscallExitFlowRef"],
                ["InterruptControlRef"],
                ["self"],
            )
            and any(
                segment
                in {
                    "CurrentTaskRef",
                    "CurrentCPU",
                    "TaskFlowRef",
                    "ResumeTargetRef",
                    "InterruptFlowRef",
                    "ExceptionFlowRef",
                    "SyscallExitFlowRef",
                    "InterruptControlRef",
                }
                for segment in (_flatten_access(assignment.target) or ([], []))[0]
            )
            for reference in model_object.references
            for assignment in reference.assignments
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                owner,
                "runtime selectors are read-only and cannot be assigned",
            )
        if is_task_object(name):
            for state in model_object.states:
                for transition in state.transitions:
                    if transition.signal != ("Transition", "Resume"):
                        continue
                    if (
                        state.name != ("State", "Online")
                        or transition.target_state != ("State", "OnCpu")
                    ):
                        raise _semantic_error(
                            loaded[name[:-1]],
                            owner,
                            "Task Transition::Resume is only allowed from State::Online "
                            "to State::OnCpu",
                        )
        if cpu_core and model_object.logical_id is None:
            raise _semantic_error(
                loaded[name[:-1]], owner, "cpu_core object requires logical_id"
            )
        if not cpu_core and model_object.logical_id is not None:
            raise _semantic_error(
                loaded[name[:-1]], owner, "logical_id is only allowed on cpu_core objects"
            )
        if sched_core and model_object.idle_task is None:
            raise _semantic_error(
                loaded[name[:-1]], owner, "sched_core object requires idle_task"
            )
        if sched_core and cpu_core_types:
            parent = (
                None
                if model_object.parent is None
                else resolve_object_expression(model_object.parent, name[:-1])
            )
            if parent is None or not is_cpu_core_object(parent):
                raise _semantic_error(
                    loaded[name[:-1]],
                    owner,
                    "sched_core object must be owned by a cpu_core parent",
                )
        if not sched_core and model_object.idle_task is not None:
            raise _semantic_error(
                loaded[name[:-1]], owner, "idle_task is only allowed on sched_core objects"
            )
        if sched_core and any(
            action.signal in core_actions
            for state in next(
                raw for module in lowered for raw in module.objects if raw.name == name
            ).states
            for action in state.actions
        ):
            raise _semantic_error(
                loaded[name[:-1]],
                owner,
                "sched_core objects must not declare or override Action::Enqueue or Action::Dequeue",
            )
        if not sched_core:
            continue
        assert model_object.idle_task is not None
        idle = resolve_object_expression(model_object.idle_task, name[:-1])
        if idle is None or not is_task_object(idle):
            raise _semantic_error(
                loaded[name[:-1]], owner, "idle_task must reference a Task object"
            )
        online = next(
            (state for state in model_object.states if state.name == ("State", "Online")),
            None,
        )
        if online is None:
            raise _semantic_error(
                loaded[name[:-1]], owner, "sched_core object requires State::Online"
            )

    for name, model_object in expanded_objects.items():
        for state in model_object.states:
            for action in state.actions:
                if action.signal != ("Action", "ResetCurrent"):
                    continue
                if (
                    name not in bootstrap_idle_tasks
                    or state.name != ("State", "OnCpu")
                    or action.parameters
                ):
                    raise _semantic_error(
                        loaded[name[:-1]],
                        object_nodes[name],
                        "Action::ResetCurrent may only be declared by BootTask in State::OnCpu",
                    )

    logical_ids = tuple(
        model_object.logical_id
        for name, model_object in expanded_objects.items()
        if is_cpu_core_object(name)
    )
    if len(set(logical_ids)) != len(logical_ids):
        duplicate = next(item for item in logical_ids if logical_ids.count(item) > 1)
        name = next(
            name
            for name, item in expanded_objects.items()
            if is_cpu_core_object(name) and item.logical_id == duplicate
        )
        raise _semantic_error(
            loaded[name[:-1]],
            object_nodes[name],
            f"duplicate CPU logical_id {duplicate}",
        )

    concrete_tasks = tuple(
        name for name in expanded_objects if is_task_object(name)
    )
    if concrete_tasks:
        if len(task_types) != 1 or len(task_flow_types) != 1:
            anchor_name = concrete_tasks[0]
            raise _semantic_error(
                loaded[anchor_name[:-1]],
                object_nodes[anchor_name],
                "Task scheduling requires exactly one Task type and one TaskFlow type",
            )
        for task in concrete_tasks:
            flows = tuple(
                name
                for name, candidate in expanded_objects.items()
                if is_task_flow_object(name)
                and candidate.parent is not None
                and resolve_object_expression(candidate.parent, name[:-1]) == task
            )
            if len(flows) != 1:
                raise _semantic_error(
                    loaded[task[:-1]],
                    object_nodes[task],
                    f"Task object {'::'.join(task)!r} requires exactly one parent TaskFlow; got {len(flows)}",
                )

    def expression_type(
        expression: ModelExpression,
        module_name: tuple[str, ...],
        source: tuple[str, ...] | None,
        parameters: dict[str, TypeKey],
        fields: dict[str, ModelField],
    ) -> TypeKey | None:
        if expression.kind == "string":
            return (("String",), ())
        if expression.kind == "call":
            callee = expression.children[0]
            if callee.kind == "member":
                owner = resolve_object_expression(callee.children[0], module_name)
                if owner is not None:
                    container = object_type(owner)
                    if container[0] in {("Relation",), ("Map",)}:
                        method = str(callee.value)
                        arguments = expression.children[1:]
                        allowed = (
                            {"contains", "has_key", "unique_value"}
                            if container[0] == ("Relation",)
                            else {"contains", "has_key", "lookup"}
                        )
                        if method not in allowed:
                            raise _semantic_error(
                                loaded[module_name],
                                object_nodes[source] if source in object_nodes else loaded[module_name].tree,
                                f"{container[0][0]} has no method {method!r}",
                            )
                        expected_arity = 2 if method == "contains" else 1
                        if len(arguments) != expected_arity:
                            raise _semantic_error(
                                loaded[module_name],
                                object_nodes[source] if source in object_nodes else loaded[module_name].tree,
                                f"{container[0][0]}.{method} expects {expected_arity} argument(s)",
                            )
                        expected_types = container[1] if method == "contains" else container[1][:1]
                        for index, (argument, expected) in enumerate(
                            zip(arguments, expected_types, strict=True)
                        ):
                            actual = expression_type(
                                argument, module_name, source, parameters, fields
                            )
                            if actual is None or not compatible(actual, expected):
                                raise _semantic_error(
                                    loaded[module_name],
                                    object_nodes[source] if source in object_nodes else loaded[module_name].tree,
                                    f"{container[0][0]}.{method} argument {index + 1} has incompatible type",
                                )
                        return (
                            container[1][1]
                            if method in {"unique_value", "lookup"}
                            else (("bool",), ())
                        )
        if expression.kind == "unary" and expression.value == "!":
            expression_type(expression.children[0], module_name, source, parameters, fields)
            return (("bool",), ())
        if expression.kind == "binary":
            expression_type(expression.children[0], module_name, source, parameters, fields)
            expression_type(expression.children[1], module_name, source, parameters, fields)
            return (("bool",), ())
        access = _flatten_access(expression)
        if access in task_owned_selectors:
            if source is None or not is_task_object(source):
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    "TaskFlowRef and ResumeTargetRef are only available in Task handlers",
                )
            if access[0][-1] == "TaskFlowRef":
                return (task_flow_types[0], ())
            # ResumeTargetRef may dynamically denote either the TaskFlow or a
            # parked user_runtime. Its signal surface is validated specially.
            return (task_flow_types[0], ())
        flow_type_candidates = {
            (tuple(interrupt_flow_selector[0]), tuple(interrupt_flow_selector[1])): interrupt_flow_types,
            (tuple(exception_flow_selector[0]), tuple(exception_flow_selector[1])): exception_flow_types,
            (tuple(syscall_flow_selector[0]), tuple(syscall_flow_selector[1])): syscall_exit_flow_types,
        }.get(
            None
            if access is None
            else (tuple(access[0]), tuple(access[1]))
        )
        if flow_type_candidates is not None:
            if len(flow_type_candidates) != 1:
                selector_name = access[0][-1]
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    f"self.{selector_name} requires exactly one matching event_flow type",
                )
            return (flow_type_candidates[0], ())
        if access == (
            ["CurrentCPU", "InterruptControlRef"],
            ["member"],
        ):
            if len(interrupt_control_types) != 1:
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    "CurrentCPU.InterruptControlRef requires exactly one InterruptControl type",
                )
            return (interrupt_control_types[0], ())
        if access == (
            ["CurrentTaskRef", "UserAppRuntimeRef"],
            ["member"],
        ):
            if len(task_types) != 1:
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    "CurrentTaskRef.UserAppRuntimeRef requires exactly one declared Task type",
                )
            if len(user_runtime_types) != 1:
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    "CurrentTaskRef.UserAppRuntimeRef requires exactly one declared user_runtime type",
                )
            return (user_runtime_types[0], ())
        if expression.kind == "identifier":
            identifier = str(expression.value)
            if identifier in parameters:
                return parameters[identifier]
            if identifier == "self" and source is not None:
                return object_type(source)
            if identifier == "CurrentTaskRef":
                if len(task_types) != 1:
                    raise _semantic_error(
                        loaded[module_name],
                        loaded[module_name].tree,
                        "CurrentTaskRef requires exactly one declared Task type",
                    )
                return (task_types[0], ())
            if identifier == "CurrentCPU":
                if len(cpu_core_types) != 1:
                    raise _semantic_error(
                        loaded[module_name],
                        loaded[module_name].tree,
                        "CurrentCPU requires exactly one declared cpu_core type",
                    )
                return (cpu_core_types[0], ())
        if (
            access is not None
            and source is not None
            and access[0][:1] == ["self"]
            and access[1] == ["member"]
        ):
            field = fields.get(access[0][1])
            return None if field is None else resolve_type(field.type, source[:-1])
        object_name = resolve_object_expression(expression, module_name)
        return None if object_name is None else object_type(object_name)

    def binding_type_expression(value: TypeKey) -> ModelTypeExpression:
        name, arguments = value
        return ModelTypeExpression(
            name,
            tuple(binding_type_expression(argument) for argument in arguments),
        )

    def rewrite_handler_bindings(
        model_object: ModelObject, handler: ModelTransition | ModelAction
    ) -> ModelTransition | ModelAction:
        module_name = model_object.name[:-1]
        fields = {field.name: field for field in model_object.attrs or ()}
        environment = {
            parameter.name: resolve_type(parameter.type, module_name)
            for parameter in handler.parameters
        }
        environment["self"] = object_type(model_object.name)
        binding_blocks = tuple(block for block in handler.blocks if block.kind == "binds")
        if len(binding_blocks) > 1:
            raise _semantic_error(
                loaded[module_name], object_nodes[model_object.name], "duplicate binds block"
            )
        rewritten: dict[int, ModelHandlerBlock] = {}
        for block in binding_blocks:
            values: list[ModelBinding] = []
            for binding in block.bindings:
                if binding.name in environment:
                    raise _semantic_error(
                        loaded[module_name],
                        object_nodes[model_object.name],
                        f"binding {binding.name!r} conflicts with a handler parameter or local binding",
                    )
                expression = binding.expression
                if expression.kind != "call" or expression.children[0].kind != "member":
                    raise _semantic_error(
                        loaded[module_name],
                        object_nodes[model_object.name],
                        "binding right-hand side must be Relation.unique_value or Map.lookup",
                    )
                callee = expression.children[0]
                owner = resolve_object_expression(callee.children[0], module_name)
                container = None if owner is None else object_type(owner)
                method = str(callee.value)
                if container is None or not (
                    container[0] == ("Relation",) and method == "unique_value"
                    or container[0] == ("Map",) and method == "lookup"
                ):
                    raise _semantic_error(
                        loaded[module_name],
                        object_nodes[model_object.name],
                        "binding right-hand side must match Relation.unique_value or Map.lookup",
                    )
                inferred = expression_type(
                    expression, module_name, model_object.name, environment, fields
                )
                assert inferred is not None
                values.append(replace(binding, type=binding_type_expression(inferred)))
                environment[binding.name] = inferred
            rewritten[id(block)] = replace(block, bindings=tuple(values))
        blocks = tuple(rewritten.get(id(block), block) for block in handler.blocks)
        return replace(handler, blocks=blocks)

    # Binding types are part of schema-v13 IR, so infer and freeze them before
    # the ordinary handler expression/signature validation below.
    for name, model_object in tuple(expanded_objects.items()):
        states = tuple(
            replace(
                state,
                transitions=tuple(
                    rewrite_handler_bindings(model_object, handler)
                    for handler in state.transitions
                ),
                actions=tuple(
                    rewrite_handler_bindings(model_object, handler)
                    for handler in state.actions
                ),
            )
            for state in model_object.states
        )
        expanded_objects[name] = replace(model_object, states=states)

    def rewrite_type_handler_bindings(
        model_type: ModelType, handler: ModelTransition | ModelAction
    ) -> ModelTransition | ModelAction:
        module_name = model_type.name[:-1]
        fields = {field.name: field for field in model_type.fields or ()}
        environment = {
            parameter.name: resolve_type(parameter.type, module_name)
            for parameter in handler.parameters
        }
        environment["self"] = (model_type.name, ())
        rewritten: dict[int, ModelHandlerBlock] = {}
        for block in (item for item in handler.blocks if item.kind == "binds"):
            values: list[ModelBinding] = []
            for binding in block.bindings:
                if binding.name in environment:
                    raise _semantic_error(
                        loaded[module_name],
                        type_nodes[model_type.name],
                        f"binding {binding.name!r} conflicts with a handler parameter or local binding",
                    )
                expression = binding.expression
                if expression.kind != "call" or expression.children[0].kind != "member":
                    raise _semantic_error(
                        loaded[module_name],
                        type_nodes[model_type.name],
                        "binding right-hand side must be Relation.unique_value or Map.lookup",
                    )
                callee = expression.children[0]
                relation_owner = resolve_object_expression(
                    callee.children[0], module_name
                )
                container = (
                    None if relation_owner is None else object_type(relation_owner)
                )
                method = str(callee.value)
                if container is None or not (
                    container[0] == ("Relation",) and method == "unique_value"
                    or container[0] == ("Map",) and method == "lookup"
                ):
                    raise _semantic_error(
                        loaded[module_name],
                        type_nodes[model_type.name],
                        "binding right-hand side must match Relation.unique_value or Map.lookup",
                    )
                inferred = expression_type(
                    expression, module_name, None, environment, fields
                )
                assert inferred is not None
                values.append(replace(binding, type=binding_type_expression(inferred)))
                environment[binding.name] = inferred
            rewritten[id(block)] = replace(block, bindings=tuple(values))
        return replace(
            handler,
            blocks=tuple(rewritten.get(id(block), block) for block in handler.blocks),
        )

    for name, model_type in tuple(expanded_types.items()):
        states = tuple(
            replace(
                state,
                transitions=tuple(
                    rewrite_type_handler_bindings(model_type, handler)
                    for handler in state.transitions
                ),
                actions=tuple(
                    rewrite_type_handler_bindings(model_type, handler)
                    for handler in state.actions
                ),
            )
            for state in model_type.states
        )
        expanded_types[name] = replace(model_type, states=states)

    signatures: dict[
        tuple[tuple[str, ...], tuple[str, ...]], tuple[ModelParameter, ...]
    ] = {}
    for name, model_object in expanded_objects.items():
        base_type = object_type(name)
        if base_type[0] == ("Collection",):
            signatures[(name, ("Action", "Enqueue"))] = (
                ModelParameter(
                    "item",
                    ModelTypeExpression(base_type[1][0][0]),
                ),
            )
        if is_sched_core_object(name):
            signatures[(name, ("Action", "Enqueue"))] = ()
            signatures[(name, ("Action", "Dequeue"))] = ()
        for state in model_object.states:
            for handler in (*state.transitions, *state.actions):
                key = (name, handler.signal)
                prior = signatures.get(key)
                if prior is not None and prior != handler.parameters:
                    raise _semantic_error(
                        loaded[name[:-1]],
                        object_nodes[name],
                        f"handler {'::'.join(handler.signal)!r} has inconsistent "
                        "parameter signatures across states",
                    )
                signatures[key] = handler.parameters

    def parameter_types(
        parameters: tuple[ModelParameter, ...], module_name: tuple[str, ...]
    ) -> dict[str, TypeKey]:
        return {
            parameter.name: resolve_type(parameter.type, module_name)
            for parameter in parameters
        }

    def validate_call(
        signal: ModelSignal,
        module_name: tuple[str, ...],
        source: tuple[str, ...] | None,
        environment: dict[str, TypeKey],
        fields: dict[str, ModelField],
        owner: Tree,
    ) -> None:
        flattened = _flatten_access(signal.target)
        if flattened in task_owned_selectors:
            if signal.arguments:
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    "Task-owned resume selectors do not accept arguments",
                )
            return
        if flattened in event_flow_selectors:
            return
        target_name = resolve_object_expression(signal.target, module_name)
        if target_name is None:
            if signal.arguments and flattened not in (
                (["CurrentTaskRef"], []),
                (["CurrentCPU"], []),
            ):
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    f"dynamic signal {'::'.join(signal.signal)!r} does not accept arguments",
                )
            target_type = expression_type(
                signal.target, module_name, source, environment, fields
            )
            if (
                target_type is None
                or not (
                    len(task_types) == 1
                    and compatible(target_type, (task_types[0], ()))
                    or len(user_runtime_types) == 1
                    and compatible(target_type, (user_runtime_types[0], ()))
                    or len(cpu_core_types) == 1
                    and compatible(target_type, (cpu_core_types[0], ()))
                    or len(interrupt_control_types) == 1
                    and compatible(target_type, (interrupt_control_types[0], ()))
                )
            ):
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    "dynamic signal target must have Task, CPU, user_runtime, or InterruptControl type",
                )
            return
        signature = signatures.get((target_name, signal.signal))
        if signature is None:
            if signal.arguments:
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    f"parameterized signal {'::'.join(signal.signal)!r} has no "
                    "resolvable handler signature",
                )
            return
        if len(signal.arguments) != len(signature):
            raise _semantic_error(
                loaded[module_name],
                owner,
                f"signal {'::'.join(signal.signal)!r} expects {len(signature)} "
                f"argument(s), got {len(signal.arguments)}",
            )
        for index, (argument, parameter) in enumerate(
            zip(signal.arguments, signature, strict=True)
        ):
            actual = expression_type(
                argument, module_name, source, environment, fields
            )
            expected = resolve_type(parameter.type, target_name[:-1])
            if actual is None:
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    f"cannot infer type of signal argument {index + 1}",
                )
            if not compatible(actual, expected):
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    f"signal argument {index + 1} has incompatible object type",
                )

    for name, model_object in expanded_objects.items():
        owner = object_nodes[name]
        module_name = name[:-1]
        fields = {field.name: field for field in model_object.attrs or ()}
        for invariant in (
            expression
            for state in model_object.states
            for block in state.invariants
            for expression in block
        ):
            for nested in _walk_expression(invariant):
                if nested.kind != "call" or nested.children[0].kind != "member":
                    continue
                callee = nested.children[0]
                relation_owner = resolve_object_expression(
                    callee.children[0], module_name
                )
                if relation_owner is None or object_type(relation_owner)[0] not in {
                    ("Relation",),
                    ("Map",),
                }:
                    continue
                if str(callee.value) in {"unique_value", "lookup"}:
                    raise _semantic_error(
                        loaded[module_name],
                        owner,
                        "unique_value and lookup may only appear in a binds block",
                    )
                expression_type(nested, module_name, name, {}, fields)
        for field in fields.values():
            declared_type = resolve_type(field.type, module_name)
            if field.mutable and declared_type == (("String",), ()):
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    "mutable String fields are not supported",
                )
            if field.default is not None:
                actual = expression_type(
                    field.default, module_name, name, {}, fields
                )
                if actual is None or not compatible(actual, declared_type):
                    raise _semantic_error(
                        loaded[module_name],
                        owner,
                        f"default value for field {field.name!r} has incompatible type",
                    )
        for state in model_object.states:
            for handler in (*state.transitions, *state.actions):
                environment = parameter_types(handler.parameters, module_name)
                relation_binding_names: set[str] = set()
                for binding in (
                    binding
                    for block in handler.blocks
                    if block.kind == "binds"
                    for binding in block.bindings
                ):
                    environment[binding.name] = resolve_type(binding.type, module_name)
                    relation_binding_names.add(binding.name)
                switch_count = 0
                for block in handler.blocks:
                    if block.kind == "binds":
                        continue
                    for expression in block.expressions:
                        for nested in _walk_expression(expression):
                            if nested.kind != "call" or nested.children[0].kind != "member":
                                continue
                            callee = nested.children[0]
                            relation_owner = resolve_object_expression(
                                callee.children[0], module_name
                            )
                            if relation_owner is None or object_type(relation_owner)[0] not in {
                                ("Relation",),
                                ("Map",),
                            }:
                                continue
                            method = str(callee.value)
                            if method in {"unique_value", "lookup"}:
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "unique_value and lookup may only appear in a binds block",
                                )
                            if block.kind == "establishes" and method != "contains":
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "establishes only accepts Relation/Map.contains effects",
                                )
                            expression_type(
                                nested,
                                module_name,
                                name,
                                environment,
                                fields,
                            )
                    if block.kind == "switches":
                        switch_count += 1
                        if (
                            switch_count > 1
                            or not isinstance(handler, ModelAction)
                            or not is_sched_core_object(name)
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                "switches is allowed at most once in a sched_core Action handler",
                            )
                        assert block.switches is not None
                        if block.switches in environment:
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                f"switches binding {block.switches!r} conflicts with an existing binding",
                            )
                        environment[block.switches] = (task_types[0], ())
                        continue
                    for signal in block.signals:
                        flattened_target = _flatten_access(signal.target)
                        task_selector = flattened_target in task_owned_selectors
                        if task_selector:
                            if not is_task_object(name):
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "TaskFlowRef and ResumeTargetRef are only available in Task handlers",
                                )
                            if signal.arguments or signal.signal != ("Action", "Enter"):
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "Task-owned resume selectors only accept parameterless Action::Enter",
                                )
                            if signal.mode != "resume":
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "Task-owned resume selector Action::Enter must use resumes",
                                )
                        if flattened_target in event_flow_selectors:
                            expected_handler = {
                                "InterruptFlowRef": ("Action", "OnInterrupt"),
                                "ExceptionFlowRef": ("Action", "OnException"),
                                "SyscallExitFlowRef": ("Action", "OnSyscallExit"),
                            }[flattened_target[0][-1]]
                            invalid_arguments = (
                                bool(signal.arguments)
                                if flattened_target[0][-1] != "SyscallExitFlowRef"
                                else len(signal.arguments) != len(handler.parameters)
                            )
                            if (
                                not is_cpu_core_object(name)
                                or handler.signal != expected_handler
                                or block.kind != "resumes"
                                or signal.mode != "resume"
                                or signal.signal != ("Action", "Enter")
                                or invalid_arguments
                            ):
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "Event FlowRef selectors are only available in their matching CPU receive handler",
                                )
                        if flattened_target == (
                            ["CurrentCPU", "InterruptControlRef"],
                            ["member"],
                        ) and (
                            signal.signal
                            not in {
                                ("Action", "MaskAll"),
                                ("Action", "ClearPending"),
                                ("Action", "Unmask"),
                            }
                            or signal.arguments
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                "InterruptControlRef only accepts parameterless MaskAll, ClearPending, and Unmask actions",
                            )
                        runtime_selector = flattened_target == (
                            ["CurrentTaskRef", "UserAppRuntimeRef"],
                            ["member"],
                        )
                        if runtime_selector:
                            if len(task_types) != 1:
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "CurrentTaskRef.UserAppRuntimeRef requires exactly one Task type",
                                )
                            if len(user_runtime_types) != 1:
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "CurrentTaskRef.UserAppRuntimeRef requires exactly one user_runtime type",
                                )
                        if (
                            flattened_target is not None
                            and len(flattened_target[0]) == 1
                            and not flattened_target[1]
                        ):
                            dynamic = flattened_target[0][0]
                            if dynamic in {"CurrentTaskRef", "CurrentCPU"}:
                                pass
                            elif dynamic not in environment:
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    f"dynamic target binding {dynamic!r} is not in scope",
                                )
                        target_name = resolve_object_expression(
                            signal.target, module_name
                        )
                        if (
                            target_name is not None
                            and is_task_flow_object(target_name)
                            and signal.signal == ("Action", "Enter")
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                "TaskFlow Action::Enter must use a Task-owned TaskFlowRef or ResumeTargetRef selector",
                            )
                        if (
                            target_name is not None
                            and is_sched_core_object(target_name)
                            and signal.signal in core_actions
                        ):
                            if (
                                is_task_object(name)
                                and isinstance(handler, ModelTransition)
                                and handler.signal
                                in {
                                    ("Transition", "Suspend"),
                                    ("Transition", "Resume"),
                                }
                            ):
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "Task Suspend/Resume handlers must not call sched_core Enqueue/Dequeue",
                                )
                            if signal.arguments:
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    f"sched_core {'::'.join(signal.signal)} accepts no arguments",
                                )
                            if not is_task_object(name):
                                raise _semantic_error(
                                    loaded[module_name],
                                    owner,
                                    "sched_core Enqueue/Dequeue signal source must be a Task object",
                                )
                        validate_call(
                            signal, module_name, name, environment, fields, owner
                        )
                    for update in block.updates:
                        access = _flatten_access(update.target)
                        if (
                            access is not None
                            and len(access[0]) == 1
                            and not access[1]
                            and access[0][0] in relation_binding_names
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                f"binding {access[0][0]!r} is read-only and cannot be updated",
                            )
                        if access is not None and any(
                            segment
                            in {
                                "CurrentCPU",
                                "TaskFlowRef",
                                "ResumeTargetRef",
                                "InterruptFlowRef",
                                "ExceptionFlowRef",
                                "SyscallExitFlowRef",
                                "InterruptControlRef",
                            }
                            for segment in access[0]
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                "TaskFlowRef and ResumeTargetRef are read-only and cannot be updated",
                            )
                        if (
                            access is None
                            or access[0][:1] != ["self"]
                            or access[1] != ["member"]
                            or len(access[0]) != 2
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                "updates target must have the form self.<mutable-field>",
                            )
                        field = fields.get(access[0][1])
                        if field is None or not field.mutable:
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                f"updates target {access[0][1]!r} is not a mutable field",
                            )
                        actual = expression_type(
                            update.value, module_name, name, environment, fields
                        )
                        expected = resolve_type(field.type, module_name)
                        if actual is None or not compatible(actual, expected):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                f"update for field {field.name!r} has incompatible type",
                            )

    for module in lowered:
        owner_module = loaded[module.name]
        for external in module.externals:
            owner = next(
                item
                for item in owner_module.tree.children
                if isinstance(item, Tree)
                and item.data == "external_declaration"
                and str(item.children[0]) == external.name[-1]
            )
            for signal in external.signals:
                if _flatten_access(signal.target) in task_owned_selectors:
                    raise _semantic_error(
                        owner_module,
                        owner,
                        "TaskFlowRef and ResumeTargetRef are only available in Task handlers",
                    )
                target_name = resolve_object_expression(signal.target, module.name)
                if (
                    target_name is not None
                    and is_task_flow_object(target_name)
                    and signal.signal == ("Action", "Enter")
                ):
                    raise _semantic_error(
                        owner_module,
                        owner,
                        "TaskFlow Action::Enter must use a Task-owned TaskFlowRef or ResumeTargetRef selector",
                    )
                if (
                    target_name is not None
                    and is_sched_core_object(target_name)
                    and signal.signal in core_actions
                ):
                    raise _semantic_error(
                        owner_module,
                        owner,
                        "sched_core Enqueue/Dequeue signal source must be a Task object",
                    )
                validate_call(signal, module.name, None, {}, {}, owner)

    return tuple(
        ModelModule(
            module.name,
            module.predicates,
            tuple(expanded_types[item.name] for item in module.types),
            tuple(expanded_objects[item.name] for item in module.objects),
            module.externals,
        )
        for module in lowered
    )


def _lower_ast(document: ModelSpec, modules: tuple[LoadedModule, ...]) -> ModelIR:
    try:
        lowered = tuple(_lower_module(module) for module in modules)
        lowered = _expand_inheritance(lowered, modules)
        return ModelIR(
            schema_version=SCHEMA_VERSION,
            entry=ModelEntry(origin=document.origin.name.parts, spec=document.spec.name.parts),
            modules=lowered,
        )
    except ModelIRValidationError as exc:
        # Cross-module validation cannot always be tied to one declaration without
        # duplicating the IR validator. The entry diagnostic remains deterministic.
        raise error(modules[0].path, 1, 1, str(exc)) from exc


def compile_spec_with_inputs(path: str | Path) -> tuple[ModelIR, tuple[Path, ...]]:
    """Compile a specification and return every source file it declared."""

    source_path = Path(path)
    document, modules = _read_entry(source_path)
    model = _lower_ast(document, modules)
    return model, (source_path, *(module.path for module in modules))


def compile_spec(path: str | Path) -> ModelIR:
    """Read a UTF-8 entry specification and compile it to Model IR."""

    return compile_spec_with_inputs(path)[0]
