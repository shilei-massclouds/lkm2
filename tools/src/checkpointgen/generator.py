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
    commit: str | None
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CheckpointMapping:
    module: tuple[str, ...]
    root_object: str
    begins_after: str
    expression_blocks: tuple[str, ...]
    milestones: tuple[str, ...]
    checkpoints: tuple[MappingCheckpoint, ...]
    sibling_path: str
    sibling_branch: str
    sibling_patch_base: SiblingRevision
    sibling_integrated: SiblingRevision
    # Content observations are deliberately outside Model IR.  They still use
    # the same deterministic renderer/ABI, but their IDs and parameter schema
    # are fixed by the mapping itself.
    implementation_only: bool = False


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


def _load_sibling_revision(
    value: object, path: str, *, allow_unintegrated: bool = False
) -> SiblingRevision:
    revision = _require_dict(value, path)
    if set(revision) != {"commit", "files"}:
        raise CheckpointGenerationError(f"{path} has missing or unknown fields")
    raw_commit = revision["commit"]
    commit = (
        None
        if allow_unintegrated and raw_commit is None
        else _require_string(raw_commit, f"{path}.commit")
    )
    if commit is not None and re.fullmatch(r"[0-9a-f]{40}", commit) is None:
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
    raw_implementation_only = data.get("implementation_only", False)
    if type(raw_implementation_only) is not bool:
        raise CheckpointGenerationError("implementation_only must be a boolean")
    implementation_only = raw_implementation_only
    if set(data) not in (expected, expected | {"implementation_only"}):
        raise CheckpointGenerationError(
            f"mapping fields must be exactly {', '.join(sorted(expected))}"
        )
    if data["schema_version"] != 2:
        raise CheckpointGenerationError("unsupported checkpoint mapping schema_version")

    scope = _require_dict(data["scope"], "scope")
    allowed_scope = (
        {"module", "root_object", "begins_after"},
        {"module", "root_object", "begins_after", "expression_blocks"},
        {"module", "root_object", "begins_after", "implementation_only"},
        {"module", "root_object", "begins_after", "expression_blocks", "implementation_only"},
    )
    if set(scope) not in allowed_scope:
        raise CheckpointGenerationError("scope has missing or unknown fields")
    if "implementation_only" in scope:
        if type(scope["implementation_only"]) is not bool:
            raise CheckpointGenerationError("scope.implementation_only must be a boolean")
        if scope["implementation_only"]:
            implementation_only = True
    module_text = _require_string(scope["module"], "scope.module")
    module = tuple(module_text.split("."))
    if any(not part.isidentifier() for part in module):
        raise CheckpointGenerationError("scope.module must be a dotted identifier")
    begins_after = _require_string(scope["begins_after"], "scope.begins_after")
    if begins_after != "arch_head_stack_established":
        raise CheckpointGenerationError(
            "scope.begins_after must exclude the pre-stack boot region"
        )
    expression_blocks = (
        _string_array(scope["expression_blocks"], "scope.expression_blocks")
        if "expression_blocks" in scope
        else ("ensures", "invariant")
    )
    supported_blocks = {"ensures", "establishes", "invariant"}
    if set(expression_blocks) - supported_blocks or "invariant" not in expression_blocks:
        raise CheckpointGenerationError(
            "scope.expression_blocks must contain invariant and only supported block kinds"
        )

    sibling = _require_dict(data["sibling"], "sibling")
    if set(sibling) != {"path", "branch", "patch_base", "integrated"}:
        raise CheckpointGenerationError("sibling has missing or unknown fields")
    sibling_patch_base = _load_sibling_revision(
        sibling["patch_base"], "sibling.patch_base"
    )
    sibling_integrated = _load_sibling_revision(
        sibling["integrated"], "sibling.integrated", allow_unintegrated=True
    )
    if (
        sibling_integrated.commit is not None
        and sibling_patch_base.commit == sibling_integrated.commit
    ):
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
    if implementation_only:
        expected_content = (
            ("SwapperPageTable.Content.fixmap", ("valid", "count", "digest_lo", "digest_hi")),
            ("SwapperPageTable.Content.linear", ("valid", "count", "digest_lo", "digest_hi")),
            ("SwapperPageTable.Content.kernel_walk_valid", ("valid",)),
        )
        actual_content = tuple((item.canonical_id, item.parameters) for item in checkpoints)
        if actual_content != expected_content:
            raise CheckpointGenerationError(
                "implementation-only swapper content mapping must contain the fixed IDs and parameter order"
            )
        if milestones != ("swapper_content",):
            raise CheckpointGenerationError(
                "implementation-only swapper content mapping must use the swapper_content milestone"
            )
    return CheckpointMapping(
        module=module,
        root_object=_require_string(scope["root_object"], "scope.root_object"),
        begins_after=begins_after,
        expression_blocks=expression_blocks,
        milestones=milestones,
        checkpoints=tuple(checkpoints),
        sibling_path=_require_string(sibling["path"], "sibling.path"),
        sibling_branch=_require_string(sibling["branch"], "sibling.branch"),
        sibling_patch_base=sibling_patch_base,
        sibling_integrated=sibling_integrated,
        implementation_only=implementation_only,
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
    if expression.kind == "call" and expression.children:
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

    # Keep the original Vm checkpoint scope frozen while the final swapper
    # table is emitted through its own root mapping.
    excluded = {"SwapperPageTable"} if mapping.root_object == "Vm" else set()
    result: list[str] = []
    for model_object in module.objects:
        object_name = model_object.name[-1]
        if object_name in excluded:
            continue
        if mapping.root_object == "SwapperPageTable" and object_name != mapping.root_object:
            continue
        for state in model_object.states:
            state_name = state.name[-1]
            if "invariant" in mapping.expression_blocks:
                for block in state.invariants:
                    for expression in block:
                        result.append(
                            f"{object_name}.{state_name}.Invariant.{_expression_label(expression)}"
                        )
            for transition in state.transitions:
                transition_name = transition.signal[-1]
                for block in transition.blocks:
                    if block.kind not in mapping.expression_blocks or block.kind == "invariant":
                        continue
                    block_label = block.kind.title()
                    for expression in block.expressions:
                        result.append(
                            f"{object_name}.{transition_name}.{block_label}.{_expression_label(expression)}"
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
    model: ModelIR | None,
    mapping: CheckpointMapping,
    *,
    hash_function: Callable[[str], str] = _default_hash,
) -> tuple[Checkpoint, ...]:
    if mapping.implementation_only:
        # There is intentionally no model expression to extract for this
        # suite.  Keep the mapping as the single source of its fixed ABI.
        extracted = {item.canonical_id for item in mapping.checkpoints}
    else:
        if model is None:
            raise CheckpointGenerationError("model IR is required for model-derived mappings")
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

#[derive(Clone, Copy)]
pub(crate) struct SwapperObservation {
    pub(crate) mode: u64,
    pub(crate) fixmap_va: u64,
    pub(crate) linear_va: u64,
    pub(crate) linear_pa: u64,
    pub(crate) linear_flags: u64,
    pub(crate) kernel_va: u64,
    pub(crate) kernel_pa: u64,
    pub(crate) kernel_flags: u64,
    pub(crate) fixmap_cleared: u64,
    pub(crate) satp_switched: u64,
    pub(crate) tlb_flush_completed: u64,
    pub(crate) late_mode_selected: u64,
}

#[derive(Clone, Copy)]
pub(crate) struct SwapperContentObservation {
    pub(crate) fixmap_valid: u64,
    pub(crate) fixmap_count: u64,
    pub(crate) fixmap_digest_lo: u64,
    pub(crate) fixmap_digest_hi: u64,
    pub(crate) linear_valid: u64,
    pub(crate) linear_count: u64,
    pub(crate) linear_digest_lo: u64,
    pub(crate) linear_digest_hi: u64,
    pub(crate) kernel_walk_valid: u64,
}

#[derive(Clone, Copy)]
pub(crate) struct MemBlockRangeObservation {
    pub(crate) count: u64,
    pub(crate) digest: u64,
}
"""


def _observation_source(checkpoint: Checkpoint, parameter: str) -> str:
    tail = checkpoint.canonical_id.rsplit(".", 1)[-1]
    if checkpoint.canonical_id.startswith("SwapperPageTable.Content."):
        content_class = checkpoint.canonical_id.rsplit(".", 1)[-1]
        prefix = "fixmap" if content_class == "fixmap" else "linear"
        if content_class == "kernel_walk_valid":
            return "content.kernel_walk_valid"
        return f"content.{prefix}_{parameter}"
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
    if tail.startswith("swapper_"):
        return f"swapper.{parameter}"
    if parameter == "memory_count":
        return "memory.count"
    if parameter == "memory_digest":
        return "memory.digest"
    if parameter == "reserved_count":
        return "reserved.count"
    if parameter == "reserved_digest":
        return "reserved.digest"
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
        "swapper_online": "swapper: SwapperObservation",
        "swapper_content": "content: SwapperContentObservation",
        "memblock_ready": "memory: MemBlockRangeObservation",
        "memblock_memory_online": "memory: MemBlockRangeObservation",
        "memblock_reserved_online": "reserved: MemBlockRangeObservation",
        "memblock_online": (
            "memory: MemBlockRangeObservation, reserved: MemBlockRangeObservation"
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


def _render_empty_handlers(
    checkpoints: tuple[Checkpoint, ...], *, include_ranges: bool, include_content: bool
) -> str:
    lines = ["mod diagnostics {"]
    for index, item in enumerate(checkpoints):
        parameters = ", ".join(
            f"_arg{number}: u64" for number, _ in enumerate(item.parameters)
        )
        arguments = ", ".join(f"_arg{number}" for number, _ in enumerate(item.parameters))
        argument_slice = f"&[{arguments}]" if arguments else "&[]"
        lines.extend(
            [
                f"    #[unsafe(export_name = \"{item.symbol}\")]",
                f"    pub(super) extern \"C\" fn checkpoint_{index}({parameters}) {{",
                f"        crate::checkpoint::handlers::empty::checkpoint(b\"{item.canonical_id}\", b\"{item.hash16}\", {argument_slice});",
                "    }",
            ]
        )
    if include_ranges:
        lines.extend(
            [
                "",
                "    #[inline(always)]",
                "    pub(super) fn range(kind: &[u8], index: u64, base: u64, end: u64) {",
                "        crate::checkpoint::handlers::empty::range(kind, index, base, end);",
                "    }",
            ]
        )
    if include_content:
        lines.extend(
            [
                "",
                "    #[inline(always)]",
                "    pub(super) fn content_chunk(class: u64, chunk: u64, count: u64, lo: u64, hi: u64) {",
                "        crate::checkpoint::handlers::empty::content_chunk(class, chunk, count, lo, hi);",
                "    }",
                "    #[inline(always)]",
                "    pub(super) fn content_item(class: u64, index: u64, va: u64, pa: u64, flags: u64) {",
                "        crate::checkpoint::handlers::empty::content_item(class, index, va, pa, flags);",
                "    }",
            ]
        )
    lines.append("}")
    return "\n".join(lines)


def _render_debugcon_handlers(
    checkpoints: tuple[Checkpoint, ...], *, include_ranges: bool, include_content: bool
) -> str:
    lines = ["mod diagnostics {"]
    for index, item in enumerate(checkpoints):
        parameters = ", ".join(
            f"arg{number}: u64" for number, _ in enumerate(item.parameters)
        )
        arguments = ", ".join(
            f"(b\"{parameter}\", arg{number})"
            for number, parameter in enumerate(item.parameters)
        )
        argument_slice = f"&[{arguments}]" if arguments else "&[]"
        lines.extend(
            [
                "",
                f"    #[unsafe(export_name = \"{item.symbol}\")]",
                f"    pub(super) extern \"C\" fn checkpoint_{index}({parameters}) {{",
                f"        crate::checkpoint::handlers::debugcon::checkpoint(b\"{item.canonical_id}\", b\"{item.hash16}\", {argument_slice});",
                "    }",
            ]
        )
    if include_ranges:
        lines.extend(
            [
                "",
                "    pub(super) fn range(kind: &[u8], index: u64, base: u64, end: u64) {",
                "        // write_bytes(b\"LKMRNG1 kind=\") is implemented by the selected handler.",
                "        crate::checkpoint::handlers::debugcon::range(kind, index, base, end);",
                "    }",
            ]
        )
    if include_content:
        lines.extend(
            [
                "",
                "    pub(super) fn content_chunk(class: u64, chunk: u64, count: u64, lo: u64, hi: u64) {",
                "        crate::checkpoint::handlers::debugcon::content_chunk(class, chunk, count, lo, hi);",
                "    }",
                "",
                "    pub(super) fn content_item(class: u64, index: u64, va: u64, pa: u64, flags: u64) {",
                "        crate::checkpoint::handlers::debugcon::content_item(class, index, va, pa, flags);",
                "    }",
            ]
        )
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


def _render_range_wrappers(mapping: CheckpointMapping) -> str:
    if mapping.root_object != "MemBlock":
        return ""
    return """

#[inline(always)]
pub(crate) fn memory_range(index: u64, base: u64, end: u64) {
    diagnostics::range(b"memory", index, base, end);
}

#[inline(always)]
pub(crate) fn reserved_range(index: u64, base: u64, end: u64) {
    diagnostics::range(b"reserved", index, base, end);
}
""".rstrip()


def _render_content_wrappers(mapping: CheckpointMapping, *, enabled: bool) -> str:
    if not mapping.implementation_only:
        return ""
    enabled_literal = "true" if enabled else "false"
    return f"""

pub(crate) const ENABLED: bool = {enabled_literal};

#[inline(always)]
pub(crate) fn content_chunk(class: u64, chunk: u64, count: u64, lo: u64, hi: u64) {{
    diagnostics::content_chunk(class, chunk, count, lo, hi);
}}

#[inline(always)]
pub(crate) fn content_item(class: u64, index: u64, va: u64, pa: u64, flags: u64) {{
    diagnostics::content_item(class, index, va, pa, flags);
}}
""".rstrip()


def render_rust(
    checkpoints: tuple[Checkpoint, ...],
    mapping: CheckpointMapping,
    handler: str,
) -> str:
    if handler not in {"empty", "debugcon"}:
        raise CheckpointGenerationError(
            f"unknown CHECKPOINT_HANDLER {handler!r}; expected empty or debugcon"
        )
    include_ranges = mapping.root_object == "MemBlock"
    include_content = mapping.implementation_only
    handlers = (
        _render_empty_handlers(checkpoints, include_ranges=include_ranges, include_content=include_content)
        if handler == "empty"
        else _render_debugcon_handlers(checkpoints, include_ranges=include_ranges, include_content=include_content)
    )
    return (
        "// @generated by checkpointgen; do not edit.\n"
        "// Model semantics remain owned by modelc/derive.\n\n"
        "#![allow(dead_code)]\n\n"
        + _OBSERVATION_STRUCTS
        + "\n"
        + _render_declarations(checkpoints)
        + "\n\n"
        + handlers
        + "\n"
        + _render_wrappers(checkpoints, mapping.milestones)
        + _render_range_wrappers(mapping)
        + _render_content_wrappers(mapping, enabled=handler == "debugcon")
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
