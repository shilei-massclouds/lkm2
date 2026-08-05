"""Strict parsing for module specification files."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from lark import (
    Lark,
    Token,
    Tree,
    UnexpectedCharacters,
    UnexpectedInput,
    UnexpectedToken,
)

from .diagnostics import error


@dataclass(frozen=True, slots=True)
class Location:
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class ModuleDeclaration:
    name: str
    location: Location


@dataclass(frozen=True, slots=True)
class UseDeclaration:
    parts: tuple[str, ...]
    locations: tuple[Location, ...]
    location: Location


@dataclass(frozen=True, slots=True)
class ParsedModule:
    declarations: tuple[ModuleDeclaration, ...]
    uses: tuple[UseDeclaration, ...]


def _load_parser() -> Lark:
    grammar = resources.files("modelc").joinpath("module_grammar.lark").read_text(
        encoding="utf-8"
    )
    return Lark(grammar, parser="lalr", propagate_positions=True)


_PARSER = _load_parser()


class _LexicalValidator:
    _OPENERS = {"(": ")", "[": "]", "{": "}"}
    _CLOSERS = {closer: opener for opener, closer in _OPENERS.items()}

    def __init__(self, source: str, path: str | Path) -> None:
        self.source = source
        self.path = path
        self.index = 0
        self.line = 1
        self.column = 1
        self.delimiters: list[tuple[str, Location]] = []

    def validate(self) -> None:
        while not self._at_end():
            if self.source.startswith("//", self.index):
                self._skip_line_comment()
            elif self.source.startswith("/*", self.index):
                self._skip_block_comment()
            elif self._peek() == '"':
                self._skip_string()
            elif self._peek() in self._OPENERS:
                self.delimiters.append((self._peek(), self._location()))
                self._advance()
            elif self._peek() in self._CLOSERS:
                self._close_delimiter()
            else:
                self._advance()

        if self.delimiters:
            opener, location = self.delimiters[-1]
            raise error(
                self.path,
                location.line,
                location.column,
                f"unclosed delimiter {opener!r}",
            )

    def _skip_line_comment(self) -> None:
        self._advance()
        self._advance()
        while not self._at_end() and self._peek() not in "\r\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        location = self._location()
        self._advance()
        self._advance()
        while not self._at_end() and not self.source.startswith("*/", self.index):
            self._advance()
        if self._at_end():
            raise error(
                self.path,
                location.line,
                location.column,
                "unterminated block comment",
            )
        self._advance()
        self._advance()

    def _skip_string(self) -> None:
        location = self._location()
        self._advance()
        while not self._at_end():
            character = self._peek()
            if character == '"':
                self._advance()
                return
            if character in "\r\n":
                raise error(
                    self.path,
                    location.line,
                    location.column,
                    "unterminated string literal",
                )
            if character == "\\":
                self._advance()
                if self._at_end():
                    break
            self._advance()
        raise error(
            self.path,
            location.line,
            location.column,
            "unterminated string literal",
        )

    def _close_delimiter(self) -> None:
        closer = self._peek()
        location = self._location()
        if not self.delimiters:
            raise error(
                self.path,
                location.line,
                location.column,
                f"unmatched closing delimiter {closer!r}",
            )
        opener, _ = self.delimiters[-1]
        if opener != self._CLOSERS[closer]:
            raise error(
                self.path,
                location.line,
                location.column,
                f"mismatched closing delimiter {closer!r}; "
                f"expected {self._OPENERS[opener]!r}",
            )
        self.delimiters.pop()
        self._advance()

    def _location(self) -> Location:
        return Location(self.line, self.column)

    def _at_end(self) -> bool:
        return self.index >= len(self.source)

    def _peek(self) -> str:
        return self.source[self.index]

    def _advance(self) -> None:
        character = self.source[self.index]
        self.index += 1
        if character == "\r":
            if self.index < len(self.source) and self.source[self.index] == "\n":
                self.index += 1
            self.line += 1
            self.column = 1
        elif character == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1


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
    return "invalid module syntax"


def _token_locations(tokens: list[Token]) -> tuple[Location, ...]:
    return tuple(Location(token.line, token.column) for token in tokens)


def parse_module(source: str, path: str | Path) -> ParsedModule:
    """Parse a complete module and extract its graph-level declarations."""

    _LexicalValidator(source, path).validate()
    try:
        tree = _PARSER.parse(source)
    except UnexpectedInput as exc:
        line = getattr(exc, "line", 0) or 0
        column = getattr(exc, "column", 0) or 0
        if line < 1 or column < 1:
            line, column = _end_position(source)
        raise error(path, line, column, _syntax_message(exc)) from exc

    declarations: list[ModuleDeclaration] = []
    uses: list[UseDeclaration] = []
    declared_names: set[str] = set()
    for item in tree.children:
        if not isinstance(item, Tree):
            continue
        location = Location(item.meta.line, item.meta.column)
        if item.data == "spec_declaration":
            name = str(item.children[0])
            if name in declared_names:
                raise error(
                    path,
                    location.line,
                    location.column,
                    f"duplicate module declaration {name!r}",
                )
            declared_names.add(name)
            declarations.append(ModuleDeclaration(name, location))
        elif item.data == "use_declaration":
            path_tree = item.children[0]
            if not isinstance(path_tree, Tree):
                raise RuntimeError("module parser produced an invalid use path")
            tokens = [
                child for child in path_tree.children if isinstance(child, Token)
            ]
            uses.append(
                UseDeclaration(
                    parts=tuple(str(token) for token in tokens),
                    locations=_token_locations(tokens),
                    location=location,
                )
            )

    return ParsedModule(tuple(declarations), tuple(uses))
