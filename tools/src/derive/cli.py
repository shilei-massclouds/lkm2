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
from .runtime_signals import (
    UserRuntimeSignalValidationError,
    load_user_runtime_signals,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="derive", description="Execute a deterministic LKM derivation sequence"
    )
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--model", type=Path, metavar="MODEL")
    inputs.add_argument("--sequence", type=Path, metavar="SEQUENCE")
    parser.add_argument(
        "--user-runtime-signals",
        type=Path,
        metavar="PATH",
        help="replace the default user-runtime signal program",
    )
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
        user_runtime_signals = None
        if args.user_runtime_signals is not None:
            with args.user_runtime_signals.open("r", encoding="utf-8") as stream:
                user_runtime_signals = load_user_runtime_signals(stream)
    except CompilationError as exc:
        print(exc.diagnostic.format(), file=sys.stderr)
        return 1
    except (
        ModelIRValidationError,
        DerivationValidationError,
        UserRuntimeSignalValidationError,
        OSError,
        UnicodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = derive(
        model,
        sequence,
        user_runtime_signals=user_runtime_signals,
    )
    try:
        render_derivation_result(result, sys.stdout)
    except (OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
