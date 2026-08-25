"""Explicitly apply a previously reviewed sibling patch without staging it."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Sequence


EXPECTED_PATHS = {
    "arch/riscv/mm/Makefile",
    "arch/riscv/mm/init.c",
    "arch/riscv/mm/lkm2_checkpoint_handler.c",
    "arch/riscv/mm/lkm2_checkpoints.inc",
}


class SiblingApplyError(RuntimeError):
    pass


def _git(sibling: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=sibling,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SiblingApplyError(f"git {' '.join(arguments)} failed: {detail}")
    return result


def apply_reviewed_patch(
    sibling: Path, patch: Path, expected_branch: str, expected_commit: str
) -> str:
    if _git(sibling, "rev-parse", "HEAD").stdout.strip() != expected_commit:
        raise SiblingApplyError("sibling HEAD is not the frozen checkpoint baseline")
    if _git(sibling, "branch", "--show-current").stdout.strip() != expected_branch:
        raise SiblingApplyError("sibling branch is not the frozen checkpoint branch")
    if not patch.is_file():
        raise SiblingApplyError(f"reviewed patch does not exist: {patch}")
    patch_argument = str(patch.resolve())
    status = _git(sibling, "status", "--short").stdout.strip()
    forward = _git(sibling, "apply", "--check", patch_argument, check=False)
    if forward.returncode == 0:
        if status:
            raise SiblingApplyError("sibling worktree must be clean before applying the patch")
        if _git(sibling, "diff", "--cached", "--quiet", check=False).returncode != 0:
            raise SiblingApplyError("sibling index must be clean before applying the patch")
        _git(sibling, "apply", patch_argument)
        changed = {
            line[3:]
            for line in _git(sibling, "status", "--short").stdout.splitlines()
            if len(line) >= 4
        }
        if changed != EXPECTED_PATHS:
            raise SiblingApplyError(
                f"applied patch changed unexpected paths: {sorted(changed)!r}"
            )
        if _git(sibling, "diff", "--cached", "--quiet", check=False).returncode != 0:
            raise SiblingApplyError("checkpoint patch unexpectedly staged changes")
        return "applied"

    reverse = _git(sibling, "apply", "--reverse", "--check", patch_argument, check=False)
    if reverse.returncode == 0:
        changed = {
            line[3:]
            for line in _git(sibling, "status", "--short").stdout.splitlines()
            if len(line) >= 4
        }
        if changed != EXPECTED_PATHS:
            raise SiblingApplyError(
                "patch appears applied, but the sibling has additional or missing changes"
            )
        return "already-applied"
    detail = forward.stderr.strip() or forward.stdout.strip()
    raise SiblingApplyError(f"reviewed patch is neither applicable nor already applied: {detail}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="checkpoint-sibling-apply")
    parser.add_argument("--sibling", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        outcome = apply_reviewed_patch(
            arguments.sibling.resolve(),
            arguments.patch.resolve(),
            arguments.branch,
            arguments.commit,
        )
    except (OSError, SiblingApplyError) as exc:
        print(f"checkpoint-sibling-apply: error: {exc}", file=sys.stderr)
        return 1
    print(f"checkpoint-sibling-apply: {outcome}; changes remain unstaged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
