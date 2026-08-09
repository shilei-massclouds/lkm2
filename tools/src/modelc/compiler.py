"""Compilation pipeline from a model-root specification to Model IR v4."""

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


def _signal(
    module: LoadedModule,
    node: Tree | Token,
    source: tuple[str, ...],
    mode: str,
    imports: dict[str, tuple[str, ...]],
) -> ModelSignal:
    access = _flatten_access(_lower_expression(node))
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
    target = _resolve_target(tuple(segments[:-2]), module.name, imports)
    signal = canonicalize_signal_name((segments[-2], segments[-1]))
    return ModelSignal(source, target, signal, mode)


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


def _fields(node: Tree) -> tuple[ModelField, ...]:
    fields = []
    for child in node.children:
        if isinstance(child, Tree) and child.data == "field_declaration":
            fields.append(ModelField(str(child.children[0]), _type_expression(child.children[1])))
    return tuple(fields)


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
            expressions = next(grandchild for grandchild in child.children if isinstance(grandchild, Tree))
            signals = tuple(
                _signal(module, statement, source, mode, imports)
                for statement in expressions.children
            )
            result.append(ModelHandlerBlock(rule.removesuffix("_block"), signals=signals))
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
                transitions.append(
                    ModelTransition(
                        signal=_transition_handler_signal(module, handler.children[0]),
                        target_state=_special_name(module, handler.children[1], "State", "target state"),
                        blocks=_handler_blocks(module, handler, object_name, imports),
                    )
                )
        elif child.data == "actions_block":
            for handler in child.children:
                assert isinstance(handler, Tree)
                actions.append(
                    ModelAction(
                        signal=_special_name(
                            module, handler.children[0], "Action", "accepted signal"
                        ),
                        blocks=_handler_blocks(module, handler, object_name, imports),
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
        else ("State", "Base") if states else None
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
    return ModelObject(
        name=name,
        base_type=_type_expression(node.children[1]),
        initial_state=initial_state,
        parent=None if parent_node is None else _lower_expression(parent_node.children[0]),
        source=None if source_node is None else _lower_expression(source_node.children[0]),
        attrs=None if not attrs_nodes else _fields(attrs_nodes[0]),
        states=states,
        references=tuple(references),
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


def _model_type(module: LoadedModule, node: Tree) -> ModelType:
    opaque = any(isinstance(child, Tree) and child.data == "type_tail" for child in node.children)
    return ModelType(
        module.name + (str(node.children[0]),),
        None if opaque else _fields(node),
    )


def _external(
    module: LoadedModule, node: Tree, imports: dict[str, tuple[str, ...]]
) -> ModelExternal:
    name = module.name + (str(node.children[0]),)
    signals: list[ModelSignal] = []
    for block in node.children[1:]:
        assert isinstance(block, Tree)
        mode = "drive" if block.data == "drives_block" else "emit"
        expression_block = next(child for child in block.children if isinstance(child, Tree))
        signals.extend(
            _signal(module, statement, name, mode, imports)
            for statement in expression_block.children
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
            types.append(_model_type(module, item))
        elif item.data == "object_declaration":
            objects.append(_object(module, item, imports))
        elif item.data == "external_declaration":
            externals.append(_external(module, item, imports))
    try:
        return ModelModule(module.name, tuple(predicates), tuple(types), tuple(objects), tuple(externals))
    except ModelIRValidationError as exc:
        raise _semantic_error(module, module.tree, str(exc)) from exc


def _lower_ast(document: ModelSpec, modules: tuple[LoadedModule, ...]) -> ModelIR:
    try:
        lowered = tuple(_lower_module(module) for module in modules)
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
