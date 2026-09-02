//! Order-based Buddy page allocator handoff.
//!
//! The model intentionally exposes only the allocator protocol.  This module
//! owns the concrete state used by the host-side implementation: MemBlock
//! reservations are made first, managed fragments are then seeded into each
//! zone's FreeArea, and allocation/free operations mutate those persistent
//! backends in place.

#![allow(dead_code)]

use super::memblock::MemBlockError;
use super::memory_node::MemoryNode;
use super::zone::{AllocatedBlock, BuddyAllocError, BuddyFreeError, LayoutError};

pub(crate) type AllocError = BuddyAllocError;
pub(crate) type FreeError = BuddyFreeError;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PageAllocatorError {
    AlreadyOnline,
    NotOnline,
    Layout(LayoutError),
    MemBlock(MemBlockError),
    Allocation(BuddyAllocError),
    Free(BuddyFreeError),
}

pub(crate) struct PageAllocator {
    online: bool,
}

impl PageAllocator {
    pub(crate) const fn new() -> Self {
        Self { online: false }
    }

    pub(crate) const fn is_online(&self) -> bool {
        self.online
    }

    /// Initialize the persistent node backends and complete the MemBlock ->
    /// Buddy ownership transfer.  Metadata reservation is intentionally part
    /// of this operation and occurs before `memblock_free_all`.
    pub(crate) fn enable(
        &mut self,
        node: &mut MemoryNode,
        memblock: &mut super::memblock::MemBlock,
    ) -> Result<(), PageAllocatorError> {
        if self.online {
            return Err(PageAllocatorError::AlreadyOnline);
        }
        if !node.is_online() {
            return Err(PageAllocatorError::Layout(LayoutError::MissingMemory));
        }
        node.reserve_page_allocator_metadata(memblock)
            .map_err(PageAllocatorError::MemBlock)?;
        node.refresh_after_memblock(memblock)
            .map_err(PageAllocatorError::Layout)?;

        if !memblock.free_all_completed() {
            node.seed_buddy().map_err(PageAllocatorError::Allocation)?;
            memblock
                .memblock_free_all()
                .map_err(PageAllocatorError::MemBlock)?;
        } else if !node.is_allocator_online() {
            node.seed_buddy().map_err(PageAllocatorError::Allocation)?;
        }
        self.online = true;
        Ok(())
    }

    pub(crate) fn initialize(
        node: &mut MemoryNode,
        memblock: &mut super::memblock::MemBlock,
    ) -> Result<Self, PageAllocatorError> {
        let mut allocator = Self::new();
        allocator.enable(node, memblock)?;
        Ok(allocator)
    }

    pub(crate) fn alloc_pages(
        &mut self,
        node: &mut MemoryNode,
        order: u8,
    ) -> Result<AllocatedBlock, PageAllocatorError> {
        if !self.online {
            return Err(PageAllocatorError::NotOnline);
        }
        node.allocate_pages(order)
            .map_err(PageAllocatorError::Allocation)
    }

    /// Direct coding-layer result type for callers that only need allocator
    /// success/failure.  Lifecycle failures are reported as OutOfMemory at
    /// this narrow interface; `alloc_pages` retains the richer wrapper.
    pub(crate) fn allocate_pages(
        &mut self,
        node: &mut MemoryNode,
        order: u8,
    ) -> Result<AllocatedBlock, AllocError> {
        if !self.online {
            return Err(BuddyAllocError::OutOfMemory);
        }
        node.allocate_pages(order)
    }

    pub(crate) fn free_pages(
        &mut self,
        node: &mut MemoryNode,
        block: AllocatedBlock,
        order: u8,
    ) -> Result<(), PageAllocatorError> {
        if !self.online {
            return Err(PageAllocatorError::NotOnline);
        }
        node.free_pages(block, order)
            .map_err(PageAllocatorError::Free)
    }

    pub(crate) fn release_pages(
        &mut self,
        node: &mut MemoryNode,
        block: AllocatedBlock,
        order: u8,
    ) -> Result<(), FreeError> {
        if !self.online {
            return Err(BuddyFreeError::NotAllocated);
        }
        node.free_pages(block, order)
    }

    pub(crate) fn alloc(
        &mut self,
        node: &mut MemoryNode,
        order: u8,
    ) -> Result<AllocatedBlock, PageAllocatorError> {
        self.alloc_pages(node, order)
    }

    pub(crate) fn free(
        &mut self,
        node: &mut MemoryNode,
        block: AllocatedBlock,
    ) -> Result<(), PageAllocatorError> {
        self.free_pages(node, block, block.order())
    }
}
