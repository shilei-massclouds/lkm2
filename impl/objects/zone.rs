//! Physical-memory zone and free-area layout plus the order-based buddy handoff.
//!
//! The first part mirrors Linux's `zone_sizes_init()`/`free_area_init()` and
//! keeps page-aligned envelopes, usable MemBlock fragments, and one
//! owner-bound FreeArea per zone alive after initialization.  The FreeArea
//! then carries the small buddy state used by the coding-layer allocator. It
//! intentionally does not model Linux's migration lists, watermarks, reclaim,
//! or pageblock bitmap details.

#![allow(dead_code)]

use super::memblock::MemBlock;

pub(crate) const PAGE_SHIFT: u32 = 12;
pub(crate) const PAGE_SIZE: u64 = 1_u64 << PAGE_SHIFT;
const DMA32_LIMIT: u64 = 1_u64 << 32;
// At most MAX_MEMORY_REGIONS + MAX_RESERVED_REGIONS disjoint fragments can
// survive reservation subtraction in the current MemBlock implementation.
const MAX_ZONE_RANGES: usize = 96;
// The backing arrays are metadata storage owned by the FreeArea itself.  The
// no_std boot image uses a small emergency descriptor budget so the 16 KiB
// boot stack is not consumed by a copied node value; host tests use the larger
// budget to exercise fragmentation.  In both cases the physical storage they
// describe is obtained from MemBlock at handoff.
#[cfg(test)]
const MAX_BUDDY_BLOCKS: usize = 256;
#[cfg(not(test))]
const MAX_BUDDY_BLOCKS: usize = 32;
#[cfg(test)]
const MAX_ALLOCATED_BLOCKS: usize = 128;
#[cfg(not(test))]
const MAX_ALLOCATED_BLOCKS: usize = 16;
#[cfg(test)]
const MAX_RELEASED_BLOCKS: usize = 128;
#[cfg(not(test))]
const MAX_RELEASED_BLOCKS: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LayoutError {
    MissingMemory,
    InvalidRange,
    AddressOverflow,
    RangeCapacityExceeded,
    AlreadyOnline,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct BuddyBlock {
    start_pfn: u64,
    order: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct AllocatedBlock {
    start_pfn: u64,
    order: u8,
    zone: ZoneKind,
}

impl AllocatedBlock {
    pub(crate) const fn new(start_pfn: u64, order: u8, zone: ZoneKind) -> Self {
        Self {
            start_pfn,
            order,
            zone,
        }
    }

    pub(crate) const fn start_pfn(self) -> u64 {
        self.start_pfn
    }

    pub(crate) const fn physical_start(self) -> u64 {
        self.start_pfn << PAGE_SHIFT
    }

    pub(crate) const fn order(self) -> u8 {
        self.order
    }

    pub(crate) const fn zone(self) -> ZoneKind {
        self.zone
    }

    pub(crate) const fn page_count(self) -> u64 {
        1_u64 << self.order
    }

    pub(crate) const fn physical_range(self) -> (u64, u64) {
        let start = self.physical_start();
        let size = if self.order > 51 {
            u64::MAX
        } else {
            self.page_count() << PAGE_SHIFT
        };
        let end = match start.checked_add(size) {
            Some(end) => end,
            None => u64::MAX,
        };
        (start, end)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BuddyAllocError {
    InvalidOrder,
    OutOfMemory,
    Capacity,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BuddyFreeError {
    InvalidOrder,
    Unaligned,
    OutOfBounds,
    WrongZone,
    DoubleFree,
    NotAllocated,
    Capacity,
}

pub(crate) type AllocError = BuddyAllocError;
pub(crate) type FreeError = BuddyFreeError;

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
    managed: RangeList,
    managed_range_count: usize,
    managed_pages: u64,
    initialized: bool,
    free_blocks: [BuddyBlock; MAX_BUDDY_BLOCKS],
    free_len: usize,
    allocated: [BuddyBlock; MAX_ALLOCATED_BLOCKS],
    allocated_len: usize,
    released: [BuddyBlock; MAX_RELEASED_BLOCKS],
    released_len: usize,
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
            managed,
            managed_range_count: managed.len(),
            managed_pages,
            initialized: true,
            free_blocks: [BuddyBlock {
                start_pfn: 0,
                order: 0,
            }; MAX_BUDDY_BLOCKS],
            free_len: 0,
            allocated: [BuddyBlock {
                start_pfn: 0,
                order: 0,
            }; MAX_ALLOCATED_BLOCKS],
            allocated_len: 0,
            released: [BuddyBlock {
                start_pfn: 0,
                order: 0,
            }; MAX_RELEASED_BLOCKS],
            released_len: 0,
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

    /// Number of block-head records needed to seed this area from its current
    /// managed fragments.  This is used only to size the MemBlock metadata
    /// reservation; the records themselves are populated by `seed_buddy`.
    pub(crate) fn buddy_record_count(self) -> u64 {
        let mut count = 0_u64;
        for fragment in self.managed.iter() {
            let mut start = fragment.start >> PAGE_SHIFT;
            let end = fragment.end >> PAGE_SHIFT;
            while start < end {
                let remaining = end - start;
                let mut order = floor_log2(remaining);
                let alignment = start.trailing_zeros().min(63) as u8;
                if order > alignment {
                    order = alignment;
                }
                count = count.saturating_add(1);
                start = match start.checked_add(1_u64 << order) {
                    Some(next) => next,
                    None => break,
                };
            }
        }
        count
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

    pub(crate) const fn free_block_count(self, order: u8) -> usize {
        let mut count = 0;
        let mut index = 0;
        while index < self.free_len {
            if self.free_blocks[index].order == order {
                count += 1;
            }
            index += 1;
        }
        count
    }

    pub(crate) const fn free_block_total(self) -> usize {
        self.free_len
    }

    pub(crate) fn has_free_block(&self, start_pfn: u64, order: u8) -> bool {
        self.free_blocks[..self.free_len]
            .iter()
            .any(|block| block.start_pfn == start_pfn && block.order == order)
    }

    pub(crate) const fn allocated_block_count(self) -> usize {
        self.allocated_len
    }

    pub(crate) fn managed_contains(self, start_pfn: u64, end_pfn: u64) -> bool {
        let mut index = 0;
        while index < self.managed.len() {
            if let Some(range) = self.managed.get(index) {
                let start = range.start >> PAGE_SHIFT;
                let end = range.end >> PAGE_SHIFT;
                if start <= start_pfn && end_pfn <= end {
                    return true;
                }
            }
            index += 1;
        }
        false
    }

    /// Seed this FreeArea from the page-aligned managed fragments after the
    /// MemBlock handoff.  Each fragment is decomposed into the largest
    /// aligned power-of-two blocks, exactly the initial state expected by a
    /// buddy allocator.
    pub(crate) fn seed_buddy(&mut self) -> Result<(), BuddyAllocError> {
        self.free_len = 0;
        self.allocated_len = 0;
        self.released_len = 0;
        for fragment in self.managed.iter() {
            let mut start = fragment.start >> PAGE_SHIFT;
            let end = fragment.end >> PAGE_SHIFT;
            while start < end {
                let remaining = end - start;
                let mut order = floor_log2(remaining);
                let alignment = start.trailing_zeros().min(63) as u8;
                if order > alignment {
                    order = alignment;
                }
                self.insert_free(BuddyBlock {
                    start_pfn: start,
                    order,
                })?;
                start = start
                    .checked_add(1_u64 << order)
                    .ok_or(BuddyAllocError::Capacity)?;
            }
        }
        Ok(())
    }

    pub(crate) fn allocate(&mut self, order: u8) -> Result<AllocatedBlock, BuddyAllocError> {
        if order > 63 {
            return Err(BuddyAllocError::InvalidOrder);
        }
        let mut selected = None;
        let mut index = 0;
        while index < self.free_len {
            let block = self.free_blocks[index];
            if block.order >= order
                && selected
                    .map(|(selected_order, selected_start, _)| {
                        (block.order, block.start_pfn) < (selected_order, selected_start)
                    })
                    .unwrap_or(true)
            {
                selected = Some((block.order, block.start_pfn, index));
            }
            index += 1;
        }
        let Some((mut current_order, start_pfn, index)) = selected else {
            return Err(BuddyAllocError::OutOfMemory);
        };
        let split_count = usize::from(current_order - order);
        if self.allocated_len == MAX_ALLOCATED_BLOCKS
            || self.free_len.saturating_add(split_count) > MAX_BUDDY_BLOCKS
        {
            return Err(BuddyAllocError::Capacity);
        }
        self.remove_free_at(index);
        while current_order > order {
            current_order -= 1;
            let buddy_start = start_pfn
                .checked_add(1_u64 << current_order)
                .ok_or(BuddyAllocError::Capacity)?;
            self.insert_free(BuddyBlock {
                start_pfn: buddy_start,
                order: current_order,
            })?;
        }
        let block = BuddyBlock { start_pfn, order };
        self.allocated[self.allocated_len] = block;
        self.allocated_len += 1;
        self.remove_released(block);
        Ok(AllocatedBlock {
            start_pfn,
            order,
            zone: self.owner,
        })
    }

    pub(crate) fn free(&mut self, block: AllocatedBlock) -> Result<(), BuddyFreeError> {
        if block.zone != self.owner {
            return Err(BuddyFreeError::WrongZone);
        }
        if block.order > 63 {
            return Err(BuddyFreeError::InvalidOrder);
        }
        let pages = 1_u64 << block.order;
        let Some(end_pfn) = block.start_pfn.checked_add(pages) else {
            return Err(BuddyFreeError::OutOfBounds);
        };
        let envelope_start = self.envelope.page_start();
        let envelope_end = self.envelope.page_end();
        if block.start_pfn < envelope_start || end_pfn > envelope_end {
            return Err(BuddyFreeError::OutOfBounds);
        }
        if block.start_pfn & (pages - 1) != 0 {
            return Err(BuddyFreeError::Unaligned);
        }
        if !self.managed_contains(block.start_pfn, end_pfn) {
            return Err(BuddyFreeError::OutOfBounds);
        }
        let key = BuddyBlock {
            start_pfn: block.start_pfn,
            order: block.order,
        };
        let Some(allocated_index) = self.find_allocated(key) else {
            return if self.contains_released(key) {
                Err(BuddyFreeError::DoubleFree)
            } else {
                Err(BuddyFreeError::NotAllocated)
            };
        };
        self.remove_allocated_at(allocated_index);
        let mut merged = key;
        while merged.order < 63 {
            let buddy_start = merged.start_pfn ^ (1_u64 << merged.order);
            let Some(buddy_index) = self.find_free(BuddyBlock {
                start_pfn: buddy_start,
                order: merged.order,
            }) else {
                break;
            };
            self.remove_free_at(buddy_index);
            merged.start_pfn = merged.start_pfn.min(buddy_start);
            merged.order += 1;
        }
        self.insert_free(merged)
            .map_err(|_| BuddyFreeError::Capacity)?;
        self.record_released(key);
        Ok(())
    }

    fn insert_free(&mut self, block: BuddyBlock) -> Result<(), BuddyAllocError> {
        if self.free_len == MAX_BUDDY_BLOCKS {
            return Err(BuddyAllocError::Capacity);
        }
        self.free_blocks[self.free_len] = block;
        self.free_len += 1;
        Ok(())
    }

    fn remove_free_at(&mut self, index: usize) {
        self.free_len -= 1;
        self.free_blocks[index] = self.free_blocks[self.free_len];
    }

    fn find_free(&self, key: BuddyBlock) -> Option<usize> {
        self.free_blocks[..self.free_len]
            .iter()
            .position(|block| *block == key)
    }

    fn find_allocated(&self, key: BuddyBlock) -> Option<usize> {
        self.allocated[..self.allocated_len]
            .iter()
            .position(|block| *block == key)
    }

    fn remove_allocated_at(&mut self, index: usize) {
        self.allocated_len -= 1;
        self.allocated[index] = self.allocated[self.allocated_len];
    }

    fn contains_released(&self, key: BuddyBlock) -> bool {
        self.released[..self.released_len].contains(&key)
    }

    fn remove_released(&mut self, key: BuddyBlock) {
        if let Some(index) = self.released[..self.released_len]
            .iter()
            .position(|item| *item == key)
        {
            self.released_len -= 1;
            self.released[index] = self.released[self.released_len];
        }
    }

    fn record_released(&mut self, key: BuddyBlock) {
        if self.contains_released(key) {
            return;
        }
        if self.released_len < MAX_RELEASED_BLOCKS {
            self.released[self.released_len] = key;
            self.released_len += 1;
        } else {
            self.released.copy_within(1..MAX_RELEASED_BLOCKS, 0);
            self.released[MAX_RELEASED_BLOCKS - 1] = key;
        }
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

    pub(crate) fn buddy_record_count(self) -> u64 {
        self.free_area.buddy_record_count()
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

    pub(crate) fn refresh_from_memblock(&mut self, memblock: &MemBlock) -> Result<(), LayoutError> {
        self.managed = managed_ranges(self.envelope, memblock)?;
        self.free_area = FreeArea::initialize(self.kind, self.envelope, self.managed);
        Ok(())
    }

    pub(crate) fn seed_buddy(&mut self) -> Result<(), BuddyAllocError> {
        self.free_area.seed_buddy()
    }

    pub(crate) fn allocate(&mut self, order: u8) -> Result<AllocatedBlock, BuddyAllocError> {
        self.free_area.allocate(order)
    }

    pub(crate) fn free(&mut self, block: AllocatedBlock) -> Result<(), BuddyFreeError> {
        self.free_area.free(block)
    }

    fn contains_page_range(self, start_pfn: u64, end_pfn: u64) -> bool {
        let (zone_start, zone_end) = self.page_range();
        zone_start <= start_pfn && end_pfn <= zone_end
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

    pub(crate) fn refresh_from_memblock(&mut self, memblock: &MemBlock) -> Result<(), LayoutError> {
        self.dma32.refresh_from_memblock(memblock)?;
        self.normal.refresh_from_memblock(memblock)?;
        self.movable.refresh_from_memblock(memblock)?;
        self.validate_disjoint()
    }

    pub(crate) fn seed_buddy(&mut self) -> Result<(), BuddyAllocError> {
        self.dma32.seed_buddy()?;
        self.normal.seed_buddy()?;
        self.movable.seed_buddy()?;
        Ok(())
    }

    pub(crate) fn zone_mut(&mut self, kind: ZoneKind) -> &mut Zone {
        match kind {
            ZoneKind::DMA32 => &mut self.dma32,
            ZoneKind::Normal => &mut self.normal,
            ZoneKind::Movable => &mut self.movable,
        }
    }

    pub(crate) fn dma32_mut(&mut self) -> &mut Zone {
        &mut self.dma32
    }

    pub(crate) fn normal_mut(&mut self) -> &mut Zone {
        &mut self.normal
    }

    pub(crate) fn movable_mut(&mut self) -> &mut Zone {
        &mut self.movable
    }

    pub(crate) fn allocate(&mut self, order: u8) -> Result<AllocatedBlock, BuddyAllocError> {
        // Keep this order explicit even when ZoneLists omits an empty zone:
        // it is the fixed Movable -> Normal -> DMA32 fallback contract.
        for kind in [ZoneKind::Movable, ZoneKind::Normal, ZoneKind::DMA32] {
            let zone = self.zone_mut(kind);
            if zone.is_empty() {
                continue;
            }
            match zone.allocate(order) {
                Ok(block) => return Ok(block),
                Err(BuddyAllocError::OutOfMemory) => {}
                Err(error) => return Err(error),
            }
        }
        Err(BuddyAllocError::OutOfMemory)
    }

    pub(crate) fn free(&mut self, block: AllocatedBlock) -> Result<(), BuddyFreeError> {
        // Check the physical owner before selecting the backend.  This keeps
        // a forged block carrying a different ZoneKind distinguishable from
        // an ordinary out-of-bounds or not-allocated release.
        let pages = if block.order() > 63 {
            return Err(BuddyFreeError::InvalidOrder);
        } else {
            1_u64 << block.order()
        };
        let end_pfn = block
            .start_pfn()
            .checked_add(pages)
            .ok_or(BuddyFreeError::OutOfBounds)?;
        for (kind, zone) in [
            (ZoneKind::Movable, self.movable),
            (ZoneKind::Normal, self.normal),
            (ZoneKind::DMA32, self.dma32),
        ] {
            if zone.contains_page_range(block.start_pfn(), end_pfn) {
                if kind != block.zone() {
                    return Err(BuddyFreeError::WrongZone);
                }
                break;
            }
        }
        self.zone_mut(block.zone()).free(block)
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

fn floor_log2(value: u64) -> u8 {
    (u64::BITS - 1 - value.leading_zeros()) as u8
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
