from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools" / "src"))

from checkpointgen.content import (  # noqa: E402
    Leaf,
    PAGE_SIZE,
    PMD_SIZE,
    PUD_SIZE,
    PTE_ACCESSED,
    PTE_DIRTY,
    PTE_EXEC,
    PTE_GLOBAL,
    PTE_READ,
    PTE_VALID,
    PTE_WRITE,
    PageTableWalkError,
    chunk_digests,
    content_digest,
    content_digest_from_leaves,
    expand_leaf,
    first_chunk_mismatch,
    format_chunk_record,
    format_item_record,
    kernel_walk_valid,
    walk_page_table,
)
from checkpointgen.generator import (  # noqa: E402
    CheckpointGenerationError,
    build_checkpoints,
    load_mapping,
    render_manifest,
)
from checkpointgen.runner import (  # noqa: E402
    CollectedOutput,
    CheckpointRunError,
    _diagnose_content_mismatch,
    _validate_manifest_identities,
    parse_content_chunk_record,
    parse_content_item_record,
    validate_records,
)


LEAF_FLAGS = PTE_VALID | PTE_READ | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY


def pte(pa: int, flags: int) -> int:
    return (pa >> 12) << 10 | flags


def tree(mode: str, va: int, leaf_pa: int, leaf_shift: int, backing: int = 0x1000):
    shifts = {
        "sv39": (30, 21, 12),
        "sv48": (39, 30, 21, 12),
        "sv57": (48, 39, 30, 21, 12),
    }[mode]
    root: dict[int, object] = {}
    node = root
    for depth, shift in enumerate(shifts):
        index = (va >> shift) & 0x1FF
        if shift == leaf_shift:
            node[index] = pte(leaf_pa, LEAF_FLAGS)
            break
        child: dict[int, object] = {}
        node[index] = {"value": pte(backing + depth * PAGE_SIZE, PTE_VALID), "table": child}
        node = child
    return root


