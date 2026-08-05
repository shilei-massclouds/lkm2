"""Build the canonical default derivation sequence from cached Model IR."""

from __future__ import annotations

import argparse
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
from typing import Sequence

from model_ir import ModelIRValidationError, load_model_ir

from .defaults import default_derivation_sequence
from .json_io import dump_derivation_sequence


def build_default_sequence(model_path: Path, output_path: Path) -> bool:
    """Generate the default sequence; return True when the output was unchanged."""

    with model_path.open("r", encoding="utf-8") as stream:
        model = load_model_ir(stream)
    sequence = default_derivation_sequence(model)
    rendered = StringIO()
    dump_derivation_sequence(sequence, rendered)
    contents = rendered.getvalue()
    try:
        if output_path.read_text(encoding="utf-8") == contents:
            return True
    except (FileNotFoundError, UnicodeError):
        pass
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="derive-sequence-builder")
    parser.add_argument("model", type=Path, metavar="MODEL")
    parser.add_argument("output", type=Path, metavar="OUTPUT")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build_default_sequence(args.model, args.output)
    except (OSError, UnicodeError, ModelIRValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
