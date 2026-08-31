/*
 * Linux-style Zone boundary envelopes for the sole physical-memory node.
 *
 * A Zone may be empty and its envelope may contain DRAM holes, reserved
 * pages, or unavailable pages. Online therefore means that boundary,
 * ownership, and ordering facts are stable; it does not mean populated.
 * Effective Page ownership is intentionally deferred to the MemMap model.
 *
 * DMA32 begins at the node DRAM start and ends no later than both the RISC-V
 * 64 32-bit DMA capability and the node memory end. CONFIG_ZONE_DMA32 decides
 * whether that Zone exists in Linux; it is not a tunable numeric boundary in
 * this model. Normal's base envelope starts at the DMA32 boundary and ends at
 * the directly addressable node limit. Movable is empty or is cut from the
 * tail of the highest populated base Zone, which may be Normal or DMA32.
 * Final effective ranges are disjoint, while their boundary envelopes cover
 * the node memory envelope despite holes and unavailable pages.
 */

use model::objects::memory_node::MemoryNode;
use model::objects::memory_node::MemoryNodeType;
use model::objects::memory_node::memory_node_covers_memblock_memory;
use model::objects::memblock::MemBlockMemory;
use model::objects::memblock::MemBlockMemoryType;

predicate zone_bound_to_unique_memory_node(
    zone: Zone,
    node: MemoryNodeType,
) -> bool;
predicate dma32_zone_bounded_by_32bit_dma_limit(
    dma32_zone: Zone,
    node: MemoryNodeType,
) -> bool;
predicate normal_zone_base_bounds_follow_dma32_and_node_limit(
    normal_zone: Zone,
    dma32_zone: Zone,
    node: MemoryNodeType,
) -> bool;
predicate movable_zone_empty_or_tail_of_highest_populated_base_zone(
    movable_zone: Zone,
    dma32_zone: Zone,
    normal_zone: Zone,
) -> bool;
predicate node_zone_effective_ranges_are_pairwise_disjoint(
    node: MemoryNodeType,
    dma32_zone: Zone,
    normal_zone: Zone,
    movable_zone: Zone,
) -> bool;
predicate node_zone_boundary_envelopes_cover_memory(
    node: MemoryNodeType,
    memory: MemBlockMemoryType,
    dma32_zone: Zone,
    normal_zone: Zone,
    movable_zone: Zone,
) -> bool;
predicate free_area_bound_to_zone(
    free_area: FreeAreaType,
    zone: Zone,
) -> bool;
predicate free_area_excludes_reserved_and_unavailable(
    free_area: FreeAreaType,
    zone: Zone,
) -> bool;

type FreeAreaType {
    parent: Zone;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    parent.state == State::Online;
                    zone_bound_to_unique_memory_node(
                        parent,
                        MemoryNode,
                    );
                }

                establishes {
                    free_area_bound_to_zone(self, parent);
                    free_area_excludes_reserved_and_unavailable(
                        self,
                        parent,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            parent.state == State::Online;
            zone_bound_to_unique_memory_node(
                parent,
                MemoryNode,
            );
            free_area_bound_to_zone(self, parent);
            free_area_excludes_reserved_and_unavailable(
                self,
                parent,
            );
        }
    }
}

type Zone {
    /* Every Zone is owned by exactly one physical-memory node. */
    parent: MemoryNodeType;
    initial_state: State::Ready;

    /*
     * A FreeArea is a possibly-empty ownership envelope. Its implementation
     * structure (orders, bitmaps, lists, and Pages) is deliberately deferred.
     */
    object FreeArea: FreeAreaType {}

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
    }
}

type DMA32ZoneType: Zone {

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    parent.state == State::Online;
                    memory_node_covers_memblock_memory(
                        parent,
                        MemBlockMemory,
                    );
                }

                establishes {
                    zone_bound_to_unique_memory_node(self, parent);
                    dma32_zone_bounded_by_32bit_dma_limit(
                        self,
                        parent,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            parent.state == State::Online;
            memory_node_covers_memblock_memory(
                parent,
                MemBlockMemory,
            );
            zone_bound_to_unique_memory_node(self, parent);
            dma32_zone_bounded_by_32bit_dma_limit(
                self,
                parent,
            );
        }
    }
}

