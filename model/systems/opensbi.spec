/* OpenSBI - firmware for Riscv Platform to start kernel. */

use super::kernel::Kernel;

type OpenSBIType;

predicate opensbi_kernel_entry_handoff_ready() -> bool;

object OpenSBI: OpenSBIType {
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
                    opensbi_kernel_entry_handoff_ready();
                }

                emits {
                    Kernel.Transition::Enable;
                }
            }
        }
    }

    state State::Online {
    }
}

/*

predicate opensbi_system_spec_established() -> bool;
predicate opensbi_firmware_constructed() -> bool;
predicate firmware_boot_args_defined<T>(boot_args: T) -> bool;
predicate boot_args_read_only<T>(boot_args: T) -> bool;
predicate opensbi_kernel_entry_satp_zero_handoff() -> bool;

object SbiSpec: PrepareObject {
    initial_state: State::Online;
    parent: OpenSBI;
    source: external_spec::riscv_sbi;

    state State::Online {
        invariant {
            sbi_hsm_available();
        }
    }
}

object BootArgs: PrepareObject {
    initial_state: State::Online;
    parent: OpenSBI;
    source: firmware::boot_abi;

    attrs {
        boot_hartid: HartId;
        dtb_pa: PhysAddr<Dtb>;
    }

    state State::Online {
        invariant {
            attrs_accessible(self);
            firmware_boot_args_defined(self);
            boot_args_read_only(self);
        }
    }
}

object OpenSBI: FirmwareObject {
    initial_state: State::Base;
    parent: Computer;
    source: firmware::opensbi;

    attrs {
        kernel_load_pa: PhysAddr<KernelImage>;
    }

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
                depends_on {
                    SbiSpec.state == State::Online;
                    BootArgs.state == State::Online;
                }

                ensures {
                    opensbi_system_spec_established();
                }
            }
        }
    }

    state State::Prepared {
        invariant {
            SbiSpec.state == State::Online;
            BootArgs.state == State::Online;
            opensbi_system_spec_established();
        }

        transitions {
            on Transition::Setup -> State::Ready {
                ensures {
                    opensbi_system_spec_established();
                    opensbi_firmware_constructed();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            SbiSpec.state == State::Online;
            BootArgs.state == State::Online;
            opensbi_system_spec_established();
            opensbi_firmware_constructed();
        }

        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    Computer.state == State::Online;
                    Riscv64Platform.state == State::Online;
                    SbiSpec.state == State::Online;
                    BootArgs.state == State::Online;
                    LinuxRiscv64KernelBootSpec.state == State::Online;
                    linux_riscv64_kernel_boot_spec_adopted();
                    linux_riscv64_kernel_a0_hartid_required();
                    linux_riscv64_kernel_a1_dtb_pa_required();
                    linux_riscv64_kernel_satp_zero_required();
                    linux_riscv64_kernel_pmd_aligned_load_required(Config.pmd_size);
                    opensbi_system_spec_established();
                    opensbi_firmware_constructed();
                    Kernel.state == State::Ready;
                    Lds.state == State::Online;
                    Config.state == State::Online;
                    kernel_elf_linked_from_config_and_lds(Config, Lds);
                    kernel_boot_artifact_constructed_from_elf(Config, Lds);
                    kernel_boot_artifact_constructed();
                }

                may_change {
                    kernel_load_pa;
                    BootCpuRegisters.a0;
                    BootCpuRegisters.a1;
                    BootCpuRegisters.satp;
                }

                drives {
                    CpuGroup.Transition::Preset;
                }

                ensures {
                    ordered_booting_enabled();
                    primary_hart_only_at_kernel_entry();
                    task_concurrency_closed();
                    firmware_dtb_blob_in_ram_at_kernel_entry(BootArgs.dtb_pa);
                    firmware_dtb_blob_complete_at_kernel_entry(BootArgs.dtb_pa);
                    firmware_dtb_blob_accessible_at_kernel_entry(BootArgs.dtb_pa);
                    BootCpuRegisters.a0 == BootArgs.boot_hartid;
                    BootCpuRegisters.a1 == BootArgs.dtb_pa;
                    BootCpuRegisters.satp == 0;
                    OpenSBI.kernel_load_pa != 0;
                    kernel_image_loaded_for_handoff_at(OpenSBI.kernel_load_pa);
                    kernel_image_load_pmd_aligned(
                        OpenSBI.kernel_load_pa,
                        Config.pmd_size
                    );
                    opensbi_kernel_entry_satp_zero_handoff();
                    task_ref_targets(BootTaskRef, BootTask);
                    task_ref_ready(BootTaskRef);
                    task_execution_authority_is(
                        BootTask,
                        TaskExecutionAuthority::Live
                    );
                    task_breakpoint_state_is(
                        BootTask,
                        TaskBreakpointState::Invalid
                    );
                    CpuGroup.state == State::Prepared;
                    CpuGroup.cpus[0].state == State::Prepared;
                    cpu_group_preset_atomic_publish(CpuGroup, CpuGroup.cpus[0]);
                }

                emits {
                    Kernel.Transition::Enable;
                }
            }
        }
    }

    state State::Online {
        invariant {
            SbiSpec.state == State::Online;
            BootArgs.state == State::Online;
            LinuxRiscv64KernelBootSpec.state == State::Online;
            opensbi_system_spec_established();
            opensbi_firmware_constructed();
            linux_riscv64_kernel_boot_spec_adopted();
            BootCpuRegisters.a0 == BootArgs.boot_hartid;
            BootCpuRegisters.a1 == BootArgs.dtb_pa;
            opensbi_kernel_entry_satp_zero_handoff();
            Lds.state == State::Online;
            Config.state == State::Online;
            OpenSBI.kernel_load_pa != 0;
            kernel_boot_artifact_constructed();
            kernel_image_loaded_for_handoff_at(OpenSBI.kernel_load_pa);
            kernel_image_load_pmd_aligned(
                OpenSBI.kernel_load_pa,
                Config.pmd_size
            );
            ordered_booting_enabled();
            primary_hart_only_at_kernel_entry();
            task_concurrency_closed();
            firmware_dtb_blob_in_ram_at_kernel_entry(BootArgs.dtb_pa);
            firmware_dtb_blob_complete_at_kernel_entry(BootArgs.dtb_pa);
            firmware_dtb_blob_accessible_at_kernel_entry(BootArgs.dtb_pa);
            task_execution_authority_is(
                BootTask,
                TaskExecutionAuthority::Live
            );
            task_breakpoint_state_is(
                BootTask,
                TaskBreakpointState::Invalid
            );
            CpuGroup.state == State::Prepared;
            CpuGroup.cpus[0].state == State::Prepared
                || CpuGroup.cpus[0].state == State::Ready
                || CpuGroup.cpus[0].state == State::Online;
            cpu_group_preset_atomic_publish(CpuGroup, CpuGroup.cpus[0]);
        }
    }
}
*/
