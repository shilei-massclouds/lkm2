/*
 * Per-node page metadata and the kernel-wide mem_map view.
 *
 * MemMap is the ownership unit for page metadata belonging to one
 * MemoryNode.  Its predicates intentionally describe only the valid
 * metadata envelope and status/zone relationships; they do not introduce
 * Page objects, PFNs, bitmap storage, or allocator implementation details.
 * Coverage is an envelope over populated memory: reserved or unavailable
 * pages retain their nonallocatable status instead of being treated as an
 * absence of metadata.
 * GlobalMemMap is the single kernel-scope alias used by the flat-memory
 * configuration.  It does not model a second backing map.
 */

use model::systems::kernel::Kernel;
use model::systems::kernel::KernelType;
use model::objects::memory_node::MemoryNode;
use model::objects::memory_node::MemoryNodeType;
use model::objects::memory_node::memory_node_covers_memblock_memory;
use model::objects::memblock::MemBlockMemory;
use model::objects::memblock::MemBlockMemoryType;
use model::objects::memblock::MemBlockReserved;
use model::objects::memblock::MemBlockReservedType;
use model::objects::zone::Zone;
use model::objects::zone::zone_bound_to_unique_memory_node;
use model::objects::zone::dma32_zone_bounded_by_32bit_dma_limit;
use model::objects::zone::normal_zone_base_bounds_follow_dma32_and_node_limit;
use model::objects::zone::movable_zone_empty_or_tail_of_highest_populated_base_zone;
use model::objects::zone::node_zone_effective_ranges_are_pairwise_disjoint;
use model::objects::zone::node_zone_boundary_envelopes_cover_memory;

predicate mem_map_bound_to_unique_memory_node(
    mem_map: MemMapType,
    node: MemoryNodeType,
) -> bool;
predicate mem_map_covers_populated_memory(
    mem_map: MemMapType,
    memory: MemBlockMemoryType,
) -> bool;
predicate mem_map_preserves_nonallocatable_status(
    mem_map: MemMapType,
    reserved: MemBlockReservedType,
) -> bool;
predicate mem_map_zone_ownership_consistent(
    mem_map: MemMapType,
    dma32_zone: Zone,
    normal_zone: Zone,
    movable_zone: Zone,
) -> bool;

predicate global_mem_map_aliases_node_mem_map(
    global_mem_map: GlobalMemMapType,
    node_mem_map: MemMapType,
) -> bool;

type MemMapType {
    parent: MemoryNodeType;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    parent.state == State::Online;
                    MemBlockMemory.state == State::Online;
                    MemBlockReserved.state == State::Online;
                    parent::DMA32Zone.state == State::Online;
                    parent::NormalZone.state == State::Online;
                    parent::MovableZone.state == State::Online;

                    memory_node_covers_memblock_memory(
                        parent,
                        MemBlockMemory,
                    );
                    zone_bound_to_unique_memory_node(
                        parent::DMA32Zone,
                        parent,
                    );
                    zone_bound_to_unique_memory_node(
                        parent::NormalZone,
                        parent,
                    );
                    zone_bound_to_unique_memory_node(
                        parent::MovableZone,
                        parent,
                    );
                    dma32_zone_bounded_by_32bit_dma_limit(
                        parent::DMA32Zone,
                        parent,
                    );
                    normal_zone_base_bounds_follow_dma32_and_node_limit(
                        parent::NormalZone,
                        parent::DMA32Zone,
                        parent,
                    );
                    movable_zone_empty_or_tail_of_highest_populated_base_zone(
                        parent::MovableZone,
                        parent::DMA32Zone,
                        parent::NormalZone,
                    );
                    node_zone_effective_ranges_are_pairwise_disjoint(
                        parent,
                        parent::DMA32Zone,
                        parent::NormalZone,
                        parent::MovableZone,
                    );
                    node_zone_boundary_envelopes_cover_memory(
                        parent,
                        MemBlockMemory,
                        parent::DMA32Zone,
                        parent::NormalZone,
                        parent::MovableZone,
                    );
                }

                establishes {
                    mem_map_bound_to_unique_memory_node(self, parent);
                    mem_map_covers_populated_memory(
                        self,
                        MemBlockMemory,
                    );
                    mem_map_preserves_nonallocatable_status(
                        self,
                        MemBlockReserved,
                    );
                    mem_map_zone_ownership_consistent(
                        self,
                        parent::DMA32Zone,
                        parent::NormalZone,
                        parent::MovableZone,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            parent.state == State::Online;
            MemBlockMemory.state == State::Online;
            MemBlockReserved.state == State::Online;
            parent::DMA32Zone.state == State::Online;
            parent::NormalZone.state == State::Online;
            parent::MovableZone.state == State::Online;

            mem_map_bound_to_unique_memory_node(self, parent);
            memory_node_covers_memblock_memory(
                parent,
                MemBlockMemory,
            );
            zone_bound_to_unique_memory_node(
                parent::DMA32Zone,
                parent,
            );
            zone_bound_to_unique_memory_node(
                parent::NormalZone,
                parent,
            );
            zone_bound_to_unique_memory_node(
                parent::MovableZone,
                parent,
            );
            dma32_zone_bounded_by_32bit_dma_limit(
                parent::DMA32Zone,
                parent,
            );
            normal_zone_base_bounds_follow_dma32_and_node_limit(
                parent::NormalZone,
                parent::DMA32Zone,
                parent,
            );
            movable_zone_empty_or_tail_of_highest_populated_base_zone(
                parent::MovableZone,
                parent::DMA32Zone,
                parent::NormalZone,
            );
            node_zone_effective_ranges_are_pairwise_disjoint(
                parent,
                parent::DMA32Zone,
                parent::NormalZone,
                parent::MovableZone,
            );
            node_zone_boundary_envelopes_cover_memory(
                parent,
                MemBlockMemory,
                parent::DMA32Zone,
                parent::NormalZone,
                parent::MovableZone,
            );

            mem_map_covers_populated_memory(
                self,
                MemBlockMemory,
            );
            mem_map_preserves_nonallocatable_status(
                self,
                MemBlockReserved,
            );
            mem_map_zone_ownership_consistent(
                self,
                parent::DMA32Zone,
                parent::NormalZone,
                parent::MovableZone,
            );
        }
    }
}

type GlobalMemMapType {
    parent: KernelType;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    MemoryNode::MemMap.state == State::Online;
                }

                establishes {
                    global_mem_map_aliases_node_mem_map(
                        GlobalMemMap,
                        MemoryNode::MemMap,
                    );
                }
            }
        }
    }

    state State::Online {
    }
}

object GlobalMemMap: GlobalMemMapType {
    parent: Kernel;
}
