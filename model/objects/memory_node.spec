/*
 * The sole non-NUMA physical-memory node in the current RISC-V 64 model.
 *
 * Online means that the node's memory envelope is fixed and covers memblock
 * memory. It does not imply that every address in the envelope is usable:
 * DRAM holes, reservations, and unavailable pages are deliberately left for
 * the later page-metadata model.
 */

use model::systems::kernel::Kernel;
use model::objects::memblock::MemBlock;
use model::objects::memblock::MemBlockMemory;
use model::objects::memblock::MemBlockMemoryType;

predicate memory_node_covers_memblock_memory(
    node: MemoryNodeType,
    memory: MemBlockMemoryType,
) -> bool;

type MemoryNodeType {
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

object MemoryNode: MemoryNodeType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    MemBlock.state == State::Online;
                }

                establishes {
                    memory_node_covers_memblock_memory(
                        self,
                        MemBlockMemory,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            MemBlock.state == State::Online;
            memory_node_covers_memblock_memory(
                self,
                MemBlockMemory,
            );
        }
    }
}
