/* Kernel-wide physical-page allocation interface over the node backends. */

use model::systems::kernel::Kernel;
use model::systems::kernel::KernelType;
use model::objects::memory_node::MemoryNode;
use model::objects::memory_node::MemoryNodeType;
use model::objects::memory_node::memory_node_covers_memblock_memory;
use model::objects::memblock::MemBlockMemory;
use model::objects::memblock::MemBlockReserved;
use model::objects::zone::Zone;
use model::objects::zone::FreeAreaType;
use model::objects::zone::ZoneListsType;
use model::objects::zone::zone_bound_to_unique_memory_node;
use model::objects::zone::dma32_zone_bounded_by_32bit_dma_limit;
use model::objects::zone::normal_zone_base_bounds_follow_dma32_and_node_limit;
use model::objects::zone::movable_zone_empty_or_tail_of_highest_populated_base_zone;
use model::objects::zone::node_zone_effective_ranges_are_pairwise_disjoint;
use model::objects::zone::node_zone_boundary_envelopes_cover_memory;
use model::objects::zone::free_area_bound_to_zone;
use model::objects::zone::free_area_excludes_reserved_and_unavailable;
use model::objects::zone::zone_lists_bound_to_unique_memory_node;
use model::objects::zone::zone_lists_is_single_fallback;
use model::objects::zone::zone_lists_orders_populated_zones_descending;
use model::objects::zone::zone_lists_excludes_empty_zones;
use model::objects::mem_map::MemMapType;
use model::objects::mem_map::mem_map_bound_to_unique_memory_node;
use model::objects::mem_map::mem_map_covers_populated_memory;
use model::objects::mem_map::mem_map_preserves_nonallocatable_status;
use model::objects::mem_map::mem_map_zone_ownership_consistent;

predicate page_allocator_uses_node_zones(
    allocator: PageAllocatorType,
    node: MemoryNodeType,
    dma32_zone: Zone,
    normal_zone: Zone,
    movable_zone: Zone,
) -> bool;
predicate page_allocator_uses_node_mem_map(
    allocator: PageAllocatorType,
    mem_map: MemMapType,
) -> bool;
predicate page_allocator_uses_zone_lists(
    allocator: PageAllocatorType,
    zone_lists: ZoneListsType,
) -> bool;
predicate page_allocator_uses_zone_free_areas(
    allocator: PageAllocatorType,
    dma32_free_area: FreeAreaType,
    normal_free_area: FreeAreaType,
    movable_free_area: FreeAreaType,
) -> bool;

