"""Lark parser and parse-tree to AST conversion."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from lark import Lark, Transformer, UnexpectedCharacters, UnexpectedInput, UnexpectedToken
from lark.tree import Meta
from lark.visitors import v_args

from .ast import (
    ModelSpec,
    OriginDeclaration,
    QualifiedName,
    SourceSpan,
    SpecDeclaration,
)
from .diagnostics import error


def _load_parser() -> Lark:
    grammar = resources.files("modelc").joinpath("grammar.lark").read_text(
        encoding="utf-8"
    )
    return Lark(grammar, parser="lalr", propagate_positions=True)


_PARSER = _load_parser()


def _span(meta: Meta) -> SourceSpan:
    return SourceSpan(
        start_line=meta.line,
        start_column=meta.column,
        end_line=meta.end_line,
        end_column=meta.end_column,
    )


@v_args(meta=True)
class _ASTBuilder(Transformer[Any, Any]):
    def module_name(self, meta: Meta, children: list[Any]) -> QualifiedName:
        return QualifiedName(tuple(str(child) for child in children), _span(meta))

    def qualified_name(self, meta: Meta, children: list[Any]) -> QualifiedName:
        return QualifiedName(tuple(str(child) for child in children), _span(meta))

    def spec_declaration(
        self, meta: Meta, children: list[Any]
    ) -> SpecDeclaration:
        return SpecDeclaration(children[0], _span(meta))

    def origin_declaration(
        self, meta: Meta, children: list[Any]
    ) -> OriginDeclaration:
        return OriginDeclaration(children[0], _span(meta))

    def start(self, meta: Meta, children: list[Any]) -> ModelSpec:
        del meta
        return ModelSpec(spec=children[0], origin=children[1])


def _end_position(source: str) -> tuple[int, int]:
    if not source:
        return (1, 1)
    lines = source.splitlines(keepends=True)
    if source.endswith(("\n", "\r")):
        return (len(lines) + 1, 1)
    return (len(lines), len(lines[-1]) + 1)


def _syntax_message(exc: UnexpectedInput) -> str:
    if isinstance(exc, UnexpectedCharacters):
        return f"unexpected character {exc.char!r}"
    if isinstance(exc, UnexpectedToken):
        if exc.token.type == "$END":
            return "unexpected end of input"
        return f"unexpected token {str(exc.token)!r}"
    return "invalid syntax"


def parse_spec(source: str, path: str | Path = "<string>") -> ModelSpec:
    """Parse one entry specification into its source-oriented AST."""

    try:
        tree = _PARSER.parse(source)
    except UnexpectedInput as exc:
        line = getattr(exc, "line", 0) or 0
        column = getattr(exc, "column", 0) or 0
        if line < 1 or column < 1:
            line, column = _end_position(source)
        raise error(path, line, column, _syntax_message(exc)) from exc

    result = _ASTBuilder().transform(tree)
    if not isinstance(result, ModelSpec):  # Defensive guard around the grammar boundary.
        raise RuntimeError("parser did not produce a model specification")
    return result
