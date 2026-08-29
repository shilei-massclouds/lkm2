"""Shared swapper page-table content protocol helpers.

The boot implementations use the same byte protocol as these small, pure
Python helpers.  They are intentionally independent of Model IR: this module
describes the mapping ABI, not a model invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence

PAGE_SIZE = 4096
PMD_SIZE = 1 << 21
PUD_SIZE = 1 << 30
FNV_PRIME = 0x100000001B3
FNV_OFFSET_LO = 0xCBF29CE484222325
FNV_OFFSET_HI = 0x84222325CBF29CE4
MASK64 = (1 << 64) - 1
PTE_VALID = 1 << 0
PTE_READ = 1 << 1
PTE_WRITE = 1 << 2
PTE_EXEC = 1 << 3
PTE_USER = 1 << 4
PTE_GLOBAL = 1 << 5
PTE_ACCESSED = 1 << 6
PTE_DIRTY = 1 << 7
PTE_PERMISSION_MASK = PTE_READ | PTE_WRITE | PTE_EXEC | PTE_USER | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY
CLASS_IDS = {"fixmap": 1, "linear": 2, "kernel": 3}


class PageTableWalkError(ValueError):
    """A malformed page-table path or unsupported paging mode."""


@dataclass(frozen=True, slots=True)
class Leaf:
    va: int
    pa: int
    flags: int
    size: int


def _entry_value(entry: object) -> int:
    if isinstance(entry, int):
        return entry
    if isinstance(entry, Mapping):
        value = entry.get("value", entry.get("pte"))
        if isinstance(value, int):
            return value
    value = getattr(entry, "value", getattr(entry, "pte", None))
    if isinstance(value, int):
        return value
    raise PageTableWalkError("page-table entry has no integer value")


def _children(entry: object) -> object | None:
    if isinstance(entry, Mapping):
        return entry.get("table", entry.get("children"))
    return getattr(entry, "table", getattr(entry, "children", None))


def _read(node: object, index: int) -> object:
    if isinstance(node, Mapping):
        return node.get(index, 0)
    try:
        return node[index]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        return 0


def _is_leaf(flags: int) -> bool:
    return bool(
        flags & PTE_VALID
        and flags & (PTE_READ | PTE_WRITE | PTE_EXEC)
        and not (flags & PTE_WRITE and not flags & PTE_READ)
    )


def _canonical(va: int, bits: int) -> bool:
    if va < 0 or va > MASK64:
        return False
    sign = 1 << (bits - 1)
    upper = MASK64 ^ ((1 << bits) - 1)
    return (va & upper) == (upper if va & sign else 0)


def walk_page_table(root: object, mode: str | int, va: int) -> Leaf | None:
    """Walk a Sv39/Sv48/Sv57 tree and return the leaf covering ``va``.

    ``root`` may be a 512-entry sequence/mapping.  A non-leaf entry can carry
    its child in ``table``/``children``; this representation keeps tests and
    diagnostics independent from implementation-specific backing addresses.
    """

    mode_name = {8: "sv39", 9: "sv48", 10: "sv57"}.get(mode, mode)
    if mode_name not in {"sv39", "sv48", "sv57"}:
        raise PageTableWalkError("unsupported paging mode")
    bits = {"sv39": 39, "sv48": 48, "sv57": 57}[mode_name]
    if not _canonical(va, bits):
        return None
    shifts = {"sv39": (30, 21, 12), "sv48": (39, 30, 21, 12), "sv57": (48, 39, 30, 21, 12)}[mode_name]
    node = root
    for level, shift in enumerate(shifts):
        raw = _read(node, (va >> shift) & 0x1FF)
        value = _entry_value(raw)
        flags = value & 0x3FF
        if value == 0:
            return None
        if _is_leaf(flags):
            size = 1 << shift
            pa = ((value >> 10) << 12) & MASK64
            return Leaf(va & ~(size - 1), pa, flags, size)
        if flags != PTE_VALID:
            raise PageTableWalkError("invalid non-leaf page-table entry")
        child = _children(raw)
        if child is None:
            raise PageTableWalkError("non-leaf entry has no child table")
        node = child
    raise PageTableWalkError("page-table walk ended without a leaf")


def expand_leaf(leaf: Leaf) -> Iterator[tuple[int, int, int]]:
    """Expand a 1 GiB/2 MiB/4 KiB leaf into 4 KiB semantic entries."""

    if leaf.size not in (PUD_SIZE, PMD_SIZE, PAGE_SIZE):
        raise PageTableWalkError("unsupported leaf size")
    if leaf.va % leaf.size or leaf.pa % leaf.size:
        raise PageTableWalkError("leaf is not aligned to its level")
    for offset in range(0, leaf.size, PAGE_SIZE):
        yield leaf.va + offset, leaf.pa + offset, leaf.flags


def normalized_flags(class_name: str, flags: int) -> int:
    if class_name == "fixmap":
        return flags & (PTE_VALID | PTE_PERMISSION_MASK)
    if class_name == "linear":
        return flags & (PTE_VALID | PTE_READ)
    raise PageTableWalkError(f"unknown content class {class_name!r}")


def _fnv_byte(state: int, byte: int) -> int:
    return ((state ^ byte) * FNV_PRIME) & MASK64


def _fnv_header(class_name: str) -> tuple[int, int]:
    try:
        class_id = CLASS_IDS[class_name]
    except KeyError as exc:
        raise PageTableWalkError(f"unknown content class {class_name!r}") from exc
    lo, hi = FNV_OFFSET_LO, FNV_OFFSET_HI
    for byte in b"LKMPTE1" + bytes((1, class_id)):
        lo, hi = _fnv_byte(lo, byte), _fnv_byte(hi, byte)
    return lo, hi


def content_digest(class_name: str, entries: Iterable[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Return ``(count, digest_lo, digest_hi)`` for semantic 4 KiB entries."""

    lo, hi = _fnv_header(class_name)
    count = 0
    for va, pa, flags in entries:
        for value in (va, pa, normalized_flags(class_name, flags)):
            for byte in int(value & MASK64).to_bytes(8, "little"):
                lo, hi = _fnv_byte(lo, byte), _fnv_byte(hi, byte)
        count += 1
    for byte in count.to_bytes(8, "little"):
        lo, hi = _fnv_byte(lo, byte), _fnv_byte(hi, byte)
    return count, lo, hi