type NormalZoneType: Zone {
    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    parent::DMA32Zone.state == State::Online;
                    zone_bound_to_unique_memory_node(
                        parent::DMA32Zone,
                        parent,
                    );
                    dma32_zone_bounded_by_32bit_dma_limit(
                        parent::DMA32Zone,
                        parent,
                    );
                }

                establishes {
                    zone_bound_to_unique_memory_node(self, parent);
                    normal_zone_base_bounds_follow_dma32_and_node_limit(
                        self,
                        parent::DMA32Zone,
                        parent,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            parent::DMA32Zone.state == State::Online;
            zone_bound_to_unique_memory_node(
                parent::DMA32Zone,
                parent,
            );
            dma32_zone_bounded_by_32bit_dma_limit(
                parent::DMA32Zone,
                parent,
            );
            zone_bound_to_unique_memory_node(self, parent);
            normal_zone_base_bounds_follow_dma32_and_node_limit(
                self,
                parent::DMA32Zone,
                parent,
            );
        }
    }
}

type MovableZoneType: Zone {
    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    parent::DMA32Zone.state == State::Online;
                    parent::NormalZone.state == State::Online;
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
                    dma32_zone_bounded_by_32bit_dma_limit(
                        parent::DMA32Zone,
                        parent,
                    );
                    normal_zone_base_bounds_follow_dma32_and_node_limit(
                        parent::NormalZone,
                        parent::DMA32Zone,
                        parent,
                    );
                }

                establishes {
                    zone_bound_to_unique_memory_node(self, parent);
                    movable_zone_empty_or_tail_of_highest_populated_base_zone(
                        self,
                        parent::DMA32Zone,
                        parent::NormalZone,
                    );
                    node_zone_effective_ranges_are_pairwise_disjoint(
                        parent,
                        parent::DMA32Zone,
                        parent::NormalZone,
                        self,
                    );
                    node_zone_boundary_envelopes_cover_memory(
                        parent,
                        MemBlockMemory,
                        parent::DMA32Zone,
                        parent::NormalZone,
                        self,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            parent::DMA32Zone.state == State::Online;
            parent::NormalZone.state == State::Online;
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
            dma32_zone_bounded_by_32bit_dma_limit(
                parent::DMA32Zone,
                parent,
            );
            normal_zone_base_bounds_follow_dma32_and_node_limit(
                parent::NormalZone,
                parent::DMA32Zone,
                parent,
            );
            zone_bound_to_unique_memory_node(self, parent);
            movable_zone_empty_or_tail_of_highest_populated_base_zone(
                self,
                parent::DMA32Zone,
                parent::NormalZone,
            );
            node_zone_effective_ranges_are_pairwise_disjoint(
                parent,
                parent::DMA32Zone,
                parent::NormalZone,
                self,
            );
            node_zone_boundary_envelopes_cover_memory(
                parent,
                MemBlockMemory,
                parent::DMA32Zone,
                parent::NormalZone,
                self,
            );
        }
    }
}

/*
 * ZoneLists models the one ZONELIST_FALLBACK projection for this
 * non-NUMA configuration.  These predicates intentionally hide the list
 * representation: no zoneref array, page metadata, PFNs, or allocator state
 * is introduced here.  The argument order of the priority predicate is the
 * zone-index descending order used by build_zonerefs_node():
 * Movable, Normal, DMA32.  Empty zones are filtered by the final predicate.
 */
predicate zone_lists_bound_to_unique_memory_node(
    zone_lists: ZoneListsType,
    node: MemoryNodeType,
) -> bool;
predicate zone_lists_is_single_fallback(
    zone_lists: ZoneListsType,
) -> bool;
predicate zone_lists_orders_populated_zones_descending(
    zone_lists: ZoneListsType,
    movable_zone: Zone,
    normal_zone: Zone,
    dma32_zone: Zone,
) -> bool;
predicate zone_lists_excludes_empty_zones(
    zone_lists: ZoneListsType,
    movable_zone: Zone,
    normal_zone: Zone,
    dma32_zone: Zone,
) -> bool;

type ZoneListsType {
    parent: MemoryNodeType;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    parent.state == State::Online;
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
                    zone_lists_bound_to_unique_memory_node(self, parent);
                    zone_lists_is_single_fallback(self);
                    zone_lists_orders_populated_zones_descending(
                        self,
                        parent::MovableZone,
                        parent::NormalZone,
                        parent::DMA32Zone,
                    );
                    zone_lists_excludes_empty_zones(
                        self,
                        parent::MovableZone,
                        parent::NormalZone,
                        parent::DMA32Zone,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            parent.state == State::Online;
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
            zone_lists_bound_to_unique_memory_node(self, parent);
            zone_lists_is_single_fallback(self);
            zone_lists_orders_populated_zones_descending(
                self,
                parent::MovableZone,
                parent::NormalZone,
                parent::DMA32Zone,
            );
            zone_lists_excludes_empty_zones(
                self,
                parent::MovableZone,
                parent::NormalZone,
                parent::DMA32Zone,
            );
        }
    }
}
