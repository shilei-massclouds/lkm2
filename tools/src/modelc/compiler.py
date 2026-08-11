"""Compilation pipeline from a model-root specification to Model IR v8."""

from __future__ import annotations

import json
from pathlib import Path

from lark import Token, Tree

from model_ir import (
    SCHEMA_VERSION,
    ModelAction,
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
    selector_names: frozenset[str] = frozenset(),
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
        and raw_target[0] in selector_names
        and raw_target[0] not in bindings
    ):
        raise _semantic_error(
            module,
            node,
            f"selects binding {raw_target[0]!r} is not in scope before its declaration",
        )
    if len(raw_target) == 1 and raw_target[0] in {*bindings, "CurrentTaskRef"}:
        target = ModelExpression("identifier", raw_target[0])
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
    selector_names = frozenset(
        str(child.children[0])
        for child in owner.children
        if isinstance(child, Tree) and child.data == "selects_statement"
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
                _signal(module, statement, source, mode, imports, frozenset(bindings), selector_names)
                for statement in _signal_nodes(child)
            )
            result.append(ModelHandlerBlock(rule.removesuffix("_block"), signals=signals))
        elif rule == "yields_statement":
            result.append(
                ModelHandlerBlock(
                    "yields",
                    signals=(
                        _signal(module, child.children[0], source, "yield", imports, frozenset(bindings), selector_names),
                    ),
                )
            )
        elif rule == "resumes_statement":
            result.append(
                ModelHandlerBlock(
                    "resumes",
                    signals=(
                        _signal(module, child.children[0], source, "resume", imports, frozenset(bindings), selector_names),
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
        elif rule == "selects_statement":
            binding = str(child.children[0])
            if binding in bindings:
                raise _semantic_error(
                    module, child, f"duplicate selects binding {binding!r}"
                )
            result.append(ModelHandlerBlock("selects", selects=binding))
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
                block.selects,
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
        expanded = ModelType(
            raw.name,
            fields,
            raw.base_type,
            continuation,
            initial_state,
            states,
            sched_core,
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
            if raw.name[-1] == "CurrentTaskRef":
                raise _semantic_error(
                    module,
                    node,
                    "CurrentTaskRef is a reserved runtime selector and must not be "
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
            if abstract:
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
            )

    continuation_names = {
        name for name, model_object in expanded_objects.items() if model_object.continuation
    }

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
            not is_continuation or signal.signal != ("Action", "Enter")
        ):
            raise _semantic_error(
                module,
                owner,
                "resumes must target Action::Enter on a continuation object",
            )
        if not is_continuation:
            return
        if signal.mode == "resume" and signal.signal == ("Action", "Enter"):
            return
        if not (
            signal.signal[0] == "Action"
            and signal.source == target_name
            and signal.mode == "drive"
        ):
            raise _semantic_error(
                module,
                owner,
                "only Action::Enter may enter a continuation, and continuation entry "
                "from outside must use resumes Action::Enter; "
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
        if expression.name == ("Collection",):
            if len(expression.arguments) != 1:
                raise _semantic_error(
                    loaded[module_name],
                    loaded[module_name].tree,
                    "Collection requires exactly one type argument",
                )
            return (
                ("Collection",),
                tuple(resolve_type(item, module_name) for item in expression.arguments),
            )
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
        if actual_arguments or actual_name == ("Collection",):
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

    task_types = tuple(name for name in raw_types if name[-1] == "Task")
    task_flow_types = tuple(name for name in raw_types if name[-1] == "TaskFlow")

    def is_task_object(name: tuple[str, ...]) -> bool:
        return len(task_types) == 1 and compatible(
            object_type(name), (task_types[0], ())
        )

    def is_task_flow_object(name: tuple[str, ...]) -> bool:
        return len(task_flow_types) == 1 and compatible(
            object_type(name), (task_flow_types[0], ())
        )

    def is_sched_core_object(name: tuple[str, ...]) -> bool:
        type_name = object_type(name)[0]
        return type_name in expanded_types and expanded_types[type_name].sched_core

    core_actions = {("Action", "Enqueue"), ("Action", "Dequeue")}
    for name, model_type in raw_types.items():
        effective = expanded_types[name]
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
        if sched_core and model_object.idle_task is None:
            raise _semantic_error(
                loaded[name[:-1]], owner, "sched_core object requires idle_task"
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
        if expression.kind == "identifier":
            identifier = str(expression.value)
            if identifier in parameters:
                return parameters[identifier]
            if identifier == "self" and source is not None:
                return object_type(source)
            if identifier == "CurrentTaskRef":
                task_types = tuple(name for name in raw_types if name[-1] == "Task")
                if len(task_types) != 1:
                    raise _semantic_error(
                        loaded[module_name],
                        loaded[module_name].tree,
                        "CurrentTaskRef requires exactly one declared Task type",
                    )
                return (task_types[0], ())
        access = _flatten_access(expression)
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
        target_name = resolve_object_expression(signal.target, module_name)
        if target_name is None:
            if signal.arguments:
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    f"dynamic signal {'::'.join(signal.signal)!r} does not accept arguments",
                )
            target_type = expression_type(
                signal.target, module_name, source, environment, fields
            )
            task_types = tuple(name for name in raw_types if name[-1] == "Task")
            if (
                target_type is None
                or len(task_types) != 1
                or not compatible(target_type, (task_types[0], ()))
            ):
                raise _semantic_error(
                    loaded[module_name],
                    owner,
                    "dynamic signal target must have Task type",
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
        for field in fields.values():
            declared_type = resolve_type(field.type, module_name)
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
                selection_count = 0
                for block in handler.blocks:
                    if block.kind == "selects":
                        selection_count += 1
                        if (
                            selection_count > 1
                            or not isinstance(handler, ModelAction)
                            or not is_sched_core_object(name)
                        ):
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                "selects is allowed at most once in a sched_core Action handler",
                            )
                        assert block.selects is not None
                        if block.selects in environment:
                            raise _semantic_error(
                                loaded[module_name],
                                owner,
                                f"selects binding {block.selects!r} conflicts with an existing binding",
                            )
                        environment[block.selects] = (task_types[0], ())
                        continue
                    for signal in block.signals:
                        flattened_target = _flatten_access(signal.target)
                        if (
                            flattened_target is not None
                            and len(flattened_target[0]) == 1
                            and not flattened_target[1]
                        ):
                            dynamic = flattened_target[0][0]
                            if dynamic == "CurrentTaskRef":
                                if not is_sched_core_object(name):
                                    raise _semantic_error(
                                        loaded[module_name],
                                        owner,
                                        "CurrentTaskRef is only available in a sched_core handler",
                                    )
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
                            and is_sched_core_object(target_name)
                            and signal.signal in core_actions
                        ):
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
                target_name = resolve_object_expression(signal.target, module.name)
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