def content_digest_from_leaves(class_name: str, leaves: Iterable[Leaf]) -> tuple[int, int, int]:
    entries: list[tuple[int, int, int]] = []
    for leaf in leaves:
        entries.extend(expand_leaf(leaf))
    entries.sort(key=lambda item: item[0])
    return content_digest(class_name, entries)


def kernel_walk_valid(
    entries: Sequence[tuple[int, int, int]],
    kernel_va: int,
    kernel_pa: int,
    image_size: int,
) -> bool:
    """Check one implementation's kernel image walk without hashing its size."""
    if image_size <= 0 or kernel_va + image_size > 1 << 64:
        return False
    expected_count = (image_size + PAGE_SIZE - 1) // PAGE_SIZE
    if len(entries) < expected_count:
        return False
    for index in range(expected_count):
        va, pa, flags = entries[index]
        if va != kernel_va + index * PAGE_SIZE or pa != kernel_pa + index * PAGE_SIZE:
            return False
        if not _is_leaf(flags) or flags & PTE_USER or flags & PTE_WRITE and flags & PTE_EXEC:
            return False
    return True


def first_chunk_mismatch(left: Sequence[tuple[int, int, int]], right: Sequence[tuple[int, int, int]], chunk_pages: int = 512) -> tuple[int, int] | None:
    """Return ``(chunk_index, first_index)`` for the first semantic mismatch."""

    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index // chunk_pages, index
    if len(left) != len(right):
        index = min(len(left), len(right))
        return index // chunk_pages, index
    return None


def chunk_digests(
    class_name: str,
    entries: Sequence[tuple[int, int, int]],
    chunk_pages: int = 512,
) -> tuple[tuple[int, int, int, int], ...]:
    """Compute diagnostic digests for 2 MiB (512-page) chunks."""
    if chunk_pages <= 0:
        raise ValueError("chunk_pages must be positive")
    result = []
    for chunk in range(0, len(entries), chunk_pages):
        count, lo, hi = content_digest(class_name, entries[chunk : chunk + chunk_pages])
        result.append((chunk // chunk_pages, count, lo, hi))
    return tuple(result)


def format_chunk_record(class_name: str, chunk: int, count: int, lo: int, hi: int) -> str:
    return (
        f"LKMPTC1 class={class_name} chunk=0x{chunk:016x} "
        f"count=0x{count:016x} digest_lo=0x{lo:016x} digest_hi=0x{hi:016x}"
    )


def format_item_record(class_name: str, index: int, va: int, pa: int, flags: int) -> str:
    return (
        f"LKMPTI1 class={class_name} index=0x{index:016x} "
        f"va=0x{va:016x} pa=0x{pa:016x} flags=0x{normalized_flags(class_name, flags):016x}"
    )
