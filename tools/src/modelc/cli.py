"""Command-line interface for the model compiler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from model_ir import dump_model_ir

from .compiler import compile_spec
from .diagnostics import CompilationError, Diagnostic


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modelc", description="Compile an LKM model entry specification"
    )
    parser.add_argument("input", type=Path, metavar="INPUT")
    parser.add_argument("-o", "--output", type=Path, metavar="OUTPUT")
    return parser


def _report(diagnostic: Diagnostic, stream: TextIO) -> None:
    print(diagnostic.format(), file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)

    try:
        model = compile_spec(args.input)
    except CompilationError as exc:
        _report(exc.diagnostic, sys.stderr)
        return 1

    if args.output is None:
        try:
            dump_model_ir(model, sys.stdout)
        except OSError as exc:
            _report(
                Diagnostic("<stdout>", 1, 1, exc.strerror or str(exc)), sys.stderr
            )
            return 1
        return 0

    try:
        with args.output.open("w", encoding="utf-8", newline="\n") as stream:
            dump_model_ir(model, stream)
    except (OSError, UnicodeError) as exc:
        message = getattr(exc, "strerror", None) or str(exc)
        _report(Diagnostic(str(args.output), 1, 1, message), sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
