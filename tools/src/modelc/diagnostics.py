"""Structured diagnostics shared by the model compiler and its CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Diagnostic:
    path: str
    line: int
    column: int
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: error: {self.message}"


class CompilationError(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def error(
    path: str | Path, line: int, column: int, message: str
) -> CompilationError:
    return CompilationError(Diagnostic(str(path), line, column, message))
