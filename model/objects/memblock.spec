/* Early physical-memory discovery and required reservation completion. */

use model::systems::kernel::Kernel;
use model::objects::dtb_blob::DtbBlob;
use model::objects::dtb_blob::DtbBlobType;
use model::objects::dtb_blob::dtb_blob_describes_nonempty_valid_physical_memory;
use model::objects::dtb_blob::dtb_blob_physical_range_valid;
use model::objects::dtb_blob::dtb_blob_reserve_map_and_reserved_memory_valid;
use model::objects::kernel_image::KernelImage;
use model::objects::kernel_image::KernelImageType;

predicate memblock_memory_derived_from_dtb(
    memory: MemBlockMemoryType,
    blob: DtbBlobType,
) -> bool;
predicate memblock_required_reservations_complete(
    reserved: MemBlockReservedType,
    kernel_image: KernelImageType,
    blob: DtbBlobType,
) -> bool;
/* The allocator has taken ownership of all unreserved managed pages. */
predicate memblock_free_all_completed() -> bool;

type MemBlockType {
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
        actions {
            on Action::FreeAll;
        }
    }
}

type MemBlockMemoryType {
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

type MemBlockReservedType {
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

object MemBlock: MemBlockType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    MemBlockMemory.state == State::Online;
                    MemBlockReserved.state == State::Online;
                }
            }
        }
    }

    state State::Online {
        invariant {
            MemBlockMemory.state == State::Online;
            MemBlockReserved.state == State::Online;
        }

        actions {
            override on Action::FreeAll {
                establishes {
                    memblock_free_all_completed();
                }
            }
        }
    }
}

object MemBlockMemory: MemBlockMemoryType {
    parent: MemBlock;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    DtbBlob.state == State::Online;
                    dtb_blob_describes_nonempty_valid_physical_memory(DtbBlob);
                }

                establishes {
                    memblock_memory_derived_from_dtb(self, DtbBlob);
                }
            }
        }
    }

    state State::Online {
        invariant {
            DtbBlob.state == State::Online;
            dtb_blob_describes_nonempty_valid_physical_memory(DtbBlob);
            memblock_memory_derived_from_dtb(self, DtbBlob);
        }
    }
}

object MemBlockReserved: MemBlockReservedType {
    parent: MemBlock;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    MemBlockMemory.state == State::Online;
                    DtbBlob.state == State::Online;
                    KernelImage.state == State::Ready;
                    dtb_blob_physical_range_valid(DtbBlob);
                    dtb_blob_reserve_map_and_reserved_memory_valid(DtbBlob);
                }

                establishes {
                    memblock_required_reservations_complete(
                        self,
                        KernelImage,
                        DtbBlob,
                    );
                }
            }
        }
    }

    state State::Online {
        invariant {
            MemBlockMemory.state == State::Online;
            DtbBlob.state == State::Online;
            KernelImage.state == State::Ready;
            dtb_blob_physical_range_valid(DtbBlob);
            dtb_blob_reserve_map_and_reserved_memory_valid(DtbBlob);
            memblock_required_reservations_complete(
                self,
                KernelImage,
                DtbBlob,
            );
        }

    }
}
