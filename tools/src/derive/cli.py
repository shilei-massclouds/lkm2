"""Command-line interface for deterministic derivation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from model_ir import ModelIRValidationError, load_model_ir
from modelc import CompilationError, compile_spec

from .engine import derive
from .defaults import default_derivation_sequence
from .json_io import load_derivation_sequence
from .model import DerivationValidationError
from .renderer import render_derivation_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="derive", description="Execute a deterministic LKM derivation sequence"
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--model", type=Path, metavar="MODEL")
    inputs.add_argument("--sequence", type=Path, metavar="SEQUENCE")
    return parser


def _load_model(path: Path):
    if path.suffix == ".spec":
        return compile_spec(path)
    with path.open("r", encoding="utf-8") as stream:
        return load_model_ir(stream)


def main(
    argv: Sequence[str] | None = None,
    *,
    default_model: Path = Path("model/main.spec"),
) -> int:
    args = _parser().parse_args(argv)
    try:
        model = _load_model(args.model or default_model)
        if args.sequence is None:
            sequence = default_derivation_sequence(model)
        else:
            with args.sequence.open("r", encoding="utf-8") as stream:
                sequence = load_derivation_sequence(stream)
    except CompilationError as exc:
        print(exc.diagnostic.format(), file=sys.stderr)
        return 1
    except (ModelIRValidationError, DerivationValidationError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = derive(model, sequence)
    try:
        render_derivation_result(result, sys.stdout)
    except (OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
