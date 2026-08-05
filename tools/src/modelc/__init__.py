"""Compiler API for LKM model specification entry files."""

from .ast import (
    ModelSpec,
    OriginDeclaration,
    QualifiedName,
    SourceSpan,
    SpecDeclaration,
)
from .compiler import compile_spec
from .diagnostics import CompilationError, Diagnostic
from .parser import parse_spec

__all__ = [
    "CompilationError",
    "Diagnostic",
    "ModelSpec",
    "OriginDeclaration",
    "QualifiedName",
    "SourceSpan",
    "SpecDeclaration",
    "compile_spec",
    "parse_spec",
]
