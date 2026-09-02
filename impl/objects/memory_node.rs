//! Single-node early physical-memory layout.
//!
//! The model currently describes one flat RISC-V memory node.  This module
//! captures the small amount of concrete state needed after `setup_vm_final`:
//! the node's physical envelope, its three zone backends, the metadata
//! envelope used by MemMap, and the fixed fallback projection used by
//! ZoneLists.  It intentionally does not allocate per-page metadata or expose
//! a page allocator.

#![allow(dead_code)]

use super::memblock::MemBlock;
use super::zone::{LayoutError, PAGE_SHIFT, PhysicalRange, ZoneKind, ZoneSet};

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
}
