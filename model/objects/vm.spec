/* Early virtual-memory setup objects. */

predicate kernel_map_established() -> bool;
predicate kernel_map_records_supported_paging_mode() -> bool;
predicate paging_mode_probe_completed_with_satp_bare() -> bool;
predicate trampoline_first_segment_mapping_established() -> bool;
predicate early_kernel_image_mapping_established() -> bool;
predicate early_dtb_four_mib_mapping_established() -> bool;
predicate vm_setup_completes_with_satp_bare() -> bool;

type VmType {
    initial_state: State::Base;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
                drives {
                    KernelMap.Transition::Preset;
                    TrampolinePageTable.Transition::Preset;
                    EarlyPageTable.Transition::Preset;
                    KernelMap.Transition::Setup;
                }

                ensures {
                    KernelMap.state == State::Ready;
                    TrampolinePageTable.state == State::Prepared;
                    EarlyPageTable.state == State::Prepared;
                }
            }
        }
    }

    state State::Prepared {
        invariant {
            KernelMap.state == State::Ready;
            TrampolinePageTable.state == State::Prepared;
            EarlyPageTable.state == State::Prepared;
            kernel_map_established();
            kernel_map_records_supported_paging_mode();
            paging_mode_probe_completed_with_satp_bare();
        }

        transitions {
            on Transition::Setup -> State::Ready {
                drives {
                    TrampolinePageTable.Transition::Setup;
                    EarlyPageTable.Transition::Setup;
                }

                ensures {
                    KernelMap.state == State::Ready;
                    TrampolinePageTable.state == State::Ready;
                    EarlyPageTable.state == State::Ready;
                }

                establishes {
                    vm_setup_completes_with_satp_bare();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            KernelMap.state == State::Ready;
            TrampolinePageTable.state == State::Ready;
            EarlyPageTable.state == State::Ready;
            kernel_map_established();
            kernel_map_records_supported_paging_mode();
            paging_mode_probe_completed_with_satp_bare();
            trampoline_first_segment_mapping_established();
            early_kernel_image_mapping_established();
            early_dtb_four_mib_mapping_established();
            vm_setup_completes_with_satp_bare();
        }
    }
}

type KernelMapType {
    initial_state: State::Base;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
            }
        }
    }

    state State::Prepared {
        transitions {
            on Transition::Setup -> State::Ready {
                establishes {
                    kernel_map_established();
                    kernel_map_records_supported_paging_mode();
                    paging_mode_probe_completed_with_satp_bare();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            kernel_map_established();
            kernel_map_records_supported_paging_mode();
            paging_mode_probe_completed_with_satp_bare();
        }
    }
}

type PageTableType {
    initial_state: State::Base;

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
    }
}

object Vm: VmType {
}

object KernelMap: KernelMapType {
    parent: Vm;
}

object TrampolinePageTable: PageTableType {
    parent: Vm;

    state State::Prepared {
        transitions {
            override on Transition::Setup -> State::Ready {
                establishes {
                    trampoline_first_segment_mapping_established();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            trampoline_first_segment_mapping_established();
        }
    }
}

object EarlyPageTable: PageTableType {
    parent: Vm;

    state State::Prepared {
        transitions {
            override on Transition::Setup -> State::Ready {
                establishes {
                    early_kernel_image_mapping_established();
                    early_dtb_four_mib_mapping_established();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            early_kernel_image_mapping_established();
            early_dtb_four_mib_mapping_established();
        }
    }
}