class SwapperContentTests(unittest.TestCase):
    def test_walker_supports_all_modes_and_leaf_sizes(self) -> None:
        for mode in ("sv39", "sv48", "sv57"):
            for shift, size in ((30, PUD_SIZE), (21, PMD_SIZE), (12, PAGE_SIZE)):
                va = (1 << 64) - (1 << 30) if shift == 30 else (1 << 64) - size
                leaf = walk_page_table(tree(mode, va, 0x80000000, shift), mode, va)
                self.assertIsNotNone(leaf)
                assert leaf is not None
                self.assertEqual((leaf.pa, leaf.size), (0x80000000, size))

    def test_walker_rejects_missing_and_illegal_paths(self) -> None:
        self.assertIsNone(walk_page_table({}, "sv57", 0))
        self.assertIsNone(walk_page_table({}, "sv57", 1 << 64))
        with self.assertRaisesRegex(PageTableWalkError, "invalid non-leaf"):
            walk_page_table({0: {"value": pte(0x1000, PTE_VALID | PTE_GLOBAL), "table": {}}}, "sv39", 0)
        with self.assertRaisesRegex(PageTableWalkError, "no child"):
            walk_page_table({0: pte(0x1000, PTE_VALID)}, "sv39", 0)
        with self.assertRaisesRegex(PageTableWalkError, "unsupported"):
            walk_page_table({}, "sv32", 0)

    def test_backing_address_does_not_change_semantic_digest(self) -> None:
        va = (1 << 64) - PMD_SIZE
        left = walk_page_table(tree("sv57", va, 0x80000000, 21, 0x1000), "sv57", va)
        right = walk_page_table(tree("sv57", va, 0x80000000, 21, 0x900000), "sv57", va)
        assert left is not None and right is not None
        self.assertEqual(content_digest_from_leaves("fixmap", (left,)), content_digest_from_leaves("fixmap", (right,)))

    def test_huge_leaf_and_4k_leaves_hash_identically(self) -> None:
        huge = Leaf(0, 0x80000000, LEAF_FLAGS, PMD_SIZE)
        leaves = tuple(
            Leaf(offset, 0x80000000 + offset, LEAF_FLAGS, PAGE_SIZE)
            for offset in range(0, PMD_SIZE, PAGE_SIZE)
        )
        self.assertEqual(content_digest_from_leaves("fixmap", (huge,)), content_digest_from_leaves("fixmap", leaves))

    def test_digest_fixed_vector_and_projection(self) -> None:
        entries = ((0x1000, 0x80001000, LEAF_FLAGS), (0x2000, 0x80002000, LEAF_FLAGS))
        self.assertEqual(content_digest("fixmap", entries), (2, 0x90F6363C3496CC29, 0x393AA3DE623426D2))
        private = tuple((va, pa, flags | PTE_WRITE | PTE_EXEC) for va, pa, flags in entries)
        self.assertEqual(content_digest("linear", entries), content_digest("linear", private))
        changed_pa = ((0x1000, 0x80003000, LEAF_FLAGS), entries[1])
        self.assertNotEqual(content_digest("linear", entries), content_digest("linear", changed_pa))
        changed_flags = ((entries[0][0], entries[0][1], LEAF_FLAGS & ~PTE_ACCESSED), entries[1])
        self.assertNotEqual(content_digest("fixmap", entries), content_digest("fixmap", changed_flags))

    def test_kernel_validation_excludes_image_size_from_cross_digest(self) -> None:
        pages = tuple((0xFFFF0000 + i * PAGE_SIZE, 0x80200000 + i * PAGE_SIZE, PTE_VALID | PTE_READ) for i in range(3))
        self.assertTrue(kernel_walk_valid(pages[:1], pages[0][0], pages[0][1], PAGE_SIZE))
        self.assertTrue(kernel_walk_valid(pages, pages[0][0], pages[0][1], 3 * PAGE_SIZE))
        self.assertFalse(kernel_walk_valid(pages[1:], pages[0][0], pages[0][1], PAGE_SIZE))
        wrong_pa = ((pages[0][0], pages[0][1] + PAGE_SIZE, pages[0][2]),)
        self.assertFalse(kernel_walk_valid(wrong_pa, pages[0][0], pages[0][1], PAGE_SIZE))
        bad = ((pages[0][0], pages[0][1], PTE_VALID | PTE_READ | PTE_WRITE | PTE_EXEC),)
        self.assertFalse(kernel_walk_valid(bad, pages[0][0], pages[0][1], PAGE_SIZE))
        write_only = ((pages[0][0], pages[0][1], PTE_VALID | PTE_WRITE),)
        self.assertFalse(kernel_walk_valid(write_only, pages[0][0], pages[0][1], PAGE_SIZE))
        self.assertFalse(kernel_walk_valid(pages[:1], (1 << 64) - PAGE_SIZE, pages[0][1], 2 * PAGE_SIZE))

    def test_chunk_and_item_diagnostics(self) -> None:
        entries = tuple((i * PAGE_SIZE, 0x80000000 + i * PAGE_SIZE, LEAF_FLAGS) for i in range(513))
        self.assertEqual(len(chunk_digests("linear", entries)), 2)
        changed = (*entries[:512], (entries[512][0], entries[512][1] + PAGE_SIZE, entries[512][2]))
        self.assertEqual(first_chunk_mismatch(entries, changed), (1, 512))
        chunk = format_chunk_record("linear", *chunk_digests("linear", entries)[0])
        self.assertEqual(parse_content_chunk_record(chunk)[0:3], ("linear", 0, 512))
        item = format_item_record("linear", 512, *entries[512])
        self.assertEqual(parse_content_item_record(item)[0:2], ("linear", 512))

    def test_runner_performs_two_bounded_diagnostic_passes(self) -> None:
        same = format_chunk_record("linear", 0, 512, 1, 2)
        left_chunk = format_chunk_record("linear", 1, 1, 3, 4)
        right_chunk = format_chunk_record("linear", 1, 1, 5, 6)
        left_item = format_item_record("linear", 512, 0x1000, 0x2000, LEAF_FLAGS)
        right_item = format_item_record("linear", 512, 0x1000, 0x3000, LEAF_FLAGS)
        outputs = (
            CollectedOutput((), (), (same, left_chunk)),
            CollectedOutput((), (), (same, right_chunk)),
            CollectedOutput((), (), (left_item,)),
            CollectedOutput((), (), (right_item,)),
        )
        with mock.patch("checkpointgen.runner._collect_qemu", side_effect=outputs) as collect:
            message = _diagnose_content_mismatch(
                ["qemu", "-append", "earlycon=sbi"],
                Path("lkm"),
                Path("linux"),
                53,
                1,
                "linear",
            )
        self.assertEqual(collect.call_count, 4)
        self.assertIn("chunk=1", message)
        self.assertIn("first_index=512", message)
        self.assertIn("lkm2 chunk", message)
        self.assertIn("linux chunk", message)

    def test_runner_rejects_incomplete_item_diagnostics(self) -> None:
        left_chunk = format_chunk_record("linear", 0, 1, 1, 2)
        right_chunk = format_chunk_record("linear", 0, 1, 3, 4)
        right_item = format_item_record("linear", 0, 0x1000, 0x3000, LEAF_FLAGS)
        outputs = (
            CollectedOutput((), (), (left_chunk,)),
            CollectedOutput((), (), (right_chunk,)),
            CollectedOutput((), (), ()),
            CollectedOutput((), (), (right_item,)),
        )
        with mock.patch("checkpointgen.runner._collect_qemu", side_effect=outputs):
            with self.assertRaisesRegex(CheckpointRunError, "incomplete lkm2"):
                _diagnose_content_mismatch(
                    ["qemu", "-append", "earlycon=sbi"],
                    Path("lkm"),
                    Path("linux"),
                    53,
                    1,
                    "linear",
                )

    def test_implementation_only_schema_and_valid_gate(self) -> None:
        mapping_path = REPOSITORY / "tools" / "checkpoints" / "swapper-content.json"
        with mapping_path.open(encoding="utf-8") as stream:
            mapping = load_mapping(stream)
        checkpoints = build_checkpoints(None, mapping)
        self.assertTrue(mapping.implementation_only)
        self.assertEqual(
            mapping.sibling_integrated.commit,
            "e5668acadb200fd194c988329288810338eba963",
        )
        self.assertEqual(len(checkpoints), 3)
        manifest = tuple(json.loads(render_manifest(checkpoints))["checkpoints"])
        lines = tuple(
            f"LKMCP1 id={item.canonical_id} hash={item.hash16} "
            + " ".join(f"{name}=0x{(0 if name == 'valid' else 1):016x}" for name in item.parameters)
            for item in checkpoints
        )
        with self.assertRaisesRegex(CheckpointRunError, "valid=0"):
            validate_records(lines, manifest, "sv57")

    def test_implementation_only_schema_is_fixed(self) -> None:
        raw = json.loads((REPOSITORY / "tools" / "checkpoints" / "swapper-content.json").read_text())
        raw["checkpoints"][0]["parameters"] = ["count", "valid", "digest_lo", "digest_hi"]
        from io import StringIO
        with self.assertRaisesRegex(CheckpointGenerationError, "fixed IDs"):
            load_mapping(StringIO(json.dumps(raw)))

    def test_runner_rejects_cross_suite_id_and_hash_collisions(self) -> None:
        one = {"id": "one", "hash": "1111111111111111"}
        duplicate_id = {"id": "one", "hash": "2222222222222222"}
        duplicate_hash = {"id": "two", "hash": "1111111111111111"}
        with self.assertRaisesRegex(CheckpointRunError, "duplicate canonical ID"):
            _validate_manifest_identities(((one,), (duplicate_id,)))
        with self.assertRaisesRegex(CheckpointRunError, "hash collision"):
            _validate_manifest_identities(((one,), (duplicate_hash,)))


if __name__ == "__main__":
    unittest.main()
