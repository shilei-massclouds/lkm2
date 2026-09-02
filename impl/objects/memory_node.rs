//! Single-node early physical-memory layout.
//!
//! The model currently describes one flat RISC-V memory node.  This module
//! captures the small amount of concrete state needed after `setup_vm_final`:
//! the node's physical envelope, its three zone backends, the metadata
//! envelope used by MemMap, and the fixed fallback projection used by
//! ZoneLists.  PageAllocator later reserves the MemMap/page-state and buddy
//! backing ranges through MemBlock, then mutates these same backends in place.

#![allow(dead_code)]

use super::memblock::{MemBlock, MemBlockError};
use super::zone::{
    AllocatedBlock, BuddyAllocError, BuddyFreeError, LayoutError, PAGE_SHIFT, PAGE_SIZE,
    PhysicalRange, ZoneKind, ZoneSet,
};

pub(crate) type MemoryNodeError = LayoutError;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct MemoryNodeLayout {
    physical_start: u64,
    physical_end: u64,
    start_pfn: u64,
    end_pfn: u64,
    memory_range_count: usize,
}

impl MemoryNodeLayout {
    fn from_memblock(memblock: &MemBlock) -> Result<Self, MemoryNodeError> {
        let mut physical_start = u64::MAX;
        let mut physical_end = 0_u64;
        let mut memory_range_count = 0_usize;
        for (start, end) in memblock.memory_ranges() {
            if start >= end {
                return Err(LayoutError::InvalidRange);
            }
            physical_start = physical_start.min(start);
            physical_end = physical_end.max(end);
            memory_range_count += 1;
        }
        if memory_range_count == 0 || physical_start >= physical_end {
            return Err(LayoutError::MissingMemory);
        }
        // Match Linux's min_low_pfn/max_low_pfn conversion: only complete
        // base pages become part of the concrete zone envelopes.  The
        // physical envelope itself remains the exact MemBlock envelope.
        let start_pfn = physical_start
            .checked_add((1_u64 << PAGE_SHIFT) - 1)
            .ok_or(LayoutError::AddressOverflow)?
            >> PAGE_SHIFT;
        let end_pfn = physical_end >> PAGE_SHIFT;
        if start_pfn >= end_pfn {
            return Err(LayoutError::InvalidRange);
        }
        Ok(Self {
            physical_start,
            physical_end,
            start_pfn,
            end_pfn,
            memory_range_count,
        })
    }

    pub(crate) const fn physical_envelope(self) -> (u64, u64) {
        (self.physical_start, self.physical_end)
    }

    pub(crate) const fn start(self) -> u64 {
        self.physical_start
    }

    pub(crate) const fn end(self) -> u64 {
        self.physical_end
    }

    pub(crate) const fn page_range(self) -> (u64, u64) {
        (self.start_pfn, self.end_pfn)
    }

    pub(crate) const fn start_pfn(self) -> u64 {
        self.start_pfn
    }

    pub(crate) const fn end_pfn(self) -> u64 {
        self.end_pfn
    }

    pub(crate) const fn memory_range_count(self) -> usize {
        self.memory_range_count
    }

