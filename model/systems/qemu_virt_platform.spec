/* Qemu Virt Platform - default platform for lkm2 */

use super::opensbi::OpenSBI;
use model::objects::dtb_blob::DtbBlob;
use model::objects::dtb_blob::dtb_blob_describes_nonempty_valid_physical_memory;
use model::objects::dtb_blob::dtb_blob_physical_range_size_at_least;
use model::objects::dtb_blob::dtb_blob_physical_range_valid;
use model::objects::dtb_blob::dtb_blob_reserve_map_and_reserved_memory_valid;

type QemuVirtPlatformType;

object QemuVirtPlatform: QemuVirtPlatformType {
    parent: Computer;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
            }
        }
    }

    state State::Prepared {
        transitions {
            on Transition::Setup -> State::Ready {
            }
        }
    }

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
                establishes {
                    dtb_blob_physical_range_size_at_least(DtbBlob, 1);
                    dtb_blob_physical_range_valid(DtbBlob);
                    dtb_blob_describes_nonempty_valid_physical_memory(DtbBlob);
                    dtb_blob_reserve_map_and_reserved_memory_valid(DtbBlob);
                }

                emits {
                    OpenSBI.Transition::Enable;
                }
            }
        }
    }

    state State::Online {
        invariant {
            dtb_blob_physical_range_size_at_least(DtbBlob, 1);
            dtb_blob_physical_range_valid(DtbBlob);
            dtb_blob_describes_nonempty_valid_physical_memory(DtbBlob);
            dtb_blob_reserve_map_and_reserved_memory_valid(DtbBlob);
        }
    }
}

/*
predicate riscv64_isa_capabilities_available() -> bool;
predicate riscv64_platform_system_spec_established() -> bool;
predicate riscv64_platform_constructed() -> bool;

object Riscv64: IsaObject {
    initial_state: State::Online;
    parent: Riscv64Platform;
    source: external_spec::riscv_isa;

    state State::Online {
        invariant {
            attrs_accessible(self);
            riscv64_isa_capabilities_available();
        }
    }
}

object Riscv64Platform: PlatformObject {
    initial_state: State::Base;
    parent: Computer;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
                depends_on {
                    Riscv64.state == State::Online;
                }

                ensures {
                    riscv64_platform_system_spec_established();
                }
            }
        }
    }

    state State::Prepared {
        invariant {
            Riscv64.state == State::Online;
            riscv64_platform_system_spec_established();
        }

        transitions {
            on Transition::Setup -> State::Ready {
                ensures {
                    riscv64_platform_constructed();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            Riscv64.state == State::Online;
            riscv64_platform_system_spec_established();
            riscv64_platform_constructed();
        }

        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    Computer.state == State::Online;
                    Riscv64.state == State::Online;
                    riscv64_platform_system_spec_established();
                    riscv64_platform_constructed();
                }

                emits {
                    OpenSBI.Transition::Enable;
                }
            }
        }
    }

    state State::Online {
        invariant {
            Riscv64.state == State::Online;
            riscv64_platform_system_spec_established();
            riscv64_platform_constructed();
        }
    }
*/
