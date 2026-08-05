"""Public compilation pipeline from source text to the minimal Model IR."""

from __future__ import annotations

from pathlib import Path

from model_ir import ModelEntry, ModelIR

from .ast import ModelSpec
from .diagnostics import error
from .parser import parse_spec


def compile_ast(document: ModelSpec) -> ModelIR:
    """Lower an entry-file AST into the first-version Model IR."""

    return ModelIR(
        schema_version=1,
        entry=ModelEntry(
            origin=document.origin.name.parts,
            spec=document.spec.name.parts,
        ),
    )


def compile_spec(path: str | Path) -> ModelIR:
    """Read a UTF-8 entry specification and compile it to Model IR."""

    source_path = Path(path)
    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise error(source_path, 1, 1, "source is not valid UTF-8") from exc
    except OSError as exc:
        message = exc.strerror or str(exc)
        raise error(source_path, 1, 1, message) from exc
    return compile_ast(parse_spec(source, source_path))
