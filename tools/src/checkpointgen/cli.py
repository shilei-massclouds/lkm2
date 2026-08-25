"""Command line entry point for Rust checkpoint generation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from model_ir import ModelIRValidationError, load_model_ir

from .generator import (
    CheckpointGenerationError,
    build_checkpoints,
    load_mapping,
    render_manifest,
    render_rust,
    write_if_changed,
)
from .sibling import generate_sibling_patch


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="checkpointgen", description="Generate implementation checkpoints from Model IR"
    )
    parser.add_argument("--model-ir", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--handler")
    parser.add_argument("--rust-output", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--sibling-patch-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        with arguments.model_ir.open(encoding="utf-8") as stream:
            model = load_model_ir(stream)
        with arguments.mapping.open(encoding="utf-8") as stream:
            mapping = load_mapping(stream)
        checkpoints = build_checkpoints(model, mapping)
        if arguments.sibling_patch_output is not None:
            if any(
                value is not None
                for value in (arguments.handler, arguments.rust_output, arguments.manifest_output)
            ):
                raise CheckpointGenerationError(
                    "sibling patch mode cannot be combined with Rust output options"
                )
            if arguments.repository is None:
                raise CheckpointGenerationError("sibling patch mode requires --repository")
            repository = arguments.repository.resolve()
            sibling = (repository / mapping.sibling_path).resolve()
            patch = generate_sibling_patch(sibling, mapping, checkpoints)
            write_if_changed(arguments.sibling_patch_output, patch)
        else:
            if (
                arguments.handler is None
                or arguments.rust_output is None
                or arguments.manifest_output is None
            ):
                raise CheckpointGenerationError(
                    "Rust mode requires --handler, --rust-output, and --manifest-output"
                )
            rust = render_rust(checkpoints, mapping, arguments.handler)
            manifest = render_manifest(checkpoints)
            write_if_changed(arguments.rust_output, rust)
            write_if_changed(arguments.manifest_output, manifest)
    except (OSError, UnicodeError, ModelIRValidationError, CheckpointGenerationError) as exc:
        print(f"checkpointgen: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
