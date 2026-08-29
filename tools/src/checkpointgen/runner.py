"""QEMU runner and strict parser for generated checkpoint records."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from typing import Sequence

from model_ir import load_model_ir

from .generator import CheckpointGenerationError, build_checkpoints, load_mapping
from .sibling import (
    validate_differential_sibling,
    validate_incremental_differential_sibling,
)


RECORD_PREFIX = "LKMCP1 "
FIELD = re.compile(r"([a-zA-Z0-9_.-]+)=([^ ]+)")
CPU_ARGUMENTS = {
    "sv57": (),
    "sv48": ("-cpu", "rv64,sv57=false"),
    "sv39": ("-cpu", "rv64,sv57=false,sv48=false"),
}
EXPECTED_MODE = {"sv57": 10, "sv48": 9, "sv39": 8}


class CheckpointRunError(RuntimeError):
    pass


def parse_record(line: str) -> tuple[str, str, tuple[tuple[str, int], ...]]:
    line = line.strip()
    if not line.startswith(RECORD_PREFIX):
        raise CheckpointRunError("not a checkpoint record")
    fields = FIELD.findall(line[len(RECORD_PREFIX) :])
    if len(fields) < 2 or fields[0][0] != "id" or fields[1][0] != "hash":
        raise CheckpointRunError(f"malformed checkpoint prefix: {line}")
    canonical_id = fields[0][1]
    hash16 = fields[1][1]
    if re.fullmatch(r"[0-9a-f]{16}", hash16) is None:
        raise CheckpointRunError(f"invalid checkpoint hash for {canonical_id}")
    parameters: list[tuple[str, int]] = []
    seen: set[str] = set()
    for key, value in fields[2:]:
        if key in seen:
            raise CheckpointRunError(f"duplicate parameter {key!r} in {canonical_id}")
        if re.fullmatch(r"0x[0-9a-f]{16}", value) is None:
            raise CheckpointRunError(f"invalid value for {canonical_id}.{key}")
        seen.add(key)
        parameters.append((key, int(value, 16)))
    return canonical_id, hash16, tuple(parameters)


def _read_manifest(path: Path) -> tuple[dict[str, object], ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointRunError(f"cannot read checkpoint manifest: {exc}") from exc
    checkpoints = data.get("checkpoints")
    if type(checkpoints) is not list:
        raise CheckpointRunError("checkpoint manifest has no checkpoints array")
    return tuple(checkpoints)


def _collect_qemu(
    command: list[str], expected_count: int, timeout: float, *, allow_extra: bool = False
) -> tuple[str, ...]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    pending = b""
    records: list[str] = []
    transcript: list[str] = []
    try:
        while len(records) < expected_count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CheckpointRunError(
                    f"QEMU timed out after {timeout:g}s with {len(records)}/{expected_count} records"
                )
            events = selector.select(remaining)
            if not events:
                continue
            chunk = process.stdout.read(4096)
            if not chunk:
                raise CheckpointRunError(
                    f"QEMU exited with {len(records)}/{expected_count} records"
                )
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                transcript.append(line)
                if line.startswith(RECORD_PREFIX):
                    if len(records) >= expected_count and allow_extra:
                        continue
                    records.append(line)
                    if len(records) > expected_count:
                        raise CheckpointRunError("QEMU emitted too many checkpoint records")
    except Exception as exc:
        if isinstance(exc, CheckpointRunError):
            detail = "\n".join(transcript[-20:])
            if detail:
                exc.args = (f"{exc}\nlast QEMU output:\n{detail}",)
        raise
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    return tuple(records)


def validate_records(
    lines: tuple[str, ...], manifest: tuple[dict[str, object], ...], mode_name: str
) -> tuple[tuple[str, str, tuple[tuple[str, int], ...]], ...]:
    if len(lines) != len(manifest):
        raise CheckpointRunError(
            f"record count {len(lines)} does not match manifest count {len(manifest)}"
        )
    parsed = tuple(parse_record(line) for line in lines)
    ids = tuple(item[0] for item in parsed)
    if len(ids) != len(set(ids)):
        raise CheckpointRunError("checkpoint output contains a duplicate canonical ID")
    expected_mode = EXPECTED_MODE[mode_name]
    for index, (record, expected) in enumerate(zip(parsed, manifest, strict=True)):
        canonical_id, hash16, parameters = record
        if canonical_id != expected.get("id"):
            raise CheckpointRunError(
                f"record {index + 1} ID {canonical_id!r} != {expected.get('id')!r}"
            )
        if hash16 != expected.get("hash"):
            raise CheckpointRunError(f"hash mismatch for {canonical_id}")
        keys = tuple(key for key, _ in parameters)
        expected_keys = tuple(expected.get("parameters", ()))
        if keys != expected_keys:
            raise CheckpointRunError(
                f"parameter order for {canonical_id} is {keys!r}, expected {expected_keys!r}"
            )
        values = OrderedDict(parameters)
        if "mode" in values and values["mode"] != expected_mode:
            raise CheckpointRunError(
                f"{canonical_id} observed mode {values['mode']}, expected {expected_mode}"
            )
        if "satp" in values and values["satp"] != 0:
            raise CheckpointRunError(f"{canonical_id} did not observe SATP Bare")
        for flag in ("path_ok", "coverage_ok"):
            if flag in values and values[flag] != 1:
                raise CheckpointRunError(f"{canonical_id} observed {flag}=0")
    return parsed


def run_self(repository: Path, mode_name: str, timeout: float) -> None:
    implementation = repository / "impl"
    build = subprocess.run(
        ["make", "--no-print-directory", "CHECKPOINT_HANDLER=debugcon", "build"],
        cwd=implementation,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if build.returncode != 0:
        raise CheckpointRunError(f"lkm2 debugcon build failed:\n{build.stdout}")
    manifest = _read_manifest(implementation / "build" / "checkpoints.manifest.json")
    swapper_manifest = _read_manifest(
        implementation / "build" / "swapper_checkpoints.manifest.json"
    )
    combined_manifest = (*manifest, *swapper_manifest)
    command = [
        "qemu-system-riscv64",
        "-machine",
        "virt",
        "-bios",
        "default",
        "-m",
        "128M",
        "-smp",
        "1",
        "-nographic",
        "-append",
        "earlycon=sbi",
        *CPU_ARGUMENTS[mode_name],
        "-kernel",
        str(implementation / "build" / "lkm2.bin"),
    ]
    records = _collect_qemu(command, len(combined_manifest), timeout)
    split = len(manifest)
    validate_records(records[:split], manifest, mode_name)
    validate_records(records[split:], swapper_manifest, mode_name)


def _verify_sibling_pc_relative(sibling: Path) -> None:
    object_paths = (
        sibling / "arch" / "riscv" / "mm" / "init.o",
        sibling / "arch" / "riscv" / "mm" / "lkm2_checkpoint_handler.o",
    )
    for path in object_paths:
        if not path.is_file():
            raise CheckpointRunError(f"sibling build did not produce {path}")
        result = subprocess.run(
            ["riscv64-linux-gnu-objdump", "-dr", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise CheckpointRunError(f"cannot inspect sibling object {path.name}")
        if re.search(r"\bR_RISCV_HI20\b", result.stdout):
            raise CheckpointRunError(
                f"{path.name} contains an absolute HI20 relocation in the MMU-off path"
            )
    handler = subprocess.run(
        ["riscv64-linux-gnu-objdump", "-dr", str(object_paths[1])],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    if "R_RISCV_PCREL_HI20" not in handler:
        raise CheckpointRunError("sibling handler strings are not addressed PC-relatively")
    init = subprocess.run(
        ["riscv64-linux-gnu-objdump", "-dr", str(object_paths[0])],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    if "lkm_checkpoint_" not in init or "R_RISCV_CALL" not in init:
        raise CheckpointRunError("sibling MMU-off checkpoint calls are not PC-relative calls")


def run_diff(
    repository: Path, sibling: Path, timeout: float, build_sibling: bool
) -> str:
    try:
        with (repository / "tools" / "checkpoints" / "vm.json").open(
            encoding="utf-8"
        ) as stream:
            mapping = load_mapping(stream)
        with (repository / "tools" / "checkpoints" / "swapper.json").open(
            encoding="utf-8"
        ) as stream:
            swapper_mapping = load_mapping(stream)
    except (OSError, UnicodeError, CheckpointGenerationError) as exc:
        raise CheckpointRunError(f"invalid sibling differential state: {exc}") from exc
    if build_sibling:
        jobs = "2"
        build = subprocess.run(
            [
                "make",
                f"-j{jobs}",
                "ARCH=riscv",
                "CROSS_COMPILE=riscv64-linux-gnu-",
                "Image",
            ],
            cwd=sibling,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if build.returncode != 0:
            raise CheckpointRunError(f"sibling Linux build failed:\n{build.stdout}")
    image = sibling / "arch" / "riscv" / "boot" / "Image"
    if not image.is_file():
        raise CheckpointRunError("sibling Linux Image is missing")
    _verify_sibling_pc_relative(sibling)

    implementation = repository / "impl"
    lkm_build = subprocess.run(
        ["make", "--no-print-directory", "CHECKPOINT_HANDLER=debugcon", "build"],
        cwd=implementation,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if lkm_build.returncode != 0:
        raise CheckpointRunError(f"lkm2 debugcon build failed:\n{lkm_build.stdout}")
    try:
        with (repository / "tools" / "build" / "modelc" / "model.ir.json").open(
            encoding="utf-8"
        ) as stream:
            model = load_model_ir(stream)
        swapper_checkpoints = build_checkpoints(model, swapper_mapping)
        sibling_state = validate_incremental_differential_sibling(
            sibling, swapper_mapping, swapper_checkpoints
        )
        if sibling_state is None:
            sibling_state = validate_differential_sibling(sibling, mapping)
    except (OSError, UnicodeError, CheckpointGenerationError) as exc:
        raise CheckpointRunError(f"invalid sibling differential state: {exc}") from exc
    swapper_enabled = sibling_state == "reviewed-swapper-patch"
    manifest = _read_manifest(implementation / "build" / "checkpoints.manifest.json")
    swapper_manifest = _read_manifest(
        implementation / "build" / "swapper_checkpoints.manifest.json"
    )
    expected_manifest = (*manifest, *swapper_manifest) if swapper_enabled else manifest
    common = [
        "qemu-system-riscv64",
        "-machine",
        "virt",
        "-bios",
        "default",
        "-m",
        "128M",
        "-smp",
        "1",
        "-nographic",
        "-append",
        "earlycon=sbi",
    ]
    lkm_lines = _collect_qemu(
        [*common, "-kernel", str(implementation / "build" / "lkm2.bin")],
        len(expected_manifest),
        timeout,
        allow_extra=not swapper_enabled,
    )
    linux_lines = _collect_qemu(
        [*common, "-kernel", str(image)], len(expected_manifest), timeout
    )
    if swapper_enabled:
        split = len(manifest)
        validate_records(lkm_lines[:split], manifest, "sv57")
        validate_records(lkm_lines[split:], swapper_manifest, "sv57")
        validate_records(linux_lines[:split], manifest, "sv57")
        validate_records(linux_lines[split:], swapper_manifest, "sv57")
    lkm_records = validate_records(lkm_lines, expected_manifest, "sv57")
    linux_records = validate_records(linux_lines, expected_manifest, "sv57")
    if lkm_records != linux_records:
        for index, (left, right) in enumerate(
            zip(lkm_records, linux_records, strict=True)
        ):
            if left != right:
                raise CheckpointRunError(
                    f"Sv57 differential mismatch at record {index + 1}:\n"
                    f"lkm2: {left!r}\nlinux: {right!r}"
                )
        raise CheckpointRunError("Sv57 differential output differs")
    return sibling_state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="checkpoint-runner")
    parser.add_argument("--repository", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self", dest="self_mode", choices=("sv57", "sv48", "sv39", "all"))
    group.add_argument("--diff-sv57", action="store_true")
    parser.add_argument("--sibling", type=Path)
    parser.add_argument("--build-sibling", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository.resolve()
        if arguments.diff_sv57:
            if arguments.sibling is None:
                raise CheckpointRunError("--diff-sv57 requires --sibling")
            sibling_state = run_diff(
                repository,
                arguments.sibling.resolve(),
                arguments.timeout,
                arguments.build_sibling,
            )
            print(
                "checkpoint-runner: strict lkm2/Linux Sv57 differential passed "
                f"(sibling state: {sibling_state})"
            )
        else:
            modes = (
                tuple(CPU_ARGUMENTS)
                if arguments.self_mode == "all"
                else (arguments.self_mode,)
            )
            for mode_name in modes:
                assert mode_name is not None
                run_self(repository, mode_name, arguments.timeout)
                print(f"checkpoint-runner: lkm2 {mode_name} passed")
    except (OSError, CheckpointRunError) as exc:
        print(f"checkpoint-runner: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