type PageAllocatorType {
    parent: KernelType;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    MemoryNode.state == State::Online;
                    MemoryNode::DMA32Zone.state == State::Online;
                    MemoryNode::NormalZone.state == State::Online;
                    MemoryNode::MovableZone.state == State::Online;
                    MemoryNode::DMA32Zone::FreeArea.state == State::Online;
                    MemoryNode::NormalZone::FreeArea.state == State::Online;
                    MemoryNode::MovableZone::FreeArea.state == State::Online;
                    MemoryNode::MemMap.state == State::Online;
                    MemoryNode::ZoneLists.state == State::Online;

                    memory_node_covers_memblock_memory(
                        MemoryNode,
                        MemBlockMemory,
                    );
                    zone_bound_to_unique_memory_node(
                        MemoryNode::DMA32Zone,
                        MemoryNode,
                    );
                    zone_bound_to_unique_memory_node(
                        MemoryNode::NormalZone,
                        MemoryNode,
                    );
                    zone_bound_to_unique_memory_node(
                        MemoryNode::MovableZone,
                        MemoryNode,
                    );
                    dma32_zone_bounded_by_32bit_dma_limit(
                        MemoryNode::DMA32Zone,
                        MemoryNode,
                    );
                    normal_zone_base_bounds_follow_dma32_and_node_limit(
                        MemoryNode::NormalZone,
                        MemoryNode::DMA32Zone,
                        MemoryNode,
                    );
                    movable_zone_empty_or_tail_of_highest_populated_base_zone(
                        MemoryNode::MovableZone,
                        MemoryNode::DMA32Zone,
                        MemoryNode::NormalZone,
                    );
                    node_zone_effective_ranges_are_pairwise_disjoint(
                        MemoryNode,
                        MemoryNode::DMA32Zone,
                        MemoryNode::NormalZone,
                        MemoryNode::MovableZone,
                    );
                    node_zone_boundary_envelopes_cover_memory(
                        MemoryNode,
                        MemBlockMemory,
                        MemoryNode::DMA32Zone,
                        MemoryNode::NormalZone,
                        MemoryNode::MovableZone,
                    );
                    free_area_bound_to_zone(
                        MemoryNode::DMA32Zone::FreeArea,
                        MemoryNode::DMA32Zone,
                    );
                    free_area_bound_to_zone(
                        MemoryNode::NormalZone::FreeArea,
                        MemoryNode::NormalZone,
                    );
                    free_area_bound_to_zone(
                        MemoryNode::MovableZone::FreeArea,
                        MemoryNode::MovableZone,
                    );
                    free_area_excludes_reserved_and_unavailable(
                        MemoryNode::DMA32Zone::FreeArea,
                        MemoryNode::DMA32Zone,
                    );
                    free_area_excludes_reserved_and_unavailable(
                        MemoryNode::NormalZone::FreeArea,
                        MemoryNode::NormalZone,
                    );
                    free_area_excludes_reserved_and_unavailable(
                        MemoryNode::MovableZone::FreeArea,
                        MemoryNode::MovableZone,
                    );
                    mem_map_bound_to_unique_memory_node(
                        MemoryNode::MemMap,
                        MemoryNode,
                    );
                    mem_map_covers_populated_memory(
                        MemoryNode::MemMap,
                        MemBlockMemory,
                    );
                    mem_map_preserves_nonallocatable_status(
                        MemoryNode::MemMap,
                        MemBlockReserved,
                    );
                    mem_map_zone_ownership_consistent(
                        MemoryNode::MemMap,
                        MemoryNode::DMA32Zone,
                        MemoryNode::NormalZone,
                        MemoryNode::MovableZone,
                    );
                    zone_lists_bound_to_unique_memory_node(
                        MemoryNode::ZoneLists,
                        MemoryNode,
                    );
                    zone_lists_is_single_fallback(
                        MemoryNode::ZoneLists,
                    );
                    zone_lists_orders_populated_zones_descending(
                        MemoryNode::ZoneLists,
                        MemoryNode::MovableZone,
                        MemoryNode::NormalZone,
                        MemoryNode::DMA32Zone,
                    );
                    zone_lists_excludes_empty_zones(
                        MemoryNode::ZoneLists,
                        MemoryNode::MovableZone,
                        MemoryNode::NormalZone,
                        MemoryNode::DMA32Zone,
                    );
                }

                establishes {
                    page_allocator_uses_node_zones(
                        self,
                        MemoryNode,
                        MemoryNode::DMA32Zone,
                        MemoryNode::NormalZone,
                        MemoryNode::MovableZone,
                    );
                    page_allocator_uses_node_mem_map(
                        self,
                        MemoryNode::MemMap,
                    );
                    page_allocator_uses_zone_lists(
                        self,
                        MemoryNode::ZoneLists,
                    );
                    page_allocator_uses_zone_free_areas(
                        self,
                        MemoryNode::DMA32Zone::FreeArea,
                        MemoryNode::NormalZone::FreeArea,
                        MemoryNode::MovableZone::FreeArea,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            MemoryNode.state == State::Online;
            MemoryNode::DMA32Zone.state == State::Online;
            MemoryNode::NormalZone.state == State::Online;
            MemoryNode::MovableZone.state == State::Online;
            MemoryNode::DMA32Zone::FreeArea.state == State::Online;
            MemoryNode::NormalZone::FreeArea.state == State::Online;
            MemoryNode::MovableZone::FreeArea.state == State::Online;
            MemoryNode::MemMap.state == State::Online;
            MemoryNode::ZoneLists.state == State::Online;

            memory_node_covers_memblock_memory(
                MemoryNode,
                MemBlockMemory,
            );
            zone_bound_to_unique_memory_node(
                MemoryNode::DMA32Zone,
                MemoryNode,
            );
            zone_bound_to_unique_memory_node(
                MemoryNode::NormalZone,
                MemoryNode,
            );
            zone_bound_to_unique_memory_node(
                MemoryNode::MovableZone,
                MemoryNode,
            );
            dma32_zone_bounded_by_32bit_dma_limit(
                MemoryNode::DMA32Zone,
                MemoryNode,
            );
            normal_zone_base_bounds_follow_dma32_and_node_limit(
                MemoryNode::NormalZone,
                MemoryNode::DMA32Zone,
                MemoryNode,
            );
            movable_zone_empty_or_tail_of_highest_populated_base_zone(
                MemoryNode::MovableZone,
                MemoryNode::DMA32Zone,
                MemoryNode::NormalZone,
            );
            node_zone_effective_ranges_are_pairwise_disjoint(
                MemoryNode,
                MemoryNode::DMA32Zone,
                MemoryNode::NormalZone,
                MemoryNode::MovableZone,
            );
            node_zone_boundary_envelopes_cover_memory(
                MemoryNode,
                MemBlockMemory,
                MemoryNode::DMA32Zone,
                MemoryNode::NormalZone,
                MemoryNode::MovableZone,
            );
            free_area_bound_to_zone(
                MemoryNode::DMA32Zone::FreeArea,
                MemoryNode::DMA32Zone,
            );
            free_area_bound_to_zone(
                MemoryNode::NormalZone::FreeArea,
                MemoryNode::NormalZone,
            );
            free_area_bound_to_zone(
                MemoryNode::MovableZone::FreeArea,
                MemoryNode::MovableZone,
            );
            free_area_excludes_reserved_and_unavailable(
                MemoryNode::DMA32Zone::FreeArea,
                MemoryNode::DMA32Zone,
            );
            free_area_excludes_reserved_and_unavailable(
                MemoryNode::NormalZone::FreeArea,
                MemoryNode::NormalZone,
            );
            free_area_excludes_reserved_and_unavailable(
                MemoryNode::MovableZone::FreeArea,
                MemoryNode::MovableZone,
            );
            mem_map_bound_to_unique_memory_node(
                MemoryNode::MemMap,
                MemoryNode,
            );
            mem_map_covers_populated_memory(
                MemoryNode::MemMap,
                MemBlockMemory,
            );
            mem_map_preserves_nonallocatable_status(
                MemoryNode::MemMap,
                MemBlockReserved,
            );
            mem_map_zone_ownership_consistent(
                MemoryNode::MemMap,
                MemoryNode::DMA32Zone,
                MemoryNode::NormalZone,
                MemoryNode::MovableZone,
            );
            zone_lists_bound_to_unique_memory_node(
                MemoryNode::ZoneLists,
                MemoryNode,
            );
            zone_lists_is_single_fallback(
                MemoryNode::ZoneLists,
            );
            zone_lists_orders_populated_zones_descending(
                MemoryNode::ZoneLists,
                MemoryNode::MovableZone,
                MemoryNode::NormalZone,
                MemoryNode::DMA32Zone,
            );
            zone_lists_excludes_empty_zones(
                MemoryNode::ZoneLists,
                MemoryNode::MovableZone,
                MemoryNode::NormalZone,
                MemoryNode::DMA32Zone,
            );

            page_allocator_uses_node_zones(
                self,
                MemoryNode,
                MemoryNode::DMA32Zone,
                MemoryNode::NormalZone,
                MemoryNode::MovableZone,
            );
            page_allocator_uses_node_mem_map(
                self,
                MemoryNode::MemMap,
            );
            page_allocator_uses_zone_lists(
                self,
                MemoryNode::ZoneLists,
            );
            page_allocator_uses_zone_free_areas(
                self,
                MemoryNode::DMA32Zone::FreeArea,
                MemoryNode::NormalZone::FreeArea,
                MemoryNode::MovableZone::FreeArea,
            );
        }
    }
}

object PageAllocator: PageAllocatorType {
    parent: Kernel;

    state State::Online {
        actions {
            on Action::AllocPages;
            on Action::FreePages;
        }
    }
}
