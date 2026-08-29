"""Deterministic, non-mutating Linux sibling checkpoint patch generation."""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
import subprocess

from .generator import (
    Checkpoint,
    CheckpointGenerationError,
    CheckpointMapping,
    SiblingRevision,
    _observation_source,
)


EXPECTED_SIBLING_PATHS = {
    "arch/riscv/mm/Makefile",
    "arch/riscv/mm/init.c",
    "arch/riscv/mm/lkm2_checkpoint_handler.c",
    "arch/riscv/mm/lkm2_checkpoints.inc",
}


def _git(sibling: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=sibling,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CheckpointGenerationError(f"sibling git {' '.join(arguments)} failed: {detail}")
    # Preserve the two-character status prefix (especially its leading space
    # for unstaged-only edits); callers parse that prefix to reject staged
    # sibling changes.
    return result.stdout.rstrip()


def _git_bytes(sibling: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=sibling,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise CheckpointGenerationError(
            f"sibling git {' '.join(arguments)} failed: {detail}"
        )
    return result.stdout


def _fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_branch(sibling: Path, mapping: CheckpointMapping) -> None:
    branch = _git(sibling, "branch", "--show-current")
    if branch != mapping.sibling_branch:
        raise CheckpointGenerationError(
            f"sibling branch {branch!r} != frozen {mapping.sibling_branch!r}"
        )


def _validate_worktree_files(
    sibling: Path, revision: SiblingRevision, label: str
) -> None:
    for relative, expected in revision.files:
        path = sibling / relative
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise CheckpointGenerationError(
                f"cannot read sibling {label} anchor {relative}: {exc}"
            ) from exc
        if _fingerprint(content) != expected:
            raise CheckpointGenerationError(
                f"sibling {label} anchor fingerprint mismatch for {relative}"
            )


def _read_revision_file(
    sibling: Path, revision: SiblingRevision, relative: str
) -> str:
    expected_files = dict(revision.files)
    if relative not in expected_files:
        raise CheckpointGenerationError(
            f"sibling revision has no frozen anchor for {relative}"
        )
    content = _git_bytes(sibling, "show", f"{revision.commit}:{relative}")
    if _fingerprint(content) != expected_files[relative]:
        raise CheckpointGenerationError(
            f"sibling patch-base object fingerprint mismatch for {relative}"
        )
    try:
        return content.decode("utf-8")
    except UnicodeError as exc:
        raise CheckpointGenerationError(
            f"sibling patch-base anchor {relative} is not UTF-8"
        ) from exc


def validate_sibling(sibling: Path, mapping: CheckpointMapping) -> None:
    """Validate a clean checkout and the immutable historical patch source."""

    if not sibling.is_dir():
        raise CheckpointGenerationError(f"sibling path does not exist: {sibling}")
    _validate_branch(sibling, mapping)
    status = _git(sibling, "status", "--short")
    if status:
        raise CheckpointGenerationError("sibling worktree is not clean")
    commit = _git(sibling, "rev-parse", "HEAD")
    revisions = {
        mapping.sibling_patch_base.commit: (mapping.sibling_patch_base, "patch-base")
    }
    if mapping.sibling_integrated.commit is not None:
        revisions[mapping.sibling_integrated.commit] = (
            mapping.sibling_integrated,
            "integrated",
        )
    current = revisions.get(commit)
    if current is None:
        raise CheckpointGenerationError(
            "sibling HEAD is neither the frozen patch base nor integrated checkpoint commit"
        )
    _validate_worktree_files(sibling, *current)
    _git(sibling, "cat-file", "-e", f"{mapping.sibling_patch_base.commit}^{{commit}}")
    for relative, _expected in mapping.sibling_patch_base.files:
        _read_revision_file(sibling, mapping.sibling_patch_base, relative)


def validate_differential_sibling(
    sibling: Path, mapping: CheckpointMapping
) -> str:
    """Accept either the committed integration or the exact unstaged review state."""

    if not sibling.is_dir():
        raise CheckpointGenerationError(f"sibling path does not exist: {sibling}")
    _validate_branch(sibling, mapping)
    commit = _git(sibling, "rev-parse", "HEAD")
    status = _git(sibling, "status", "--short")
    if any(line and line[0] not in {" ", "?"} for line in status.splitlines()):
        raise CheckpointGenerationError("sibling checkpoint changes must remain unstaged")
    if (
        mapping.sibling_integrated.commit is not None
        and commit == mapping.sibling_integrated.commit
    ):
        if status:
            raise CheckpointGenerationError(
                "integrated sibling checkpoint worktree must be clean"
            )
        state = "integrated"
    elif commit == mapping.sibling_patch_base.commit:
        changed_paths = {
            line[3:] for line in status.splitlines() if len(line) >= 4
        }
        if changed_paths != EXPECTED_SIBLING_PATHS:
            raise CheckpointGenerationError(
                "sibling must contain exactly the reviewed, unstaged checkpoint patch"
            )
        state = "reviewed-patch"
    else:
        raise CheckpointGenerationError(
            "sibling HEAD is neither the frozen patch base nor integrated checkpoint commit"
        )
    _validate_worktree_files(sibling, mapping.sibling_integrated, "integrated")
    return state


def validate_incremental_differential_sibling(
    sibling: Path,
    mapping: CheckpointMapping,
    checkpoints: tuple[Checkpoint, ...],
) -> str | None:
    """Recognize the unstaged M1 patch layered on the VM integration.

    The M1 Linux patch intentionally has no new commit yet.  It is reviewed
    as three unstaged modifications on the existing VM-integrated commit.
    Return ``None`` for a clean integrated tree so callers can retain the
    frozen 28-record differential path.
    """

    if mapping.root_object != "SwapperPageTable":
        raise CheckpointGenerationError("incremental validation requires SwapperPageTable mapping")
    if not sibling.is_dir():
        raise CheckpointGenerationError(f"sibling path does not exist: {sibling}")
    _validate_branch(sibling, mapping)
    head = _git(sibling, "rev-parse", "HEAD")
    if (
        mapping.sibling_integrated.commit is not None
        and head == mapping.sibling_integrated.commit
    ):
        status_lines = _git(sibling, "status", "--short").splitlines()
        if status_lines:
            raise CheckpointGenerationError(
                "integrated sibling M1 checkpoint worktree must be clean"
            )
        _validate_worktree_files(sibling, mapping.sibling_integrated, "integrated")
        return "integrated-swapper"
    if head != mapping.sibling_patch_base.commit:
        return None
    status_lines = _git(sibling, "status", "--short").splitlines()
    if not status_lines:
        return None
    if any(line and line[0] not in {" ", "?"} for line in status_lines):
        raise CheckpointGenerationError("sibling checkpoint changes must remain unstaged")
    expected_paths = {
        "arch/riscv/mm/init.c",
        "arch/riscv/mm/lkm2_checkpoint_handler.c",
        "arch/riscv/mm/lkm2_checkpoints.inc",
    }
    changed_paths = {line[3:] for line in status_lines if len(line) >= 4}
    if changed_paths != expected_paths:
        raise CheckpointGenerationError(
            "sibling must contain exactly the reviewed, unstaged M1 checkpoint patch"
        )

    baseline = mapping.sibling_patch_base
    init_path = "arch/riscv/mm/init.c"
    include_path = "arch/riscv/mm/lkm2_checkpoints.inc"
    handler_path = "arch/riscv/mm/lkm2_checkpoint_handler.c"
    expected = {
        init_path: _modify_init_swapper_incremental(
            _read_revision_file(sibling, baseline, init_path)
        ),
        include_path: _swapper_include_append(
            _read_revision_file(sibling, baseline, include_path), checkpoints, mapping
        ),
        handler_path: _swapper_handler_append(
            _read_revision_file(sibling, baseline, handler_path), checkpoints
        ),
    }
    for relative, content in expected.items():
        try:
            actual = (sibling / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CheckpointGenerationError(
                f"cannot read sibling M1 patch anchor {relative}: {exc}"
            ) from exc
        if actual != content:
            raise CheckpointGenerationError(
                f"sibling M1 patch content differs from generated {relative}"
            )
    return "reviewed-swapper-patch"


def validate_memblock_differential_sibling(
    sibling: Path,
    mapping: CheckpointMapping,
    checkpoints: tuple[Checkpoint, ...],
) -> str | None:
    """Recognize the exact MemBlock patch layered on the M1 integration."""

    if mapping.root_object != "MemBlock":
        raise CheckpointGenerationError("MemBlock validation requires MemBlock mapping")
    if not sibling.is_dir():
        raise CheckpointGenerationError(f"sibling path does not exist: {sibling}")
    _validate_branch(sibling, mapping)
    head = _git(sibling, "rev-parse", "HEAD")
    if (
        mapping.sibling_integrated.commit is not None
        and head == mapping.sibling_integrated.commit
    ):
        status_lines = _git(sibling, "status", "--short").splitlines()
        if status_lines:
            raise CheckpointGenerationError(
                "integrated sibling MemBlock checkpoint worktree must be clean"
            )
        _validate_worktree_files(sibling, mapping.sibling_integrated, "integrated")
        return "integrated-memblock"
    if head != mapping.sibling_patch_base.commit:
        return None
    status_lines = _git(sibling, "status", "--short").splitlines()
    if not status_lines:
        return None
    if any(line and line[0] not in {" ", "?"} for line in status_lines):
        raise CheckpointGenerationError("sibling checkpoint changes must remain unstaged")
    expected_paths = {
        "arch/riscv/mm/init.c",
        "arch/riscv/mm/lkm2_checkpoint_handler.c",
        "arch/riscv/mm/lkm2_checkpoints.inc",
    }
    changed_paths = {line[3:] for line in status_lines if len(line) >= 4}
    if changed_paths != expected_paths:
        raise CheckpointGenerationError(
            "sibling must contain exactly the reviewed, unstaged MemBlock checkpoint patch"
        )

    baseline = mapping.sibling_patch_base
    init_path = "arch/riscv/mm/init.c"
    include_path = "arch/riscv/mm/lkm2_checkpoints.inc"
    handler_path = "arch/riscv/mm/lkm2_checkpoint_handler.c"
    expected = {
        init_path: _modify_init_memblock_incremental(
            _read_revision_file(sibling, baseline, init_path)
        ),
        include_path: _memblock_include_append(
            _read_revision_file(sibling, baseline, include_path), checkpoints, mapping
        ),
        handler_path: _memblock_handler_append(
            _read_revision_file(sibling, baseline, handler_path), checkpoints
        ),
    }
    for relative, content in expected.items():
        try:
            actual = (sibling / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CheckpointGenerationError(
                f"cannot read sibling MemBlock patch anchor {relative}: {exc}"
            ) from exc
        if actual != content:
            raise CheckpointGenerationError(
                f"sibling MemBlock patch content differs from generated {relative}"
            )
    return "reviewed-memblock-patch"


def _c_parameters(item: Checkpoint, *, names: bool = True) -> str:
    return ", ".join(
        f"uint64_t {'arg' + str(index) if names else ''}".rstrip()
        for index, _ in enumerate(item.parameters)
    ) or "void"


def _render_c_declarations(checkpoints: tuple[Checkpoint, ...]) -> str:
    return "\n".join(
        f"extern void {item.symbol}({_c_parameters(item)});" for item in checkpoints
    )


def _c_source(item: Checkpoint, parameter: str) -> str:
    source = _observation_source(item, parameter)
    return source.replace("kernel.", "kernel.").replace("trampoline.", "trampoline.")


def _render_milestone(
    milestone: str, checkpoints: tuple[Checkpoint, ...]
) -> str:
    observation_lines = {
        "kernel_map_ready": (
            "\tstruct lkm2_cp_kernel kernel = lkm2_cp_observe_kernel();\n"
            "\tuint64_t satp = csr_read(CSR_SATP);"
        ),
        "preset_complete": (
            "\tstruct lkm2_cp_kernel kernel = lkm2_cp_observe_kernel();\n"
            "\tuint64_t satp = csr_read(CSR_SATP);"
        ),
        "trampoline_ready": (
            "\tstruct lkm2_cp_trampoline trampoline = lkm2_cp_observe_trampoline();"
        ),
        "early_kernel_ready": (
            "\tstruct lkm2_cp_early_kernel early_kernel = lkm2_cp_observe_early_kernel();"
        ),
        "early_dtb_ready": (
            "\tstruct lkm2_cp_early_dtb early_dtb = lkm2_cp_observe_early_dtb();"
        ),
        "setup_complete": (
            "\tstruct lkm2_cp_kernel kernel = lkm2_cp_observe_kernel();\n"
            "\tstruct lkm2_cp_trampoline trampoline = lkm2_cp_observe_trampoline();\n"
            "\tstruct lkm2_cp_early_kernel early_kernel = lkm2_cp_observe_early_kernel();\n"
            "\tstruct lkm2_cp_early_dtb early_dtb = lkm2_cp_observe_early_dtb();\n"
            "\tuint64_t satp = csr_read(CSR_SATP);"
        ),
        "swapper_online": (
            "\tstruct lkm2_cp_swapper swapper = lkm2_cp_observe_swapper();"
        ),
        "swapper_content": (
            "\tstruct lkm2_cp_content_observation content = lkm2_cp_observe_content();"
        ),
        "memblock_ready": (
            "\tstruct lkm2_cp_memblock memory = lkm2_cp_observe_memblock(&memblock.memory);\n"
            "\tlkm2_cp_emit_ranges(&memory, true);"
        ),
        "memblock_memory_online": (
            "\tstruct lkm2_cp_memblock memory = lkm2_cp_observe_memblock(&memblock.memory);"
        ),
        "memblock_reserved_online": (
            "\tstruct lkm2_cp_memblock reserved = lkm2_cp_observe_reserved();\n"
            "\tlkm2_cp_emit_ranges(&reserved, false);"
        ),
        "memblock_online": (
            "\tstruct lkm2_cp_memblock memory = lkm2_cp_observe_memblock(&memblock.memory);\n"
            "\tstruct lkm2_cp_memblock reserved = lkm2_cp_observe_reserved();"
        ),
    }
    lines = [f"static void __init lkm2_checkpoint_{milestone}(void)", "{", observation_lines[milestone]]
    for item in checkpoints:
        if item.milestone != milestone:
            continue
        arguments = ", ".join(_c_source(item, parameter) for parameter in item.parameters)
        lines.append(f"\t{item.symbol}({arguments});")
    lines.append("}")
    return "\n".join(lines)


def _render_memblock_linux_include(
    checkpoints: tuple[Checkpoint, ...], mapping: CheckpointMapping
) -> str:
    milestones = "\n\n".join(
        _render_milestone(name, checkpoints) for name in mapping.milestones
    )
    return f"""/* @generated by lkm2 checkpointgen; MemBlock incremental fragment. */
#include <linux/types.h>

{_render_c_declarations(checkpoints)}
extern void lkm2_checkpoint_memory_range(uint64_t index, uint64_t base, uint64_t end);
extern void lkm2_checkpoint_reserved_range(uint64_t index, uint64_t base, uint64_t end);

#define LKM2_CP_MAX_MEMBLOCK_RANGES 128
#define LKM2_CP_RANGE_DIGEST_OFFSET 0xcbf29ce484222325ULL
#define LKM2_CP_RANGE_DIGEST_PRIME 0x100000001b3ULL

struct lkm2_cp_range {{
	uint64_t base, end;
}};

struct lkm2_cp_memblock {{
	uint64_t count, digest, stored;
	struct lkm2_cp_range ranges[LKM2_CP_MAX_MEMBLOCK_RANGES];
}};

static uint64_t __init lkm2_cp_digest_word(uint64_t digest, uint64_t value)
{{
	int shift;

	for (shift = 56; shift >= 0; shift -= 8) {{
		digest ^= (value >> shift) & 0xff;
		digest *= LKM2_CP_RANGE_DIGEST_PRIME;
	}}
	return digest;
}}

static struct lkm2_cp_memblock __init
lkm2_cp_observe_memblock(const struct memblock_type *type)
{{
	struct lkm2_cp_memblock result = {{
		.digest = LKM2_CP_RANGE_DIGEST_OFFSET,
	}};
	unsigned long i, j;

	if (type->cnt > LKM2_CP_MAX_MEMBLOCK_RANGES) {{
		result.count = ~0ULL;
		return result;
	}}
	for (i = 0; i < type->cnt; i++) {{
		uint64_t base = type->regions[i].base;
		uint64_t end = base + type->regions[i].size;

		if (!type->regions[i].size)
			continue;
		if (end < base) {{
			result.count = ~0ULL;
			result.stored = 0;
			return result;
		}}
		j = result.stored;
		while (j && result.ranges[j - 1].base > base) {{
			result.ranges[j] = result.ranges[j - 1];
			j--;
		}}
		result.ranges[j].base = base;
		result.ranges[j].end = end;
		result.stored++;
	}}
	for (i = 0, j = 0; i < result.stored; i++) {{
		if (j && result.ranges[i].base <= result.ranges[j - 1].end) {{
			if (result.ranges[i].end > result.ranges[j - 1].end)
				result.ranges[j - 1].end = result.ranges[i].end;
			continue;
		}}
		result.ranges[j++] = result.ranges[i];
	}}
	result.stored = j;
	result.count = j;
	for (i = 0; i < result.stored; i++) {{
		result.digest = lkm2_cp_digest_word(result.digest, result.ranges[i].base);
		result.digest = lkm2_cp_digest_word(result.digest, result.ranges[i].end);
	}}
	return result;
}}

static bool __init
lkm2_cp_append_projected(struct memblock_region *regions, unsigned long *count,
			 uint64_t base, uint64_t size, uint64_t excluded_base,
			 uint64_t excluded_end)
{{
	uint64_t end = base + size;

	if (!size)
		return true;
	if (end < base)
		return false;
	if (end <= excluded_base || base >= excluded_end) {{
		if (*count == LKM2_CP_MAX_MEMBLOCK_RANGES)
			return false;
		regions[*count].base = base;
		regions[(*count)++].size = size;
		return true;
	}}
	if (base < excluded_base) {{
		if (*count == LKM2_CP_MAX_MEMBLOCK_RANGES)
			return false;
		regions[*count].base = base;
		regions[(*count)++].size = excluded_base - base;
	}}
	if (end > excluded_end) {{
		if (*count == LKM2_CP_MAX_MEMBLOCK_RANGES)
			return false;
		regions[*count].base = excluded_end;
		regions[(*count)++].size = end - excluded_end;
	}}
	return true;
}}

static struct lkm2_cp_memblock __init lkm2_cp_observe_reserved(void)
{{
	struct memblock_region projected[LKM2_CP_MAX_MEMBLOCK_RANGES] = {{ }};
	struct memblock_type type = {{ .regions = projected }};
	uint64_t kernel_base = IS_ENABLED(CONFIG_XIP_KERNEL) ?
		__pa_symbol(&_sdata) : __pa_symbol(&_start);
	uint64_t kernel_end = __pa_symbol(&_end);
	struct memblock_region *region;

	if (IS_ENABLED(CONFIG_64BIT) && IS_ENABLED(CONFIG_STRICT_KERNEL_RWX))
		kernel_end = ALIGN(kernel_end, PMD_SIZE);
	for_each_reserved_mem_region(region) {{
		if (!lkm2_cp_append_projected(projected, &type.cnt,
					       region->base, region->size,
					       kernel_base, kernel_end)) {{
			type.cnt = LKM2_CP_MAX_MEMBLOCK_RANGES + 1;
			break;
		}}
	}}
	if (type.cnt <= LKM2_CP_MAX_MEMBLOCK_RANGES) {{
		for_each_mem_region(region) {{
			if (!memblock_is_nomap(region))
				continue;
			if (!lkm2_cp_append_projected(projected, &type.cnt,
						       region->base, region->size,
						       kernel_base, kernel_end)) {{
				type.cnt = LKM2_CP_MAX_MEMBLOCK_RANGES + 1;
				break;
			}}
		}}
	}}
	return lkm2_cp_observe_memblock(&type);
}}

static void __init
lkm2_cp_emit_ranges(const struct lkm2_cp_memblock *observation, bool memory)
{{
	uint64_t i;

	for (i = 0; i < observation->stored; i++) {{
		if (memory)
			lkm2_checkpoint_memory_range(i, observation->ranges[i].base,
						 observation->ranges[i].end);
		else
			lkm2_checkpoint_reserved_range(i, observation->ranges[i].base,
						   observation->ranges[i].end);
	}}
}}

{milestones}
"""



def render_linux_include(
    checkpoints: tuple[Checkpoint, ...], mapping: CheckpointMapping
) -> str:
    if mapping.implementation_only:
        return _render_content_linux_include(checkpoints, mapping)
    if mapping.root_object == "MemBlock":
        return _render_memblock_linux_include(checkpoints, mapping)
    milestones = "\n\n".join(
        _render_milestone(name, checkpoints) for name in mapping.milestones
    )
    swapper_struct = "" if mapping.root_object != "SwapperPageTable" else """
struct lkm2_cp_swapper {
	uint64_t mode, fixmap_va, linear_va, linear_pa, linear_flags;
	uint64_t kernel_va, kernel_pa, kernel_flags;
	uint64_t fixmap_cleared, satp_switched, tlb_flush_completed;
	uint64_t late_mode_selected;
};
"""
    swapper_observer = "" if mapping.root_object != "SwapperPageTable" else """
static struct lkm2_cp_swapper __init lkm2_cp_observe_swapper(void)
{
	/* setup_vm_final has completed all of these operations at its checkpoint. */
	return (struct lkm2_cp_swapper) {
		.mode = satp_mode >> SATP_MODE_SHIFT,
		.fixmap_va = __fix_to_virt(FIX_FDT),
		.linear_va = PAGE_OFFSET,
		.linear_pa = memblock_start_of_DRAM(),
		.linear_flags = pgprot_val(PAGE_KERNEL),
		.kernel_va = kernel_map.virt_addr,
		.kernel_pa = kernel_map.phys_addr,
		/* The representative text mapping is strict-RWX (read/execute). */
		.kernel_flags = pgprot_val(PAGE_KERNEL_READ_EXEC),
		.fixmap_cleared = 1,
		.satp_switched = 1,
		.tlb_flush_completed = 1,
		.late_mode_selected = 1,
	};
}
""".strip("\n")
    if swapper_observer:
        swapper_observer = f"\n{swapper_observer}\n"
    return f"""/* @generated by lkm2 checkpointgen; do not edit. */
#include <linux/types.h>

{_render_c_declarations(checkpoints)}

struct lkm2_cp_kernel {{
	uint64_t kernel_va, kernel_pa, page_offset, kernel_va_pa_offset;
	uint64_t mode, levels, top_shift;
}};

struct lkm2_cp_trampoline {{
	uint64_t mode, va, pa, size, flags, path_ok;
}};

struct lkm2_cp_early_kernel {{
	uint64_t mode, va, pa, flags, coverage_ok;
}};

struct lkm2_cp_early_dtb {{
	uint64_t mode, dtb_pa, dtb_va, fix_va;
	uint64_t leaf0_pa, leaf0_flags, leaf1_pa, leaf1_flags;
	uint64_t size, coverage_ok;
}};
{swapper_struct}
static uint64_t __init lkm2_cp_pte_pa(uint64_t entry)
{{
	return ((entry & _PAGE_PFN_MASK) >> _PAGE_PFN_SHIFT) << PAGE_SHIFT;
}}

static uint64_t __init lkm2_cp_pte_flags(uint64_t entry)
{{
	return entry & 0x3ff;
}}

static bool __init lkm2_cp_table_matches(uint64_t entry, uintptr_t table)
{{
	return lkm2_cp_pte_pa(entry) == table && lkm2_cp_pte_flags(entry) == _PAGE_TABLE;
}}

static uint64_t __init lkm2_cp_walk(pgd_t *root, p4d_t *p4d, pud_t *pud,
				     pmd_t *pmd, uintptr_t va, uint64_t *path_ok)
{{
	uint64_t entry = pgd_val(root[pgd_index(va)]);
	bool ok;

	if (pgtable_l5_enabled) {{
		ok = lkm2_cp_table_matches(entry, (uintptr_t)p4d);
		entry = p4d_val(p4d[p4d_index(va)]);
		ok = ok && lkm2_cp_table_matches(entry, (uintptr_t)pud);
	}} else if (pgtable_l4_enabled) {{
		ok = lkm2_cp_table_matches(entry, (uintptr_t)pud);
	}} else {{
		ok = lkm2_cp_table_matches(entry, (uintptr_t)pmd);
	}}
	if (pgtable_l4_enabled) {{
		entry = pud_val(pud[pud_index(va)]);
		ok = ok && lkm2_cp_table_matches(entry, (uintptr_t)pmd);
	}}
	*path_ok = ok;
	return pmd_val(pmd[pmd_index(va)]);
}}

static struct lkm2_cp_kernel __init lkm2_cp_observe_kernel(void)
{{
	uint64_t mode = satp_mode >> SATP_MODE_SHIFT;
	struct lkm2_cp_kernel result = {{
		.kernel_va = kernel_map.virt_addr,
		.kernel_pa = kernel_map.phys_addr,
		.page_offset = kernel_map.page_offset,
		.kernel_va_pa_offset = kernel_map.va_kernel_pa_offset,
		.mode = mode,
		.levels = pgtable_l5_enabled ? 5 : (pgtable_l4_enabled ? 4 : 3),
		.top_shift = pgtable_l5_enabled ? PGDIR_SHIFT :
			(pgtable_l4_enabled ? P4D_SHIFT : PUD_SHIFT),
	}};

	return result;
}}

static struct lkm2_cp_trampoline __init lkm2_cp_observe_trampoline(void)
{{
	uint64_t path_ok, leaf = lkm2_cp_walk(trampoline_pg_dir, trampoline_p4d,
					      trampoline_pud, trampoline_pmd,
					      kernel_map.virt_addr, &path_ok);
	struct lkm2_cp_trampoline result = {{
		.mode = satp_mode >> SATP_MODE_SHIFT,
		.va = kernel_map.virt_addr,
		.pa = lkm2_cp_pte_pa(leaf),
		.size = PMD_SIZE,
		.flags = lkm2_cp_pte_flags(leaf),
		.path_ok = path_ok,
	}};

	return result;
}}

static struct lkm2_cp_early_kernel __init lkm2_cp_observe_early_kernel(void)
{{
	uintptr_t va, end = kernel_map.virt_addr + kernel_map.size;
	uint64_t first_path, first = lkm2_cp_walk(early_pg_dir, early_p4d, early_pud,
						  early_pmd, kernel_map.virt_addr,
						  &first_path);
	bool coverage_ok = first_path;

	for (va = kernel_map.virt_addr; va < end; va += PMD_SIZE) {{
		uint64_t path_ok, leaf = lkm2_cp_walk(early_pg_dir, early_p4d,
						       early_pud, early_pmd, va,
						       &path_ok);
		uint64_t expected_pa = kernel_map.phys_addr + (va - kernel_map.virt_addr);

		coverage_ok = coverage_ok && path_ok && lkm2_cp_pte_pa(leaf) == expected_pa &&
			lkm2_cp_pte_flags(leaf) == pgprot_val(PAGE_KERNEL_EXEC);
	}}
	return (struct lkm2_cp_early_kernel) {{
		.mode = satp_mode >> SATP_MODE_SHIFT,
		.va = kernel_map.virt_addr,
		.pa = lkm2_cp_pte_pa(first),
		.flags = lkm2_cp_pte_flags(first),
		.coverage_ok = coverage_ok,
	}};
}}

static struct lkm2_cp_early_dtb __init lkm2_cp_observe_early_dtb(void)
{{
	uintptr_t fix_va = __fix_to_virt(FIX_FDT);
	uint64_t path0, path1;
	uint64_t leaf0 = lkm2_cp_walk(early_pg_dir, fixmap_p4d, fixmap_pud,
					      fixmap_pmd, fix_va, &path0);
	uint64_t leaf1 = lkm2_cp_walk(early_pg_dir, fixmap_p4d, fixmap_pud,
					      fixmap_pmd, fix_va + PMD_SIZE, &path1);
	uint64_t expected_pa = dtb_early_pa & PMD_MASK;
	bool coverage_ok = path0 && path1 && lkm2_cp_pte_pa(leaf0) == expected_pa &&
		lkm2_cp_pte_pa(leaf1) == expected_pa + PMD_SIZE &&
		lkm2_cp_pte_flags(leaf0) == pgprot_val(PAGE_KERNEL) &&
		lkm2_cp_pte_flags(leaf1) == pgprot_val(PAGE_KERNEL);

	return (struct lkm2_cp_early_dtb) {{
		.mode = satp_mode >> SATP_MODE_SHIFT,
		.dtb_pa = dtb_early_pa,
		.dtb_va = (uintptr_t)dtb_early_va,
		.fix_va = fix_va,
		.leaf0_pa = lkm2_cp_pte_pa(leaf0),
		.leaf0_flags = lkm2_cp_pte_flags(leaf0),
		.leaf1_pa = lkm2_cp_pte_pa(leaf1),
		.leaf1_flags = lkm2_cp_pte_flags(leaf1),
		.size = 2 * PMD_SIZE,
		.coverage_ok = coverage_ok,
	}};
}}

{swapper_observer}{milestones}
"""




def _render_content_linux_include(
    checkpoints: tuple[Checkpoint, ...], mapping: CheckpointMapping
) -> str:
    """Render the implementation-only page-table content observer.

    The walker deliberately uses the architecture's typed page-table accessors
    (rather than retaining a flattened sequence or dereferencing raw physical
    addresses).  Huge leaves are projected to 4 KiB tuples before hashing.
    """
    milestones = "\n\n".join(
        _render_milestone(name, checkpoints) for name in mapping.milestones
    )
    return f"""/* @generated by lkm2 checkpointgen; swapper content fragment. */
#include <linux/types.h>

{_render_c_declarations(checkpoints)}
extern void lkm2_checkpoint_content_chunk(uint64_t class_id, uint64_t chunk,
					 uint64_t count, uint64_t lo, uint64_t hi);
extern void lkm2_checkpoint_content_item(uint64_t class_id, uint64_t index,
					uint64_t va, uint64_t pa, uint64_t flags);

#define LKM2_PTE_CONTENT_PAGE_SHIFT 12
#define LKM2_PTE_CONTENT_PMD_SHIFT 21
#define LKM2_PTE_CONTENT_PUD_SHIFT 30
#define LKM2_PTE_CONTENT_FNV_PRIME 0x100000001b3ULL
#define LKM2_PTE_CONTENT_FNV_LO 0xcbf29ce484222325ULL
#define LKM2_PTE_CONTENT_FNV_HI 0x84222325cbf29ce4ULL

struct lkm2_cp_content {{
	bool valid;
	uint64_t count, lo, hi;
	uint64_t class_id, diagnostic_lo, diagnostic_hi;
	uint64_t diagnostic_count, diagnostic_chunk;
}};

static uint64_t lkm2_cp_diag_class, lkm2_cp_diag_stage, lkm2_cp_diag_chunk;

static void __init lkm2_cp_diag_configure(void)
{{
	const char *value = strstr(boot_command_line, "lkm2.ptdiag=");
	uint64_t chunk = 0;
	lkm2_cp_diag_class = lkm2_cp_diag_stage = lkm2_cp_diag_chunk = 0;
	if (!value)
		return;
	value += sizeof("lkm2.ptdiag=") - 1;
	if (!strncmp(value, "fixmap,", 7)) lkm2_cp_diag_class = 1, value += 7;
	else if (!strncmp(value, "linear,", 7)) lkm2_cp_diag_class = 2, value += 7;
	else if (!strncmp(value, "kernel,", 7)) lkm2_cp_diag_class = 3, value += 7;
	else return;
	if (!strncmp(value, "chunks", 6) && (!value[6] || value[6] == ' '))
		lkm2_cp_diag_stage = 1;
	else if (!strncmp(value, "items,", 6)) {{
		value += 6;
		if (*value < '0' || *value > '9') return;
		while (*value >= '0' && *value <= '9')
			chunk = chunk * 10 + *value++ - '0';
		if (*value && *value != ' ') return;
		lkm2_cp_diag_chunk = chunk;
		lkm2_cp_diag_stage = 2;
	}}
}}

struct lkm2_cp_content_observation {{
	uint64_t fixmap_valid, fixmap_count, fixmap_digest_lo, fixmap_digest_hi;
	uint64_t linear_valid, linear_count, linear_digest_lo, linear_digest_hi;
	uint64_t kernel_walk_valid;
}};

static uint64_t __init lkm2_cp_content_byte(uint64_t state, uint8_t byte)
{{
	return (state ^ byte) * LKM2_PTE_CONTENT_FNV_PRIME;
}}

static void __init lkm2_cp_content_word(uint64_t *lo, uint64_t *hi, uint64_t value)
{{
	int shift;

	for (shift = 0; shift < 64; shift += 8) {{
		*lo = lkm2_cp_content_byte(*lo, (value >> shift) & 0xff);
		*hi = lkm2_cp_content_byte(*hi, (value >> shift) & 0xff);
	}}
}}

static void __init lkm2_cp_content_diag_begin(struct lkm2_cp_content *state)
{{
	const char header[] = "LKMPTE1";
	int i;
	state->diagnostic_lo = LKM2_PTE_CONTENT_FNV_LO;
	state->diagnostic_hi = LKM2_PTE_CONTENT_FNV_HI;
	state->diagnostic_count = 0;
	for (i = 0; i < sizeof(header) - 1; i++) {{
		state->diagnostic_lo = lkm2_cp_content_byte(state->diagnostic_lo, header[i]);
		state->diagnostic_hi = lkm2_cp_content_byte(state->diagnostic_hi, header[i]);
	}}
	state->diagnostic_lo = lkm2_cp_content_byte(state->diagnostic_lo, 1);
	state->diagnostic_hi = lkm2_cp_content_byte(state->diagnostic_hi, 1);
	state->diagnostic_lo = lkm2_cp_content_byte(state->diagnostic_lo, state->class_id);
	state->diagnostic_hi = lkm2_cp_content_byte(state->diagnostic_hi, state->class_id);
}}

static struct lkm2_cp_content __init lkm2_cp_content_begin(uint8_t class_id)
{{
	struct lkm2_cp_content result = {{
		.valid = true,
		.lo = LKM2_PTE_CONTENT_FNV_LO,
		.hi = LKM2_PTE_CONTENT_FNV_HI,
		.class_id = class_id,
	}};
	const char header[] = "LKMPTE1";
	int i;

	for (i = 0; i < sizeof(header) - 1; i++) {{
		result.lo = lkm2_cp_content_byte(result.lo, header[i]);
		result.hi = lkm2_cp_content_byte(result.hi, header[i]);
	}}
	result.lo = lkm2_cp_content_byte(result.lo, 1);
	result.hi = lkm2_cp_content_byte(result.hi, 1);
	result.lo = lkm2_cp_content_byte(result.lo, class_id);
	result.hi = lkm2_cp_content_byte(result.hi, class_id);
	lkm2_cp_content_diag_begin(&result);
	return result;
}}

/* Return the leaf PTE and its level shift.  All address arithmetic is checked
 * by callers; this helper only follows valid architecture page-table entries. */
static bool __init lkm2_cp_content_walk(uintptr_t va, uint64_t *pa,
					uint64_t *flags, unsigned int *shift)
{{
	pgd_t *pgdp = &swapper_pg_dir[pgd_index(va)];
	pgd_t pgd = READ_ONCE(*pgdp);
	p4d_t *p4dp;
	pud_t *pudp;
	pmd_t *pmdp;
	pte_t *ptep;
	if (pgd_none(pgd) || pgd_bad(pgd) ||
	    (pgtable_l5_enabled && (pgd_val(pgd) & 0x3ff) != _PAGE_TABLE))
		return false;
	p4dp = p4d_offset(pgdp, va);
	if (p4d_none(*p4dp) || p4d_bad(*p4dp) ||
	    (pgtable_l4_enabled && (p4d_val(*p4dp) & 0x3ff) != _PAGE_TABLE))
		return false;
	pudp = pud_offset(p4dp, va);
	if (pud_leaf(*pudp)) {{
		*pa = ((pud_val(*pudp) & _PAGE_PFN_MASK) >> _PAGE_PFN_SHIFT) << PAGE_SHIFT;
		*flags = pud_val(*pudp) & 0x3ff;
		*shift = PUD_SHIFT;
		return true;
	}}
	if (pud_none(*pudp) || pud_bad(*pudp) ||
	    (pud_val(*pudp) & 0x3ff) != _PAGE_TABLE)
		return false;
	pmdp = pmd_offset(pudp, va);
	if (pmd_leaf(*pmdp)) {{
		*pa = ((pmd_val(*pmdp) & _PAGE_PFN_MASK) >> _PAGE_PFN_SHIFT) << PAGE_SHIFT;
		*flags = pmd_val(*pmdp) & 0x3ff;
		*shift = PMD_SHIFT;
		return true;
	}}
	if (pmd_none(*pmdp) || pmd_bad(*pmdp) ||
	    (pmd_val(*pmdp) & 0x3ff) != _PAGE_TABLE)
		return false;
	ptep = pte_offset_kernel(pmdp, va);
	if (!ptep || pte_none(*ptep))
		return false;
	*pa = ((pte_val(*ptep) & _PAGE_PFN_MASK) >> _PAGE_PFN_SHIFT) << PAGE_SHIFT;
	*flags = pte_val(*ptep) & 0x3ff;
	*shift = PAGE_SHIFT;
	return (*flags & _PAGE_PRESENT) != 0;
}}

static bool __init lkm2_cp_content_resolve(uintptr_t va, uint64_t *pa,
					   uint64_t *flags)
{{
	uint64_t base, offset, mask;
	unsigned int shift;

	if (!lkm2_cp_content_walk(va, &base, flags, &shift) ||
	    (shift != PUD_SHIFT && shift != PMD_SHIFT && shift != PAGE_SHIFT))
		return false;
	mask = (1ULL << shift) - 1;
	offset = va & mask;
	if (base & mask || base > ~0ULL - offset ||
	    !(*flags & _PAGE_PRESENT) || !(*flags & _PAGE_LEAF) ||
	    (*flags & _PAGE_WRITE && !(*flags & _PAGE_READ)))
		return false;
	*pa = base + offset;
	return true;
}}

static void __init lkm2_cp_content_push(struct lkm2_cp_content *state,
						uint64_t va, uint64_t pa, uint64_t flags)
{{
	uint64_t index = state->count;
	flags &= state->class_id == 1 ? 0xff : _PAGE_PRESENT | _PAGE_READ;
	lkm2_cp_content_word(&state->lo, &state->hi, va);
	lkm2_cp_content_word(&state->lo, &state->hi, pa);
	lkm2_cp_content_word(&state->lo, &state->hi, flags);
	state->count++;
	if (lkm2_cp_diag_class != state->class_id)
		return;
	if (lkm2_cp_diag_stage == 1) {{
		lkm2_cp_content_word(&state->diagnostic_lo, &state->diagnostic_hi, va);
		lkm2_cp_content_word(&state->diagnostic_lo, &state->diagnostic_hi, pa);
		lkm2_cp_content_word(&state->diagnostic_lo, &state->diagnostic_hi, flags);
		state->diagnostic_count++;
		if (state->diagnostic_count == 512) {{
			lkm2_cp_content_word(&state->diagnostic_lo, &state->diagnostic_hi,
					     state->diagnostic_count);
			lkm2_checkpoint_content_chunk(state->class_id, state->diagnostic_chunk,
						state->diagnostic_count, state->diagnostic_lo,
						state->diagnostic_hi);
			state->diagnostic_chunk++;
			lkm2_cp_content_diag_begin(state);
		}}
	}} else if (lkm2_cp_diag_stage == 2 && index / 512 == lkm2_cp_diag_chunk) {{
		lkm2_checkpoint_content_item(state->class_id, index, va, pa, flags);
	}}
}}

static void __init lkm2_cp_content_finish(struct lkm2_cp_content *state)
{{
	if (lkm2_cp_diag_class == state->class_id && lkm2_cp_diag_stage == 1 &&
	    state->diagnostic_count) {{
		lkm2_cp_content_word(&state->diagnostic_lo, &state->diagnostic_hi,
				     state->diagnostic_count);
		lkm2_checkpoint_content_chunk(state->class_id, state->diagnostic_chunk,
					state->diagnostic_count, state->diagnostic_lo,
					state->diagnostic_hi);
		state->diagnostic_count = 0;
	}}
	lkm2_cp_content_word(&state->lo, &state->hi, state->count);
}}

static struct lkm2_cp_content __init lkm2_cp_observe_content_fixmap(void)
{{
	struct lkm2_cp_content result = lkm2_cp_content_begin(1);
	uintptr_t va = __fix_to_virt(FIX_FDT);
	unsigned int i;
	/* The final FDT window is two PMD leaves; project each to 4 KiB. */
	for (i = 0; i < 2 * PMD_SIZE / PAGE_SIZE; i++, va += PAGE_SIZE) {{
		uint64_t pa, flags;
		if (!lkm2_cp_content_resolve(va, &pa, &flags)) {{
			result.valid = false;
			break;
		}}
		lkm2_cp_content_push(&result, va, pa, flags);
	}}
	lkm2_cp_content_finish(&result);
	return result;
}}

static struct lkm2_cp_content __init lkm2_cp_observe_content_linear(void)
{{
	struct lkm2_cp_content result = lkm2_cp_content_begin(2);
	uint64_t start, end, i, pa;
	for_each_mem_range(i, &start, &end) {{
		for (pa = start; pa < end; pa += PAGE_SIZE) {{
			uintptr_t va = (uintptr_t)__va(pa);
			uint64_t mapped_pa, flags;
			if (!lkm2_cp_content_resolve(va, &mapped_pa, &flags) ||
			    mapped_pa != pa || !(flags & _PAGE_READ) ||
			    (flags & _PAGE_USER)) {{
				result.valid = false;
				break;
			}}
			lkm2_cp_content_push(&result, va, mapped_pa, flags);
		}}
		if (!result.valid)
			break;
	}}
	lkm2_cp_content_finish(&result);
	return result;
}}

static uint64_t __init lkm2_cp_observe_content_kernel(void)
{{
	uintptr_t va, end = kernel_map.virt_addr + kernel_map.size;
	if (end < kernel_map.virt_addr || !kernel_map.size ||
	    kernel_map.virt_addr & (PAGE_SIZE - 1))
		return 0;
	for (va = kernel_map.virt_addr; va < end; va += PAGE_SIZE) {{
		uint64_t pa, flags, expected = kernel_map.phys_addr +
			(va - kernel_map.virt_addr);
		if (expected < kernel_map.phys_addr ||
		    !lkm2_cp_content_resolve(va, &pa, &flags) || pa != expected ||
		    (flags & _PAGE_USER) || (flags & _PAGE_WRITE && flags & _PAGE_EXEC))
			return 0;
	}}
	return 1;
}}

static struct lkm2_cp_content __init lkm2_cp_observe_content_fixmap_final(void)
{{
	return lkm2_cp_observe_content_fixmap();
}}

static struct lkm2_cp_content __init lkm2_cp_observe_content_linear_final(void)
{{
	return lkm2_cp_observe_content_linear();
}}

static struct lkm2_cp_content_observation __init lkm2_cp_observe_content(void)
{{
	struct lkm2_cp_content fixmap, linear;
	lkm2_cp_diag_configure();
	fixmap = lkm2_cp_observe_content_fixmap_final();
	linear = lkm2_cp_observe_content_linear_final();
	return (struct lkm2_cp_content_observation) {{
		.fixmap_valid = fixmap.valid, .fixmap_count = fixmap.count,
		.fixmap_digest_lo = fixmap.lo, .fixmap_digest_hi = fixmap.hi,
		.linear_valid = linear.valid, .linear_count = linear.count,
		.linear_digest_lo = linear.lo, .linear_digest_hi = linear.hi,
		.kernel_walk_valid = lkm2_cp_observe_content_kernel(),
	}};
}}

{milestones}
"""
def render_linux_handler(
    checkpoints: tuple[Checkpoint, ...], *, include_ranges: bool = False
) -> str:
    lines = [
        "// SPDX-License-Identifier: GPL-2.0-only",
        "/* @generated by lkm2 checkpointgen; fixed raw SBI DBCN handler. */",
        "#include <linux/types.h>",
        "",
        "#define LKM2_SBI_EXT_DBCN 0x4442434eUL",
        "#define LKM2_SBI_DBCN_WRITE_BYTE 2UL",
        "",
        "static void lkm2_cp_write_byte(uint8_t byte)",
        "{",
        '\tregister unsigned long a0 asm ("a0") = byte;',
        '\tregister unsigned long a1 asm ("a1") = 0;',
        '\tregister unsigned long a6 asm ("a6") = LKM2_SBI_DBCN_WRITE_BYTE;',
        '\tregister unsigned long a7 asm ("a7") = LKM2_SBI_EXT_DBCN;',
        "",
        '\tasm volatile ("ecall" : "+r" (a0), "+r" (a1), "+r" (a6), "+r" (a7) :: "memory");',
        "}",
        "",
        "static void lkm2_cp_write_bytes(const char *bytes)",
        "{",
        "\twhile (*bytes)",
        "\t\tlkm2_cp_write_byte(*bytes++);",
        "}",
        "",
        "static void lkm2_cp_write_hex(uint64_t value)",
        "{",
        '\tstatic const char digits[] = "0123456789abcdef";',
        "\tint shift;",
        "",
        "\tfor (shift = 60; shift >= 0; shift -= 4)",
        "\t\tlkm2_cp_write_byte(digits[(value >> shift) & 0xf]);",
        "}",
        "",
        _render_c_declarations(checkpoints),
    ]
    for item in checkpoints:
        args = ", ".join(f"arg{index}" for index, _ in enumerate(item.parameters))
        lines.extend(
            [
                "",
                f"void {item.symbol}({_c_parameters(item)})",
                "{",
                f'\tlkm2_cp_write_bytes("LKMCP1 id={item.canonical_id} hash={item.hash16}");',
            ]
        )
        for index, parameter in enumerate(item.parameters):
            lines.append(f'\tlkm2_cp_write_bytes(" {parameter}=0x");')
            lines.append(f"\tlkm2_cp_write_hex(arg{index});")
        if not args:
            pass
        lines.extend(["\tlkm2_cp_write_byte('\\n');", "}"])
    if include_ranges:
        for kind in ("memory", "reserved"):
            lines.extend(
                [
                    "",
                    f"void lkm2_checkpoint_{kind}_range(uint64_t index, uint64_t base, uint64_t end)",
                    "{",
                    f'\tlkm2_cp_write_bytes("LKMRNG1 kind={kind} index=0x");',
                    "\tlkm2_cp_write_hex(index);",
                    '\tlkm2_cp_write_bytes(" base=0x");',
                    "\tlkm2_cp_write_hex(base);",
                    '\tlkm2_cp_write_bytes(" end=0x");',
                    "\tlkm2_cp_write_hex(end);",
                    "\tlkm2_cp_write_byte('\\n');",
                    "}",
                ]
            )
    return "\n".join(lines) + "\n"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise CheckpointGenerationError(
            f"sibling anchor {label!r} occurs {count} times instead of once"
        )
    return source.replace(old, new, 1)


def _modify_init(source: str) -> str:
    source = _replace_once(
        source,
        "#endif\n\nasmlinkage void __init setup_vm(uintptr_t dtb_pa)\n{",
        '#endif\n\n#include "lkm2_checkpoints.inc"\n\nasmlinkage void __init setup_vm(uintptr_t dtb_pa)\n{',
        "checkpoint include",
    )
    source = _replace_once(
        source,
        "\tapply_early_boot_alternatives();\n\tpt_ops_set_early();",
        "\tlkm2_checkpoint_kernel_map_ready();\n\tlkm2_checkpoint_preset_complete();\n\n"
        "\tapply_early_boot_alternatives();\n\tpt_ops_set_early();",
        "kernel map ready",
    )
    source = _replace_once(
        source,
        "#endif\n\n\t/*\n\t * Setup early PGD covering entire kernel",
        "#endif\n\tlkm2_checkpoint_trampoline_ready();\n\n\t/*\n\t * Setup early PGD covering entire kernel",
        "trampoline ready",
    )
    source = _replace_once(
        source,
        "\tcreate_kernel_page_table(early_pg_dir, true);\n\n\t/* Setup early mapping for FDT early scan */",
        "\tcreate_kernel_page_table(early_pg_dir, true);\n"
        "\tlkm2_checkpoint_early_kernel_ready();\n\n\t/* Setup early mapping for FDT early scan */",
        "early kernel ready",
    )
    source = _replace_once(
        source,
        "\tcreate_fdt_early_page_table(__fix_to_virt(FIX_FDT), dtb_pa);\n\n\t/*\n\t * Bootime fixmap",
        "\tcreate_fdt_early_page_table(__fix_to_virt(FIX_FDT), dtb_pa);\n"
        "\tlkm2_checkpoint_early_dtb_ready();\n"
        "\tlkm2_checkpoint_setup_complete();\n\n\t/*\n\t * Bootime fixmap",
        "early DTB and setup complete",
    )
    return source


def _modify_init_swapper(source: str) -> str:
    source = _replace_once(
        source,
        "#endif\n\nasmlinkage void __init setup_vm(uintptr_t dtb_pa)\n{",
        '#endif\n\n#include "lkm2_checkpoints.inc"\n\nasmlinkage void __init setup_vm(uintptr_t dtb_pa)\n{',
        "checkpoint include",
    )
    source = _replace_once(
        source,
        "\tpt_ops_set_late();",
        "\tpt_ops_set_late();\n\tlkm2_checkpoint_swapper_online();",
        "swapper online",
    )
    return source


def _modify_init_swapper_incremental(source: str) -> str:
    """Add the M1 observation to an already VM-instrumented Linux tree."""

    return _replace_once(
        source,
        "\tpt_ops_set_late();",
        "\tpt_ops_set_late();\n\tlkm2_checkpoint_swapper_online();",
        "swapper online",
    )


def _modify_init_swapper_content(source: str) -> str:
    """Append content observation after the late page-table callbacks."""
    return _replace_once(
        source,
        "\tlkm2_checkpoint_swapper_online();",
        "\tlkm2_checkpoint_swapper_online();\n\tlkm2_checkpoint_swapper_content();",
        "swapper content observation",
    )


def _modify_init_memblock_incremental(source: str) -> str:
    """Observe memblock at setup_bootmem completion, before final page tables."""

    return _replace_once(
        source,
        "\tsetup_bootmem();\n\tsetup_vm_final();",
        "\tsetup_bootmem();\n"
        "\tlkm2_checkpoint_memblock_ready();\n"
        "\tlkm2_checkpoint_memblock_memory_online();\n"
        "\tlkm2_checkpoint_memblock_reserved_online();\n"
        "\tlkm2_checkpoint_memblock_online();\n"
        "\tsetup_vm_final();",
        "MemBlock setup boundary",
    )


def _swapper_include_append(
    existing: str, checkpoints: tuple[Checkpoint, ...], mapping: CheckpointMapping
) -> str:
    """Append only swapper declarations/observation to the frozen VM include."""

    rendered = render_linux_include(checkpoints, mapping)
    declaration_end = "\n\nstruct lkm2_cp_kernel {"
    declarations = rendered.split(declaration_end, 1)[0]
    declarations = "\n".join(
        line for line in declarations.splitlines() if line.startswith("extern void ")
    )
    struct_start = rendered.index("struct lkm2_cp_swapper {")
    struct_end = rendered.index("\n};", struct_start) + len("\n};")
    struct = rendered[struct_start:struct_end]
    observer_start = rendered.index("static struct lkm2_cp_swapper __init lkm2_cp_observe_swapper")
    milestone_start = rendered.index("static void __init lkm2_checkpoint_swapper_online", observer_start)
    observer = rendered[observer_start:milestone_start].rstrip()
    milestone = rendered[milestone_start:].rstrip()
    suffix = "\n\n".join((declarations, struct, observer, milestone))
    return existing.rstrip() + "\n\n" + suffix + "\n"


def _swapper_handler_append(
    existing: str, checkpoints: tuple[Checkpoint, ...]
) -> str:
    """Append swapper handler functions without duplicating shared helpers."""

    rendered = render_linux_handler(checkpoints)
    first_function = rendered.index("\nvoid lkm_checkpoint_") + 1
    return existing.rstrip() + "\n\n" + rendered[first_function:].rstrip() + "\n"


def _memblock_include_append(
    existing: str, checkpoints: tuple[Checkpoint, ...], mapping: CheckpointMapping
) -> str:
    """Append the independent MemBlock observation fragment."""

    rendered = render_linux_include(checkpoints, mapping)
    return existing.rstrip() + "\n\n" + rendered.rstrip() + "\n"


def _memblock_handler_append(
    existing: str, checkpoints: tuple[Checkpoint, ...]
) -> str:
    """Append MemBlock handlers while reusing the frozen raw DBCN helpers."""

    rendered = render_linux_handler(checkpoints, include_ranges=True)
    first_function = rendered.index("\nvoid lkm_checkpoint_") + 1
    return existing.rstrip() + "\n\n" + rendered[first_function:].rstrip() + "\n"


def _swapper_content_include_append(
    existing: str, checkpoints: tuple[Checkpoint, ...], mapping: CheckpointMapping
) -> str:
    return existing.rstrip() + "\n\n" + render_linux_include(checkpoints, mapping).rstrip() + "\n"


def _swapper_content_handler_append(
    existing: str, checkpoints: tuple[Checkpoint, ...]
) -> str:
    rendered = render_linux_handler(checkpoints)
    first_function = rendered.index("\nvoid lkm_checkpoint_") + 1
    diagnostics = r'''

void lkm2_checkpoint_content_chunk(uint64_t class_id, uint64_t chunk,
				   uint64_t count, uint64_t lo, uint64_t hi)
{
	const char *name = class_id == 1 ? "fixmap" :
		(class_id == 2 ? "linear" : "kernel");
	lkm2_cp_write_bytes("LKMPTC1 class="); lkm2_cp_write_bytes(name);
	lkm2_cp_write_bytes(" chunk=0x"); lkm2_cp_write_hex(chunk);
	lkm2_cp_write_bytes(" count=0x"); lkm2_cp_write_hex(count);
	lkm2_cp_write_bytes(" digest_lo=0x"); lkm2_cp_write_hex(lo);
	lkm2_cp_write_bytes(" digest_hi=0x"); lkm2_cp_write_hex(hi);
	lkm2_cp_write_byte('\n');
}

void lkm2_checkpoint_content_item(uint64_t class_id, uint64_t index,
				  uint64_t va, uint64_t pa, uint64_t flags)
{
	const char *name = class_id == 1 ? "fixmap" :
		(class_id == 2 ? "linear" : "kernel");
	lkm2_cp_write_bytes("LKMPTI1 class="); lkm2_cp_write_bytes(name);
	lkm2_cp_write_bytes(" index=0x"); lkm2_cp_write_hex(index);
	lkm2_cp_write_bytes(" va=0x"); lkm2_cp_write_hex(va);
	lkm2_cp_write_bytes(" pa=0x"); lkm2_cp_write_hex(pa);
	lkm2_cp_write_bytes(" flags=0x"); lkm2_cp_write_hex(flags);
	lkm2_cp_write_byte('\n');
}'''
    return existing.rstrip() + "\n\n" + rendered[first_function:].rstrip() + diagnostics + "\n"


def validate_incremental_content_sibling(
    sibling: Path,
    mapping: CheckpointMapping,
    checkpoints: tuple[Checkpoint, ...],
) -> str | None:
    """Recognize the integrated or reviewed content patch after MemBlock."""
    if not mapping.implementation_only:
        raise CheckpointGenerationError("content validation requires implementation-only mapping")
    if not sibling.is_dir():
        raise CheckpointGenerationError(f"sibling path does not exist: {sibling}")
    _validate_branch(sibling, mapping)
    head = _git(sibling, "rev-parse", "HEAD")
    if (
        mapping.sibling_integrated.commit is not None
        and head == mapping.sibling_integrated.commit
    ):
        status_lines = _git(sibling, "status", "--short").splitlines()
        if status_lines:
            raise CheckpointGenerationError(
                "integrated sibling swapper-content worktree must be clean"
            )
        _validate_worktree_files(sibling, mapping.sibling_integrated, "integrated")
        return "integrated-swapper-content"
    if mapping.sibling_patch_base.commit != head:
        return None
    status_lines = _git(sibling, "status", "--short").splitlines()
    if not status_lines:
        return None
    if any(line and line[0] not in {" ", "?"} for line in status_lines):
        raise CheckpointGenerationError("sibling content changes must remain unstaged")
    expected_paths = {
        "arch/riscv/mm/init.c",
        "arch/riscv/mm/lkm2_checkpoint_handler.c",
        "arch/riscv/mm/lkm2_checkpoints.inc",
    }
    changed_paths = {line[3:] for line in status_lines if len(line) >= 4}
    if changed_paths != expected_paths:
        raise CheckpointGenerationError(
            "sibling must contain exactly the reviewed, unstaged swapper-content patch"
        )
    baseline = mapping.sibling_patch_base
    expected = {
        "arch/riscv/mm/init.c": _modify_init_swapper_content(
            _read_revision_file(sibling, baseline, "arch/riscv/mm/init.c")
        ),
        "arch/riscv/mm/lkm2_checkpoints.inc": _swapper_content_include_append(
            _read_revision_file(sibling, baseline, "arch/riscv/mm/lkm2_checkpoints.inc"),
            checkpoints,
            mapping,
        ),
        "arch/riscv/mm/lkm2_checkpoint_handler.c": _swapper_content_handler_append(
            _read_revision_file(sibling, baseline, "arch/riscv/mm/lkm2_checkpoint_handler.c"),
            checkpoints,
        ),
    }
    for relative, content in expected.items():
        try:
            actual = (sibling / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CheckpointGenerationError(
                f"cannot read sibling content patch anchor {relative}: {exc}"
            ) from exc
        if actual != content:
            raise CheckpointGenerationError(
                f"sibling content patch differs from generated {relative}"
            )
    return "reviewed-swapper-content-patch"


def _modify_makefile(source: str) -> str:
    source = _replace_once(
        source,
        "CFLAGS_init.o := -mcmodel=medany\n",
        "CFLAGS_init.o := -mcmodel=medany\n"
        "CFLAGS_lkm2_checkpoint_handler.o := -mcmodel=medany\n",
        "handler code model",
    )
    source = _replace_once(
        source,
        "CFLAGS_init.o += -fno-pie\n",
        "CFLAGS_init.o += -fno-pie\n"
        "CFLAGS_lkm2_checkpoint_handler.o += -fno-pie\n",
        "handler relocatable flags",
    )
    source = _replace_once(
        source,
        "CFLAGS_REMOVE_init.o = $(CC_FLAGS_FTRACE)\n",
        "CFLAGS_REMOVE_init.o = $(CC_FLAGS_FTRACE)\n"
        "CFLAGS_REMOVE_lkm2_checkpoint_handler.o = $(CC_FLAGS_FTRACE)\n",
        "handler ftrace flags",
    )
    source = _replace_once(
        source,
        "KCOV_INSTRUMENT_init.o := n\n",
        "KCOV_INSTRUMENT_init.o := n\n"
        "KCOV_INSTRUMENT_lkm2_checkpoint_handler.o := n\n"
        "KASAN_SANITIZE_lkm2_checkpoint_handler.o := n\n"
        "KCSAN_SANITIZE_lkm2_checkpoint_handler.o := n\n",
        "handler instrumentation",
    )
    source = _replace_once(
        source,
        "obj-y += init.o\n",
        "obj-y += init.o lkm2_checkpoint_handler.o\n",
        "handler object",
    )
    return source


def _diff_file(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _diff_new_file(path: str, content: str) -> str:
    lines = content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            [], lines, fromfile="/dev/null", tofile=f"b/{path}"
        )
    )


def generate_sibling_patch(
    sibling: Path,
    mapping: CheckpointMapping,
    checkpoints: tuple[Checkpoint, ...],
) -> str:
    validate_sibling(sibling, mapping)
    init_path = "arch/riscv/mm/init.c"
    makefile_path = "arch/riscv/mm/Makefile"

    if mapping.implementation_only:
        baseline = mapping.sibling_patch_base
        include_path = "arch/riscv/mm/lkm2_checkpoints.inc"
        handler_path = "arch/riscv/mm/lkm2_checkpoint_handler.c"
        init_before = _read_revision_file(sibling, baseline, init_path)
        include_before = _read_revision_file(sibling, baseline, include_path)
        handler_before = _read_revision_file(sibling, baseline, handler_path)
        generated = {
            init_path: _modify_init_swapper_content(init_before),
            include_path: _swapper_content_include_append(include_before, checkpoints, mapping),
            handler_path: _swapper_content_handler_append(handler_before, checkpoints),
        }
        return (
            _diff_file(init_path, init_before, generated[init_path])
            + _diff_file(include_path, include_before, generated[include_path])
            + _diff_file(handler_path, handler_before, generated[handler_path])
        )

    if mapping.root_object in {"SwapperPageTable", "MemBlock"}:
        # The sibling repository is already at the integrated VM checkpoint
        # commit.  M1 is deliberately an incremental patch: preserve the
        # existing 28-record protocol and add only the final observation.
        baseline = mapping.sibling_patch_base
        init_before = _read_revision_file(sibling, baseline, init_path)
        include_path = "arch/riscv/mm/lkm2_checkpoints.inc"
        handler_path = "arch/riscv/mm/lkm2_checkpoint_handler.c"
        include_before = _read_revision_file(sibling, baseline, include_path)
        handler_before = _read_revision_file(sibling, baseline, handler_path)
        if mapping.root_object == "SwapperPageTable":
            init_after = _modify_init_swapper_incremental(init_before)
            include_after = _swapper_include_append(include_before, checkpoints, mapping)
            handler_after = _swapper_handler_append(handler_before, checkpoints)
        else:
            init_after = _modify_init_memblock_incremental(init_before)
            include_after = _memblock_include_append(include_before, checkpoints, mapping)
            handler_after = _memblock_handler_append(handler_before, checkpoints)
        generated = {
            init_path: init_after,
            include_path: include_after,
            handler_path: handler_after,
        }
        expected_integrated = dict(mapping.sibling_integrated.files)
        for relative, expected in expected_integrated.items():
            if relative in generated:
                content = generated[relative].encode("utf-8")
            else:
                content = _read_revision_file(sibling, baseline, relative).encode("utf-8")
            if _fingerprint(content) != expected:
                raise CheckpointGenerationError(
                    f"generated sibling content differs from integrated anchor {relative}"
                )
        return (
            _diff_file(init_path, init_before, init_after)
            + _diff_file(include_path, include_before, include_after)
            + _diff_file(handler_path, handler_before, handler_after)
        )

    init_before = _read_revision_file(sibling, mapping.sibling_patch_base, init_path)
    makefile_before = _read_revision_file(
        sibling, mapping.sibling_patch_base, makefile_path
    )
    init_after = (
        _modify_init_swapper(init_before)
        if mapping.root_object == "SwapperPageTable"
        else _modify_init(init_before)
    )
    makefile_after = _modify_makefile(makefile_before)
    include_path = "arch/riscv/mm/lkm2_checkpoints.inc"
    handler_path = "arch/riscv/mm/lkm2_checkpoint_handler.c"
    generated = {
        makefile_path: makefile_after,
        init_path: init_after,
        include_path: render_linux_include(checkpoints, mapping),
        handler_path: render_linux_handler(checkpoints),
    }
    if set(generated) != {path for path, _digest in mapping.sibling_integrated.files}:
        raise CheckpointGenerationError(
            "integrated sibling fingerprints do not cover exactly the generated patch paths"
        )
    for relative, expected in mapping.sibling_integrated.files:
        if _fingerprint(generated[relative].encode("utf-8")) != expected:
            raise CheckpointGenerationError(
                f"generated sibling content differs from integrated anchor {relative}"
            )
    return (
        _diff_file(makefile_path, makefile_before, makefile_after)
        + _diff_file(init_path, init_before, init_after)
        + _diff_new_file(include_path, generated[include_path])
        + _diff_new_file(handler_path, generated[handler_path])
    )
