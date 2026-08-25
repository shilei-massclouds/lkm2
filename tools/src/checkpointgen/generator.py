"""Extract and render implementation checkpoints without changing derive semantics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, TextIO

from model_ir import ModelExpression, ModelIR


class CheckpointGenerationError(ValueError):
    """The model and implementation checkpoint mapping do not agree."""


@dataclass(frozen=True, slots=True)
class MappingCheckpoint:
    canonical_id: str
    milestone: str
    parameters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SiblingRevision:
    commit: str
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CheckpointMapping:
    module: tuple[str, ...]
    root_object: str
    begins_after: str
    milestones: tuple[str, ...]
    checkpoints: tuple[MappingCheckpoint, ...]
    sibling_path: str
    sibling_branch: str
    sibling_patch_base: SiblingRevision
    sibling_integrated: SiblingRevision


@dataclass(frozen=True, slots=True)
class Checkpoint:
    canonical_id: str
    milestone: str
    parameters: tuple[str, ...]
    hash16: str
    symbol: str


def _require_dict(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        raise CheckpointGenerationError(f"{path} must be an object")
    return value


def _require_string(value: object, path: str) -> str:
    if type(value) is not str or not value:
        raise CheckpointGenerationError(f"{path} must be a non-empty string")
    return value


def _string_array(value: object, path: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise CheckpointGenerationError(f"{path} must be an array")
    values = tuple(_require_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if len(values) != len(set(values)):
        raise CheckpointGenerationError(f"{path} contains a duplicate")
    return values


def _load_sibling_revision(value: object, path: str) -> SiblingRevision:
    revision = _require_dict(value, path)
    if set(revision) != {"commit", "files"}:
        raise CheckpointGenerationError(f"{path} has missing or unknown fields")
    commit = _require_string(revision["commit"], f"{path}.commit")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise CheckpointGenerationError(
            f"{path}.commit must be a full lowercase Git object ID"
        )
    files_data = _require_dict(revision["files"], f"{path}.files")
    files: list[tuple[str, str]] = []
    for relative, digest in sorted(files_data.items()):
        if (
            type(relative) is not str
            or not relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
        ):
            raise CheckpointGenerationError(
                f"{path}.files keys must be relative paths"
            )
        digest_text = _require_string(digest, f"{path}.files[{relative!r}]")
        if re.fullmatch(r"[0-9a-f]{64}", digest_text) is None:
            raise CheckpointGenerationError(
                f"{path} file fingerprint must be SHA-256"
            )
        files.append((relative, digest_text))
    if not files:
        raise CheckpointGenerationError(f"{path}.files must not be empty")
    return SiblingRevision(commit, tuple(files))


def load_mapping(stream: TextIO) -> CheckpointMapping:
    try:
        raw = json.load(stream)
    except json.JSONDecodeError as exc:
        raise CheckpointGenerationError(
            f"invalid mapping JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    data = _require_dict(raw, "mapping")
    expected = {"schema_version", "scope", "sibling", "milestones", "checkpoints"}
    if set(data) != expected:
        raise CheckpointGenerationError(
            f"mapping fields must be exactly {', '.join(sorted(expected))}"
        )
    if data["schema_version"] != 2:
        raise CheckpointGenerationError("unsupported checkpoint mapping schema_version")

    scope = _require_dict(data["scope"], "scope")
    if set(scope) != {"module", "root_object", "begins_after"}:
        raise CheckpointGenerationError("scope has missing or unknown fields")
    module_text = _require_string(scope["module"], "scope.module")
    module = tuple(module_text.split("."))
    if any(not part.isidentifier() for part in module):
        raise CheckpointGenerationError("scope.module must be a dotted identifier")
    begins_after = _require_string(scope["begins_after"], "scope.begins_after")
    if begins_after != "arch_head_stack_established":
        raise CheckpointGenerationError(
            "scope.begins_after must exclude the pre-stack boot region"
        )

    sibling = _require_dict(data["sibling"], "sibling")
    if set(sibling) != {"path", "branch", "patch_base", "integrated"}:
        raise CheckpointGenerationError("sibling has missing or unknown fields")
    sibling_patch_base = _load_sibling_revision(
        sibling["patch_base"], "sibling.patch_base"
    )
    sibling_integrated = _load_sibling_revision(
        sibling["integrated"], "sibling.integrated"
    )
    if sibling_patch_base.commit == sibling_integrated.commit:
        raise CheckpointGenerationError(
            "sibling patch_base and integrated commits must differ"
        )

    milestones = _string_array(data["milestones"], "milestones")
    raw_checkpoints = data["checkpoints"]
    if type(raw_checkpoints) is not list:
        raise CheckpointGenerationError("checkpoints must be an array")
    checkpoints: list[MappingCheckpoint] = []
    for index, raw_checkpoint in enumerate(raw_checkpoints):
        item = _require_dict(raw_checkpoint, f"checkpoints[{index}]")
        if set(item) != {"id", "milestone", "parameters"}:
            raise CheckpointGenerationError(
                f"checkpoints[{index}] has missing or unknown fields"
            )
        canonical_id = _require_string(item["id"], f"checkpoints[{index}].id")
        milestone = _require_string(
            item["milestone"], f"checkpoints[{index}].milestone"
        )
        if milestone not in milestones:
            raise CheckpointGenerationError(
                f"checkpoint {canonical_id!r} names unknown milestone {milestone!r}"
            )
        parameters = _string_array(
            item["parameters"], f"checkpoints[{index}].parameters"
        )
        checkpoints.append(MappingCheckpoint(canonical_id, milestone, parameters))
    ids = tuple(item.canonical_id for item in checkpoints)
    if len(ids) != len(set(ids)):
        raise CheckpointGenerationError("checkpoint mapping contains a duplicate canonical ID")
    used_milestones = {item.milestone for item in checkpoints}
    missing_milestones = tuple(item for item in milestones if item not in used_milestones)
    if missing_milestones:
        raise CheckpointGenerationError(
            f"milestone {missing_milestones[0]!r} has no checkpoints"
        )
    return CheckpointMapping(
        module=module,
        root_object=_require_string(scope["root_object"], "scope.root_object"),
        begins_after=begins_after,
        milestones=milestones,
        checkpoints=tuple(checkpoints),
        sibling_path=_require_string(sibling["path"], "sibling.path"),
        sibling_branch=_require_string(sibling["branch"], "sibling.branch"),
        sibling_patch_base=sibling_patch_base,
        sibling_integrated=sibling_integrated,
    )


def _access_parts(expression: ModelExpression) -> tuple[str, ...] | None:
    parts: list[str] = []
    cursor = expression
    while cursor.kind in {"member", "path"}:
        if type(cursor.value) is not str:
            return None
        parts.append(cursor.value)
        cursor = cursor.children[0]
    if cursor.kind != "identifier" or type(cursor.value) is not str:
        return None
    parts.append(cursor.value)
    return tuple(reversed(parts))


def _expression_label(expression: ModelExpression) -> str:
    if expression.kind == "call" and len(expression.children) == 1:
        access = _access_parts(expression.children[0])
        if access is not None:
            return access[-1]
    if expression.kind == "binary" and expression.value == "==" and len(expression.children) == 2:
        left = _access_parts(expression.children[0])
        right = _access_parts(expression.children[1])
        if (
            left is not None
            and len(left) >= 2
            and left[-1] == "state"
            and right is not None
            and len(right) == 2
            and right[0] == "State"
        ):
            return f"{left[-2]}.{right[-1]}"
    raise CheckpointGenerationError(
        "checkpoint expression is not a supported predicate call or object-state equality"
    )


def extract_canonical_ids(model: ModelIR, mapping: CheckpointMapping) -> tuple[str, ...]:
    module = next((item for item in model.modules if item.name == mapping.module), None)
    if module is None:
        raise CheckpointGenerationError(
            f"model is missing checkpoint module {'.'.join(mapping.module)!r}"
        )
    if not any(item.name[-1] == mapping.root_object for item in module.objects):
        raise CheckpointGenerationError(
            f"checkpoint root object {mapping.root_object!r} does not exist"
        )

    result: list[str] = []
    for model_object in module.objects:
        object_name = model_object.name[-1]
        for state in model_object.states:
            state_name = state.name[-1]
            for block in state.invariants:
                for expression in block:
                    result.append(
                        f"{object_name}.{state_name}.Invariant.{_expression_label(expression)}"
                    )
            for transition in state.transitions:
                transition_name = transition.signal[-1]
                for block in transition.blocks:
                    if block.kind != "ensures":
                        continue
                    for expression in block.expressions:
                        result.append(
                            f"{object_name}.{transition_name}.Ensures.{_expression_label(expression)}"
                        )
    if len(result) != len(set(result)):
        duplicate = next(item for item in result if result.count(item) > 1)
        raise CheckpointGenerationError(
            f"canonical checkpoint ID collision at declaration {duplicate!r}"
        )
    return tuple(result)


def _default_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _symbol(canonical_id: str, hash16: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", canonical_id.lower()).strip("_")
    return f"lkm_checkpoint_{slug}_{hash16}"


def build_checkpoints(
    model: ModelIR,
    mapping: CheckpointMapping,
    *,
    hash_function: Callable[[str], str] = _default_hash,
) -> tuple[Checkpoint, ...]:
    extracted = set(extract_canonical_ids(model, mapping))
    configured = {item.canonical_id for item in mapping.checkpoints}
    missing = sorted(extracted - configured)
    if missing:
        raise CheckpointGenerationError(
            f"reachable checkpoint {missing[0]!r} has no implementation mapping"
        )
    extra = sorted(configured - extracted)
    if extra:
        raise CheckpointGenerationError(
            f"implementation mapping {extra[0]!r} has no reachable checkpoint"
        )

    result: list[Checkpoint] = []
    hashes: dict[str, str] = {}
    for item in mapping.checkpoints:
        hash16 = hash_function(item.canonical_id)
        if re.fullmatch(r"[0-9a-f]{16}", hash16) is None:
            raise CheckpointGenerationError("checkpoint hash must be 16 lowercase hex digits")
        previous = hashes.get(hash16)
        if previous is not None and previous != item.canonical_id:
            raise CheckpointGenerationError(
                f"checkpoint hash collision between {previous!r} and {item.canonical_id!r}"
            )
        hashes[hash16] = item.canonical_id
        result.append(
            Checkpoint(
                item.canonical_id,
                item.milestone,
                item.parameters,
                hash16,
                _symbol(item.canonical_id, hash16),
            )
        )
    return tuple(result)


def render_manifest(checkpoints: tuple[Checkpoint, ...]) -> str:
    data = {
        "schema_version": 1,
        "checkpoints": [
            {
                "id": item.canonical_id,
                "hash": item.hash16,
                "symbol": item.symbol,
                "milestone": item.milestone,
                "parameters": list(item.parameters),
            }
            for item in checkpoints
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


_OBSERVATION_STRUCTS = """\
#[derive(Clone, Copy)]
pub(crate) struct KernelMapObservation {
    pub(crate) kernel_va: u64,
    pub(crate) kernel_pa: u64,
    pub(crate) page_offset: u64,
    pub(crate) kernel_va_pa_offset: u64,
    pub(crate) mode: u64,
    pub(crate) levels: u64,
    pub(crate) top_shift: u64,
}

