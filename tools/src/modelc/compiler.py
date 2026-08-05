"""Compilation pipeline from a crate-root specification to Model IR."""

from __future__ import annotations

from pathlib import Path

from model_ir import SCHEMA_VERSION, ModelEntry, ModelIR, ModelModule

from .ast import ModelSpec
from .diagnostics import error
from .module_loader import load_module_graph
from .parser import parse_spec


def _lower_ast(
    document: ModelSpec, module_names: tuple[tuple[str, ...], ...]
) -> ModelIR:
    """Lower a validated entry AST and module graph into Model IR."""

    return ModelIR(
        schema_version=SCHEMA_VERSION,
        entry=ModelEntry(
            origin=document.origin.name.parts,
            spec=document.spec.name.parts,
        ),
        modules=tuple(ModelModule(name=name) for name in module_names),
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
    document = parse_spec(source, source_path)
    module_names = load_module_graph(source_path, document)
    origin_module = document.origin.name.parts[:-1]
    if origin_module not in set(module_names):
        span = document.origin.name.span
        rendered = ".".join(origin_module) or "<crate>"
        raise error(
            source_path,
            span.start_line,
            span.start_column,
            f"origin module {rendered!r} is not declared",
        )
    return _lower_ast(document, module_names)
