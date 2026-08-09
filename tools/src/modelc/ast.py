"""Source-oriented syntax tree for the model specification entry file."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """A one-based, end-exclusive source range."""

    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True, slots=True)
class QualifiedName:
    parts: tuple[str, ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class SpecDeclaration:
    name: QualifiedName
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class OriginDeclaration:
    name: QualifiedName
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class ModelSpec:
    specs: tuple[SpecDeclaration, ...]
    origin: OriginDeclaration

    @property
    def spec(self) -> SpecDeclaration:
        """Return the primary root retained in Model IR's singular entry field."""

        return self.specs[0]