#[derive(Clone, Copy)]
pub(crate) struct TrampolineObservation {
    pub(crate) mode: u64,
    pub(crate) va: u64,
    pub(crate) pa: u64,
    pub(crate) size: u64,
    pub(crate) flags: u64,
    pub(crate) path_ok: u64,
}

#[derive(Clone, Copy)]
pub(crate) struct EarlyKernelObservation {
    pub(crate) mode: u64,
    pub(crate) va: u64,
    pub(crate) pa: u64,
    pub(crate) flags: u64,
    pub(crate) coverage_ok: u64,
}

#[derive(Clone, Copy)]
pub(crate) struct EarlyDtbObservation {
    pub(crate) mode: u64,
    pub(crate) dtb_pa: u64,
    pub(crate) dtb_va: u64,
    pub(crate) fix_va: u64,
    pub(crate) leaf0_pa: u64,
    pub(crate) leaf0_flags: u64,
    pub(crate) leaf1_pa: u64,
    pub(crate) leaf1_flags: u64,
    pub(crate) size: u64,
    pub(crate) coverage_ok: u64,
}
"""


def _observation_source(checkpoint: Checkpoint, parameter: str) -> str:
    tail = checkpoint.canonical_id.rsplit(".", 1)[-1]
    if tail in {
        "kernel_map_established",
        "kernel_map_records_supported_paging_mode",
    }:
        return f"kernel.{parameter}"
    if tail == "paging_mode_probe_completed_with_satp_bare":
        return "kernel.mode" if parameter == "mode" else "satp"
    if tail == "trampoline_first_segment_mapping_established":
        return f"trampoline.{parameter}"
    if tail == "early_kernel_image_mapping_established":
        return f"early_kernel.{parameter}"
    if tail == "early_dtb_four_mib_mapping_established":
        return f"early_dtb.{parameter}"
    if tail == "vm_setup_completes_with_satp_bare" and parameter == "satp":
        return "satp"
    raise CheckpointGenerationError(
        f"no observation binding for {checkpoint.canonical_id}.{parameter}"
    )


def _milestone_signature(milestone: str) -> str:
    signatures = {
        "kernel_map_ready": "kernel: KernelMapObservation, satp: u64",
        "preset_complete": "kernel: KernelMapObservation, satp: u64",
        "trampoline_ready": "trampoline: TrampolineObservation",
        "early_kernel_ready": "early_kernel: EarlyKernelObservation",
        "early_dtb_ready": "early_dtb: EarlyDtbObservation",
        "setup_complete": (
            "kernel: KernelMapObservation, trampoline: TrampolineObservation, "
            "early_kernel: EarlyKernelObservation, early_dtb: EarlyDtbObservation, satp: u64"
        ),
    }
    try:
        return signatures[milestone]
    except KeyError as exc:
        raise CheckpointGenerationError(
            f"Rust renderer does not implement milestone {milestone!r}"
        ) from exc


def _render_declarations(checkpoints: tuple[Checkpoint, ...]) -> str:
    lines = ["mod ffi {", "    unsafe extern \"C\" {"]
    for index, item in enumerate(checkpoints):
        parameters = ", ".join(f"arg{number}: u64" for number, _ in enumerate(item.parameters))
        lines.append(f"        #[link_name = \"{item.symbol}\"]")
        lines.append(f"        pub(super) fn checkpoint_{index}({parameters});")
    lines.extend(["    }", "}"])
    return "\n".join(lines)


def _render_empty_handlers(checkpoints: tuple[Checkpoint, ...]) -> str:
    lines = ["mod selected_handler {"]
    for index, item in enumerate(checkpoints):
        parameters = ", ".join(
            f"_arg{number}: u64" for number, _ in enumerate(item.parameters)
        )
        lines.append(f"    #[unsafe(export_name = \"{item.symbol}\")]")
        lines.append(f"    pub(super) extern \"C\" fn checkpoint_{index}({parameters}) {{}}")
    lines.append("}")
    return "\n".join(lines)


def _render_debugcon_handlers(checkpoints: tuple[Checkpoint, ...]) -> str:
    lines = [
        "mod selected_handler {",
        "    use core::arch::asm;",
        "",
        "    const SBI_EXT_DBCN: usize = 0x4442_434e;",
        "    const SBI_EXT_DBCN_CONSOLE_WRITE_BYTE: usize = 2;",
        "",
        "    #[inline(always)]",
        "    fn write_byte(byte: u8) {",
        "        // SAFETY: setup_vm runs in S-mode with the SBI calling convention active.",
        "        // DBCN write-byte takes only register arguments and returned errors are ignored.",
        "        unsafe {",
        "            asm!(",
        "                \"ecall\",",
        "                inlateout(\"a0\") byte as usize => _,",
        "                inlateout(\"a1\") 0_usize => _,",
        "                inlateout(\"a6\") SBI_EXT_DBCN_CONSOLE_WRITE_BYTE => _,",
        "                inlateout(\"a7\") SBI_EXT_DBCN => _,",
        "                options(nostack),",
        "            );",
        "        }",
        "    }",
        "",
        "    fn write_bytes(bytes: &[u8]) {",
        "        for byte in bytes {",
        "            write_byte(*byte);",
        "        }",
        "    }",
        "",
        "    fn write_hex(value: u64) {",
        "        let digits = *b\"0123456789abcdef\";",
        "        let mut shift = 60_u32;",
        "        loop {",
        "            write_byte(digits[((value >> shift) & 0xf) as usize]);",
        "            if shift == 0 {",
        "                break;",
        "            }",
        "            shift -= 4;",
        "        }",
        "    }",
    ]
    for index, item in enumerate(checkpoints):
        parameters = ", ".join(
            f"arg{number}: u64" for number, _ in enumerate(item.parameters)
        )
        lines.extend(
            [
                "",
                f"    #[unsafe(export_name = \"{item.symbol}\")]",
                f"    pub(super) extern \"C\" fn checkpoint_{index}({parameters}) {{",
                f"        write_bytes(b\"LKMCP1 id={item.canonical_id} hash={item.hash16}\");",
            ]
        )
        for number, parameter in enumerate(item.parameters):
            lines.append(f"        write_bytes(b\" {parameter}=0x\");")
            lines.append(f"        write_hex(arg{number});")
        lines.extend(["        write_byte(b'\\n');", "    }"])
    lines.append("}")
    return "\n".join(lines)


def _render_wrappers(
    checkpoints: tuple[Checkpoint, ...], milestones: tuple[str, ...]
) -> str:
    lines: list[str] = []
    for milestone in milestones:
        lines.extend(
            [
                "",
                "#[inline(always)]",
                f"pub(crate) fn {milestone}({_milestone_signature(milestone)}) {{",
            ]
        )
        for index, item in enumerate(checkpoints):
            if item.milestone != milestone:
                continue
            arguments = ", ".join(
                _observation_source(item, parameter) for parameter in item.parameters
            )
            lines.append(f"    // {item.canonical_id}")
            lines.append(f"    // SAFETY: generated declaration and selected handler share this exact C ABI.")
            lines.append(f"    unsafe {{ ffi::checkpoint_{index}({arguments}) }};")
        lines.append("}")
    return "\n".join(lines)


def render_rust(
    checkpoints: tuple[Checkpoint, ...],
    mapping: CheckpointMapping,
    handler: str,
) -> str:
    if handler not in {"empty", "debugcon"}:
        raise CheckpointGenerationError(
            f"unknown CHECKPOINT_HANDLER {handler!r}; expected empty or debugcon"
        )
    handlers = (
        _render_empty_handlers(checkpoints)
        if handler == "empty"
        else _render_debugcon_handlers(checkpoints)
    )
    return (
        "// @generated by checkpointgen; do not edit.\n"
        "// Model semantics remain owned by modelc/derive.\n\n"
        + _OBSERVATION_STRUCTS
        + "\n"
        + _render_declarations(checkpoints)
        + "\n\n"
        + handlers
        + "\n"
        + _render_wrappers(checkpoints, mapping.milestones)
        + "\n"
    )


def write_if_changed(path: Path, content: str) -> None:
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if current == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
