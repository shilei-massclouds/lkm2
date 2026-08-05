"""Persistent, content-addressed Model IR cache used only by the build entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Sequence

from model_ir import SCHEMA_VERSION, ModelIRValidationError, dump_model_ir, load_model_ir

from .compiler import compile_spec_with_inputs
from .diagnostics import CompilationError


CACHE_FORMAT_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _group_fingerprint(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        contents = path.read_bytes()
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def tool_fingerprints() -> dict[str, str]:
    """Return deterministic fingerprints for compiler, IR, and grammar code."""

    source_root = Path(__file__).resolve().parents[1]
    modelc_root = source_root / "modelc"
    model_ir_root = source_root / "model_ir"
    return {
        "grammar": _group_fingerprint(list(modelc_root.glob("*.lark")), source_root),
        "model_ir": _group_fingerprint(list(model_ir_root.glob("*.py")), source_root),
        "modelc": _group_fingerprint(list(modelc_root.glob("*.py")), source_root),
    }


def _relative(path: Path, repository: Path) -> str:
    try:
        return path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _input_records(paths: tuple[Path, ...], repository: Path) -> list[dict[str, str]]:
    records = [
        {"path": _relative(path, repository), "sha256": _sha256(path)}
        for path in paths
    ]
    return sorted(records, key=lambda item: item["path"])


def _resolve_record(path: str, repository: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repository / candidate


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if type(value) is not dict or set(value) != {
        "cache_format",
        "entry",
        "fingerprints",
        "inputs",
        "ir_schema_version",
    }:
        return None
    return value


def _manifest_matches(
    manifest: dict[str, Any], entry: Path, repository: Path
) -> bool:
    if manifest.get("cache_format") != CACHE_FORMAT_VERSION:
        return False
    if manifest.get("entry") != _relative(entry, repository):
        return False
    if manifest.get("ir_schema_version") != SCHEMA_VERSION:
        return False
    if manifest.get("fingerprints") != tool_fingerprints():
        return False
    inputs = manifest.get("inputs")
    if type(inputs) is not list or not inputs:
        return False
    seen: set[str] = set()
    for item in inputs:
        if type(item) is not dict or set(item) != {"path", "sha256"}:
            return False
        input_path = item["path"]
        digest = item["sha256"]
        if type(input_path) is not str or type(digest) is not str or input_path in seen:
            return False
        seen.add(input_path)
        try:
            if _sha256(_resolve_record(input_path, repository)) != digest:
                return False
        except OSError:
            return False
    return True


def _cache_ir_is_valid(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as stream:
            load_model_ir(stream)
    except (OSError, UnicodeError, ModelIRValidationError):
        return False
    return True


def _atomic_write(path: Path, writer) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def build_model_cache(entry: Path, cache_directory: Path, repository: Path) -> bool:
    """Build or reuse the cache; return True exactly when an existing cache was reused."""

    entry = entry.resolve()
    repository = repository.resolve()
    cache_directory.mkdir(parents=True, exist_ok=True)
    ir_path = cache_directory / "model.ir.json"
    manifest_path = cache_directory / "manifest.json"
    manifest = _load_manifest(manifest_path)
    if manifest is not None and _manifest_matches(manifest, entry, repository) and _cache_ir_is_valid(ir_path):
        return True

    model, inputs = compile_spec_with_inputs(entry)
    new_manifest = {
        "cache_format": CACHE_FORMAT_VERSION,
        "entry": _relative(entry, repository),
        "fingerprints": tool_fingerprints(),
        "inputs": _input_records(inputs, repository),
        "ir_schema_version": SCHEMA_VERSION,
    }
    _atomic_write(ir_path, lambda stream: dump_model_ir(model, stream))
    _atomic_write(
        manifest_path,
        lambda stream: (
            json.dump(new_manifest, stream, ensure_ascii=False, indent=2, sort_keys=True),
            stream.write("\n"),
        ),
    )
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="modelc-cache")
    parser.add_argument("entry", type=Path, metavar="ENTRY")
    parser.add_argument("cache_directory", type=Path, metavar="CACHE_DIRECTORY")
    parser.add_argument("--repository", type=Path, default=Path.cwd(), metavar="ROOT")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build_model_cache(args.entry, args.cache_directory, args.repository)
    except CompilationError as exc:
        print(exc.diagnostic.format(), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ModelIRValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