    const fn page_envelope(self) -> (u64, u64) {
        (self.start_pfn << PAGE_SHIFT, self.end_pfn << PAGE_SHIFT)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Lifecycle {
    Ready,
    Online,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct MemMap {
    envelope: PhysicalRange,
    page_envelope: PhysicalRange,
    start_pfn: u64,
    end_pfn: u64,
    reserved_range_count: usize,
    initialized: bool,
}

impl MemMap {
    fn initialize(layout: MemoryNodeLayout, memblock: &MemBlock) -> Result<Self, MemoryNodeError> {
        let envelope = PhysicalRange::new(layout.physical_start, layout.physical_end)?;
        let (page_start, page_end) = layout.page_envelope();
        let page_envelope = PhysicalRange::new(page_start, page_end)?;
        let reserved_range_count = memblock.reserved_ranges().count();
        Ok(Self {
            envelope,
            page_envelope,
            start_pfn: layout.start_pfn,
            end_pfn: layout.end_pfn,
            reserved_range_count,
            initialized: true,
        })
    }

    pub(crate) const fn envelope(self) -> (u64, u64) {
        (self.envelope.start(), self.envelope.end())
    }

    pub(crate) const fn page_range(self) -> (u64, u64) {
        (self.start_pfn, self.end_pfn)
    }

    pub(crate) const fn page_envelope(self) -> (u64, u64) {
        (self.page_envelope.start(), self.page_envelope.end())
    }

    pub(crate) const fn reserved_range_count(self) -> usize {
        self.reserved_range_count
    }

    pub(crate) const fn is_initialized(self) -> bool {
        self.initialized
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ZoneLists {
    order: [ZoneKind; 3],
    len: usize,
    initialized: bool,
}

impl ZoneLists {
    fn initialize(zones: ZoneSet) -> Self {
        let mut order = [ZoneKind::Movable, ZoneKind::Normal, ZoneKind::DMA32];
        let mut len = 0;
        for zone in order {
            let populated = match zone {
                ZoneKind::Movable => zones.movable(),
                ZoneKind::Normal => zones.normal(),
                ZoneKind::DMA32 => zones.dma32(),
            };
            if !populated.is_empty() {
                order[len] = zone;
                len += 1;
            }
        }
        Self {
            order,
            len,
            initialized: true,
        }
    }

    pub(crate) const fn len(self) -> usize {
        self.len
    }

    pub(crate) const fn get(self, index: usize) -> Option<ZoneKind> {
        if index < self.len {
            Some(self.order[index])
        } else {
            None
        }
    }

    pub(crate) fn iter(self) -> ZoneListIter {
        ZoneListIter {
            lists: self,
            next: 0,
        }
    }

    pub(crate) const fn is_single_fallback(self) -> bool {
        self.initialized
    }

    pub(crate) const fn is_initialized(self) -> bool {
        self.initialized
    }
}

#[derive(Clone, Copy)]
pub(crate) struct ZoneListIter {
    lists: ZoneLists,
    next: usize,
}

impl Iterator for ZoneListIter {
    type Item = ZoneKind;

    fn next(&mut self) -> Option<Self::Item> {
        let result = self.lists.get(self.next);
        if result.is_some() {
            self.next += 1;
        }
        result
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct MemoryNode {
    layout: MemoryNodeLayout,
    state: Lifecycle,
    zones: Option<ZoneSet>,
    mem_map: Option<MemMap>,
    zone_lists: Option<ZoneLists>,
    metadata_reserved: bool,
    buddy_online: bool,
}

impl MemoryNode {
    /// Build the Ready node envelope from the normalized MemBlock memory
    /// ranges.  No backend is initialized by this constructor.
    pub(crate) fn from_memblock(memblock: &MemBlock) -> Result<Self, MemoryNodeError> {
        Ok(Self {
            layout: MemoryNodeLayout::from_memblock(memblock)?,
            state: Lifecycle::Ready,
            zones: None,
            mem_map: None,
            zone_lists: None,
            metadata_reserved: false,
            buddy_online: false,
        })
    }

    pub(crate) fn new(memblock: &MemBlock) -> Result<Self, MemoryNodeError> {
        Self::from_memblock(memblock)
    }

    /// Enable the node and initialize its children in model order:
    /// DMA32/Normal/Movable zones (including their FreeAreas), MemMap, then
    /// ZoneLists.  State is published only after every child succeeds.
    pub(crate) fn enable(&mut self, memblock: &MemBlock) -> Result<(), MemoryNodeError> {
        if self.state == Lifecycle::Online {
            return Err(LayoutError::AlreadyOnline);
        }
        let (start, end) = self.layout.page_envelope();
        let zones = ZoneSet::initialize(start, end, memblock)?;
        let mem_map = MemMap::initialize(self.layout, memblock)?;
        let zone_lists = ZoneLists::initialize(zones);
        self.zones = Some(zones);
        self.mem_map = Some(mem_map);
        self.zone_lists = Some(zone_lists);
        self.metadata_reserved = false;
        self.buddy_online = false;
        self.state = Lifecycle::Online;
        Ok(())
    }

    /// Convenience entry point for the complete EarlyBoot backend skeleton.
    pub(crate) fn initialize(memblock: &MemBlock) -> Result<Self, MemoryNodeError> {
        let mut node = Self::from_memblock(memblock)?;
        node.enable(memblock)?;
        Ok(node)
    }

    pub(crate) const fn layout(self) -> MemoryNodeLayout {
        self.layout
    }

    pub(crate) const fn is_online(self) -> bool {
        matches!(self.state, Lifecycle::Online)
    }

    pub(crate) const fn zones(self) -> Option<ZoneSet> {
        self.zones
    }

    pub(crate) const fn mem_map(self) -> Option<MemMap> {
        self.mem_map
    }

    pub(crate) const fn zone_lists(self) -> Option<ZoneLists> {
        self.zone_lists
    }

    pub(crate) fn zones_mut(&mut self) -> Option<&mut ZoneSet> {
        self.zones.as_mut()
    }

    pub(crate) const fn metadata_reserved(self) -> bool {
        self.metadata_reserved
    }

    pub(crate) const fn is_allocator_online(self) -> bool {
        self.buddy_online
    }

    /// Reserve the page-level and Buddy backing storage through MemBlock.
    /// The exact layout is an implementation detail; the important contract
    /// is that both ranges are recorded as MemBlock reservations before any
    /// managed page is handed to Buddy.
    pub(crate) fn reserve_page_allocator_metadata(
        &mut self,
        memblock: &mut MemBlock,
    ) -> Result<(), MemBlockError> {
        if !self.is_online() {
            return Err(MemBlockError::MissingMemory);
        }
        if self.metadata_reserved {
            return Ok(());
        }
        let pages = self
            .layout
            .end_pfn
            .checked_sub(self.layout.start_pfn)
            .ok_or(MemBlockError::AddressOverflow)?;
        // A compact early-boot page record.  The FreeArea block records are
        // reserved separately so the two ownership classes remain visible in
        // MemBlock observations.
        let mem_map_bytes = pages
            .checked_mul(64)
            .and_then(|bytes| bytes.checked_add(PAGE_SIZE - 1))
            .ok_or(MemBlockError::AddressOverflow)?;
        let mem_map_bytes = (mem_map_bytes / PAGE_SIZE) * PAGE_SIZE;
        let mem_map_bytes = mem_map_bytes.max(PAGE_SIZE);
        let mem_map_base = memblock.reserve_mem_map_metadata(mem_map_bytes, PAGE_SIZE)?;

        // A MemMap reservation can split a previously contiguous managed
        // fragment.  Recompute the block-head count against that post-map
        // view before sizing the second reservation, while retaining the
        // original node state until both allocations succeed.
        let Some(mut buddy_zones) = self.zones else {
            let _ = memblock.rollback_metadata(mem_map_base, mem_map_bytes);
            return Err(MemBlockError::MissingMemory);
        };
        if let Err(error) = buddy_zones.refresh_from_memblock(memblock) {
            let _ = memblock.rollback_metadata(mem_map_base, mem_map_bytes);
            return Err(match error {
                LayoutError::MissingMemory => MemBlockError::MissingMemory,
                LayoutError::InvalidRange | LayoutError::AlreadyOnline => {
                    MemBlockError::InvalidReservation
                }
                LayoutError::AddressOverflow => MemBlockError::AddressOverflow,
                LayoutError::RangeCapacityExceeded => MemBlockError::RegionCapacityExceeded,
            });
        }
        let buddy_records = buddy_zones
            .iter()
            .map(|zone| zone.buddy_record_count())
            .sum::<u64>()
            .max(1);
        let buddy_bytes = buddy_records
            .checked_mul(32)
            .and_then(|bytes| bytes.checked_add(PAGE_SIZE - 1))
            .ok_or_else(|| {
                let _ = memblock.rollback_metadata(mem_map_base, mem_map_bytes);
                MemBlockError::AddressOverflow
            })?;
        let buddy_bytes = (buddy_bytes / PAGE_SIZE) * PAGE_SIZE;
        let buddy_bytes = buddy_bytes.max(PAGE_SIZE);
        if let Err(error) = memblock.reserve_buddy_metadata(buddy_bytes, PAGE_SIZE) {
            // Do not leave a half-installed allocator reservation behind when
            // the second metadata allocation cannot be satisfied.
            let _ = memblock.rollback_metadata(mem_map_base, mem_map_bytes);
            return Err(error);
        }
        self.zones = Some(buddy_zones);
        self.metadata_reserved = true;
        Ok(())
    }

    /// Recompute managed fragments after metadata reservations have been
    /// added.  Zone and MemMap values remain owned by this node rather than
    /// being discarded after construction.
    pub(crate) fn refresh_after_memblock(
        &mut self,
        memblock: &MemBlock,
    ) -> Result<(), LayoutError> {
        if !self.is_online() {
            return Err(LayoutError::MissingMemory);
        }
        let mut zones = self.zones.ok_or(LayoutError::MissingMemory)?;
        zones.refresh_from_memblock(memblock)?;
        let mem_map = MemMap::initialize(self.layout, memblock)?;
        let zone_lists = ZoneLists::initialize(zones);
        self.zones = Some(zones);
        self.mem_map = Some(mem_map);
        self.zone_lists = Some(zone_lists);
        self.buddy_online = false;
        Ok(())
    }

    pub(crate) fn seed_buddy(&mut self) -> Result<(), BuddyAllocError> {
        let zones = self.zones.as_mut().ok_or(BuddyAllocError::OutOfMemory)?;
        zones.seed_buddy()?;
        self.buddy_online = true;
        Ok(())
    }

    pub(crate) fn allocate_pages(&mut self, order: u8) -> Result<AllocatedBlock, BuddyAllocError> {
        if !self.buddy_online {
            return Err(BuddyAllocError::OutOfMemory);
        }
        self.zones
            .as_mut()
            .ok_or(BuddyAllocError::OutOfMemory)?
            .allocate(order)
    }

    pub(crate) fn free_pages(
        &mut self,
        block: AllocatedBlock,
        order: u8,
    ) -> Result<(), BuddyFreeError> {
        if !self.buddy_online {
            return Err(BuddyFreeError::NotAllocated);
        }
        if block.order() != order {
            return Err(BuddyFreeError::InvalidOrder);
        }
        self.zones
            .as_mut()
            .ok_or(BuddyFreeError::NotAllocated)?
            .free(block)
    }

    pub(crate) fn free_block(&mut self, block: AllocatedBlock) -> Result<(), BuddyFreeError> {
        self.free_pages(block, block.order())
    }
}
