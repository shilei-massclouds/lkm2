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

type Zone {
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
    }
}

object DMA32Zone: Zone {
    parent: MemoryNode;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    MemoryNode.state == State::Online;
                    memory_node_covers_memblock_memory(
                        MemoryNode,
                        MemBlockMemory,
                    );
                }

                establishes {
                    zone_bound_to_unique_memory_node(self, MemoryNode);
                    dma32_zone_bounded_by_32bit_dma_limit(
                        self,
                        MemoryNode,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            MemoryNode.state == State::Online;
            memory_node_covers_memblock_memory(
                MemoryNode,
                MemBlockMemory,
            );
            zone_bound_to_unique_memory_node(self, MemoryNode);
            dma32_zone_bounded_by_32bit_dma_limit(
                self,
                MemoryNode,
            );
        }
    }
}

object NormalZone: Zone {
    parent: MemoryNode;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    DMA32Zone.state == State::Online;
                    zone_bound_to_unique_memory_node(
                        DMA32Zone,
                        MemoryNode,
                    );
                    dma32_zone_bounded_by_32bit_dma_limit(
                        DMA32Zone,
                        MemoryNode,
                    );
                }

                establishes {
                    zone_bound_to_unique_memory_node(self, MemoryNode);
                    normal_zone_base_bounds_follow_dma32_and_node_limit(
                        self,
                        DMA32Zone,
                        MemoryNode,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            DMA32Zone.state == State::Online;
            zone_bound_to_unique_memory_node(
                DMA32Zone,
                MemoryNode,
            );
            dma32_zone_bounded_by_32bit_dma_limit(
                DMA32Zone,
                MemoryNode,
            );
            zone_bound_to_unique_memory_node(self, MemoryNode);
            normal_zone_base_bounds_follow_dma32_and_node_limit(
                self,
                DMA32Zone,
                MemoryNode,
            );
        }
    }
}

object MovableZone: Zone {
    parent: MemoryNode;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    DMA32Zone.state == State::Online;
                    NormalZone.state == State::Online;
                    memory_node_covers_memblock_memory(
                        MemoryNode,
                        MemBlockMemory,
                    );
                    zone_bound_to_unique_memory_node(
                        DMA32Zone,
                        MemoryNode,
                    );
                    zone_bound_to_unique_memory_node(
                        NormalZone,
                        MemoryNode,
                    );
                    dma32_zone_bounded_by_32bit_dma_limit(
                        DMA32Zone,
                        MemoryNode,
                    );
                    normal_zone_base_bounds_follow_dma32_and_node_limit(
                        NormalZone,
                        DMA32Zone,
                        MemoryNode,
                    );
                }

                establishes {
                    zone_bound_to_unique_memory_node(self, MemoryNode);
                    movable_zone_empty_or_tail_of_highest_populated_base_zone(
                        self,
                        DMA32Zone,
                        NormalZone,
                    );
                    node_zone_effective_ranges_are_pairwise_disjoint(
                        MemoryNode,
                        DMA32Zone,
                        NormalZone,
                        self,
                    );
                    node_zone_boundary_envelopes_cover_memory(
                        MemoryNode,
                        MemBlockMemory,
                        DMA32Zone,
                        NormalZone,
                        self,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            DMA32Zone.state == State::Online;
            NormalZone.state == State::Online;
            memory_node_covers_memblock_memory(
                MemoryNode,
                MemBlockMemory,
            );
            zone_bound_to_unique_memory_node(
                DMA32Zone,
                MemoryNode,
            );
            zone_bound_to_unique_memory_node(
                NormalZone,
                MemoryNode,
            );
            dma32_zone_bounded_by_32bit_dma_limit(
                DMA32Zone,
                MemoryNode,
            );
            normal_zone_base_bounds_follow_dma32_and_node_limit(
                NormalZone,
                DMA32Zone,
                MemoryNode,
            );
            zone_bound_to_unique_memory_node(self, MemoryNode);
            movable_zone_empty_or_tail_of_highest_populated_base_zone(
                self,
                DMA32Zone,
                NormalZone,
            );
            node_zone_effective_ranges_are_pairwise_disjoint(
                MemoryNode,
                DMA32Zone,
                NormalZone,
                self,
            );
            node_zone_boundary_envelopes_cover_memory(
                MemoryNode,
                MemBlockMemory,
                DMA32Zone,
                NormalZone,
                self,
            );
        }
    }
}
