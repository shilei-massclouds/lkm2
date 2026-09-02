//! Minimal physical-memory zone and free-area layout.
//!
//! This module deliberately stops at the data assembled by Linux's
//! `zone_sizes_init()`/`free_area_init()`.  It records page-aligned envelopes,
//! the usable portions left after MemBlock reservations, and one owner-bound
//! FreeArea per zone.  Buddy orders, bitmaps, lists, and page objects belong
//! to a later allocator milestone and are not represented here.

#![allow(dead_code)]

use super::memblock::MemBlock;

pub(crate) const PAGE_SHIFT: u32 = 12;
pub(crate) const PAGE_SIZE: u64 = 1_u64 << PAGE_SHIFT;
const DMA32_LIMIT: u64 = 1_u64 << 32;
// At most MAX_MEMORY_REGIONS + MAX_RESERVED_REGIONS disjoint fragments can
// survive reservation subtraction in the current MemBlock implementation.
const MAX_ZONE_RANGES: usize = 96;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LayoutError {
    MissingMemory,
    InvalidRange,
    AddressOverflow,
    RangeCapacityExceeded,
    AlreadyOnline,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct PhysicalRange {
    start: u64,
    end: u64,
}

impl PhysicalRange {
    pub(crate) const fn new(start: u64, end: u64) -> Result<Self, LayoutError> {
        if start >= end {
            return Err(LayoutError::InvalidRange);
        }
        Ok(Self { start, end })
    }

    pub(crate) const fn empty(at: u64) -> Self {
        Self { start: at, end: at }
    }

    pub(crate) const fn start(self) -> u64 {
        self.start
    }

    pub(crate) const fn end(self) -> u64 {
        self.end
    }

    pub(crate) const fn is_empty(self) -> bool {
        self.start >= self.end
    }

    pub(crate) const fn page_start(self) -> u64 {
        self.start >> PAGE_SHIFT
    }

    pub(crate) const fn page_end(self) -> u64 {
        self.end >> PAGE_SHIFT
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct RangeList {
    ranges: [PhysicalRange; MAX_ZONE_RANGES],
    len: usize,
}

impl RangeList {
    const fn empty() -> Self {
        Self {
            ranges: [PhysicalRange::empty(0); MAX_ZONE_RANGES],
            len: 0,
        }
    }

    fn push(&mut self, range: PhysicalRange) -> Result<(), LayoutError> {
        if range.is_empty() {
            return Ok(());
        }
        if self.len == MAX_ZONE_RANGES {
            return Err(LayoutError::RangeCapacityExceeded);
        }
        self.ranges[self.len] = range;
        self.len += 1;
        Ok(())
    }

    const fn len(self) -> usize {
        self.len
    }

    fn get(self, index: usize) -> Option<PhysicalRange> {
        self.ranges
            .get(index)
            .copied()
            .filter(|range| !range.is_empty())
    }

    fn iter(self) -> RangeListIter {
        RangeListIter {
            list: self,
            next: 0,
        }
    }

    fn contains_overlap(self, range: PhysicalRange) -> bool {
        self.ranges[..self.len]
            .iter()
            .any(|item| item.start < range.end && range.start < item.end)
    }
}

#[derive(Clone, Copy)]
struct RangeListIter {
    list: RangeList,
    next: usize,
}

impl Iterator for RangeListIter {
    type Item = PhysicalRange;

    fn next(&mut self) -> Option<Self::Item> {
        let result = self.list.get(self.next);
        if result.is_some() {
            self.next += 1;
        }
        result
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ZoneKind {
    DMA32,
    Normal,
    Movable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct FreeArea {
    owner: ZoneKind,
    envelope: PhysicalRange,
    managed_range_count: usize,
    managed_pages: u64,
    initialized: bool,
}

impl FreeArea {
    fn initialize(owner: ZoneKind, envelope: PhysicalRange, managed: RangeList) -> Self {
        let mut managed_pages = 0_u64;
        for range in managed.iter() {
            managed_pages = managed_pages.saturating_add((range.end - range.start) >> PAGE_SHIFT);
        }
        Self {
            owner,
            envelope,
            managed_range_count: managed.len(),
            managed_pages,
            initialized: true,
        }
    }

    pub(crate) const fn owner(self) -> ZoneKind {
        self.owner
    }

    pub(crate) const fn envelope(self) -> (u64, u64) {
        (self.envelope.start, self.envelope.end)
    }

    pub(crate) const fn managed_range_count(self) -> usize {
        self.managed_range_count
    }

    pub(crate) const fn managed_pages(self) -> u64 {
        self.managed_pages
    }

    pub(crate) const fn is_initialized(self) -> bool {
        self.initialized
    }

    pub(crate) const fn is_online(self) -> bool {
        self.initialized
    }

    pub(crate) const fn is_empty(self) -> bool {
        self.managed_range_count == 0
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Zone {
    kind: ZoneKind,
    envelope: PhysicalRange,
    managed: RangeList,
    free_area: FreeArea,
}

impl Zone {
    fn initialize(
        kind: ZoneKind,
        envelope: PhysicalRange,
        memblock: &MemBlock,
    ) -> Result<Self, LayoutError> {
        let managed = managed_ranges(envelope, memblock)?;
        let free_area = FreeArea::initialize(kind, envelope, managed);
        Ok(Self {
            kind,
            envelope,
            managed,
            free_area,
        })
    }

    pub(crate) const fn kind(self) -> ZoneKind {
        self.kind
    }

    pub(crate) const fn envelope(self) -> (u64, u64) {
        (self.envelope.start, self.envelope.end)
    }

    pub(crate) const fn page_range(self) -> (u64, u64) {
        (self.envelope.page_start(), self.envelope.page_end())
    }

    /// A zone is populated only when at least one non-reserved memory range
    /// remains.  Empty envelopes and fully reserved envelopes are both
    /// omitted from the fallback projection.
    pub(crate) const fn is_empty(self) -> bool {
        self.envelope.is_empty() || self.managed.len() == 0
    }

    pub(crate) const fn managed_range_count(self) -> usize {
        self.managed.len()
    }

    pub(crate) const fn managed_pages(self) -> u64 {
        self.free_area.managed_pages
    }

    pub(crate) fn managed_ranges(self) -> ManagedRangeIter {
        ManagedRangeIter {
            ranges: self.managed.iter(),
        }
    }

    pub(crate) const fn free_area(self) -> FreeArea {
        self.free_area
    }

    pub(crate) const fn free_area_initialized(self) -> bool {
        self.free_area.initialized
    }

    pub(crate) const fn is_online(self) -> bool {
        self.free_area.initialized
    }
}

#[derive(Clone, Copy)]
pub(crate) struct ManagedRangeIter {
    ranges: RangeListIter,
}

impl Iterator for ManagedRangeIter {
    type Item = (u64, u64);

    fn next(&mut self) -> Option<Self::Item> {
        self.ranges.next().map(|range| (range.start, range.end))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ZoneSet {
    dma32: Zone,
    normal: Zone,
    movable: Zone,
}

impl ZoneSet {
    /// Mirror RISC-V's `zone_sizes_init()`: DMA32 ends at the lower of the
    /// node's highest page and the 32-bit DMA limit, while Normal covers the
    /// remaining directly addressable pages.  Movable has no independent
    /// boundary in this early skeleton and is intentionally empty.
    pub(crate) fn initialize(
        node_start: u64,
        node_end: u64,
        memblock: &MemBlock,
    ) -> Result<Self, LayoutError> {
        if node_start >= node_end {
            return Err(LayoutError::InvalidRange);
        }
        let start = align_up(node_start)?;
        let end = align_down(node_end);
        if start >= end {
            return Err(LayoutError::InvalidRange);
        }
        let dma_end = align_down(core::cmp::min(end, DMA32_LIMIT));
        let dma_envelope = if start < dma_end {
            PhysicalRange::new(start, dma_end)?
        } else {
            PhysicalRange::empty(start)
        };
        let normal_start = core::cmp::max(start, dma_end);
        let normal_envelope = if normal_start < end {
            PhysicalRange::new(normal_start, end)?
        } else {
            PhysicalRange::empty(normal_start)
        };
        let movable_envelope = PhysicalRange::empty(end);
        let dma32 = Zone::initialize(ZoneKind::DMA32, dma_envelope, memblock)?;
        let normal = Zone::initialize(ZoneKind::Normal, normal_envelope, memblock)?;
        let movable = Zone::initialize(ZoneKind::Movable, movable_envelope, memblock)?;
        let zones = Self {
            dma32,
            normal,
            movable,
        };
        zones.validate_disjoint()?;
        Ok(zones)
    }

    pub(crate) fn new(
        node_start: u64,
        node_end: u64,
        memblock: &MemBlock,
    ) -> Result<Self, LayoutError> {
        Self::initialize(node_start, node_end, memblock)
    }

    pub(crate) const fn dma32(self) -> Zone {
        self.dma32
    }

    pub(crate) const fn normal(self) -> Zone {
        self.normal
    }

    pub(crate) const fn movable(self) -> Zone {
        self.movable
    }

    pub(crate) fn iter(self) -> ZoneSetIter {
        ZoneSetIter {
            zones: [self.dma32, self.normal, self.movable],
            next: 0,
        }
    }

    pub(crate) fn validate_disjoint(self) -> Result<(), LayoutError> {
        let zones = [self.dma32, self.normal, self.movable];
        for (index, left) in zones.iter().enumerate() {
            for right in zones.iter().skip(index + 1) {
                if left.envelope.start < right.envelope.end
                    && right.envelope.start < left.envelope.end
                {
                    return Err(LayoutError::InvalidRange);
                }
                if left.managed.contains_overlap(right.envelope)
                    || right.managed.contains_overlap(left.envelope)
                {
                    return Err(LayoutError::InvalidRange);
                }
            }
        }
        Ok(())
    }

    pub(crate) fn validate(self) -> Result<(), LayoutError> {
        self.validate_disjoint()
    }
}

#[derive(Clone, Copy)]
pub(crate) struct ZoneSetIter {
    zones: [Zone; 3],
    next: usize,
}

impl Iterator for ZoneSetIter {
    type Item = Zone;

    fn next(&mut self) -> Option<Self::Item> {
        let result = self.zones.get(self.next).copied();
        if result.is_some() {
            self.next += 1;
        }
        result
    }
}

fn align_down(value: u64) -> u64 {
    value & !(PAGE_SIZE - 1)
}

fn align_up(value: u64) -> Result<u64, LayoutError> {
    value
        .checked_add(PAGE_SIZE - 1)
        .map(align_down)
        .ok_or(LayoutError::AddressOverflow)
}

fn managed_ranges(envelope: PhysicalRange, memblock: &MemBlock) -> Result<RangeList, LayoutError> {
    let mut result = RangeList::empty();
    if envelope.is_empty() {
        return Ok(result);
    }
    for (memory_start, memory_end) in memblock.memory_ranges() {
        let start = align_up(core::cmp::max(memory_start, envelope.start))?;
        let end = align_down(core::cmp::min(memory_end, envelope.end));
        if start >= end {
            continue;
        }

        // Subtract each normalized reservation from this memory fragment.
        // The temporary list is bounded and no allocation is needed during
        // the boot path.
        let mut fragments = RangeList::empty();
        fragments.push(PhysicalRange::new(start, end)?)?;
        for (raw_reserved_start, raw_reserved_end) in memblock.reserved_ranges() {
            // Avoid rounding an unrelated high physical reservation before
            // checking whether it intersects this zone.  This also keeps a
            // reservation ending at u64::MAX from turning an otherwise valid
            // low-memory layout into an overflow error.
            if raw_reserved_end <= start || end <= raw_reserved_start {
                continue;
            }
            let reserved_start = align_down(raw_reserved_start);
            let reserved_end = align_up_clamped(raw_reserved_end);
            let mut next = RangeList::empty();
            for fragment in fragments.iter() {
                if fragment.end <= reserved_start || reserved_end <= fragment.start {
                    next.push(fragment)?;
                    continue;
                }
                if fragment.start < reserved_start {
                    next.push(PhysicalRange::new(fragment.start, reserved_start)?)?;
                }
                if reserved_end < fragment.end {
                    next.push(PhysicalRange::new(reserved_end, fragment.end)?)?;
                }
            }
            fragments = next;
            if fragments.len() == 0 {
                break;
            }
        }
        for fragment in fragments.iter() {
            result.push(fragment)?;
        }
    }
    Ok(result)
}

fn align_up_clamped(value: u64) -> u64 {
    value
        .checked_add(PAGE_SIZE - 1)
        .map(align_down)
        .unwrap_or(u64::MAX)
}
