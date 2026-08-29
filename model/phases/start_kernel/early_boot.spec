/* EarlyBoot - start_kernel entry through the M0/M1 handoff boundary. */

use model::objects::dtb_blob::DtbBlob;
use model::objects::early_console::BootCommandLine;
use model::objects::early_console::EarlyConsole;
use model::objects::early_console::SbiCapability;
use model::objects::early_console::SbiConsole;
use model::objects::early_console::early_console_bound_from_registry;
use model::objects::early_console::printk_console_registered;
use model::objects::memblock::MemBlockMemory;
use model::objects::memblock::MemBlockReserved;
use model::objects::printk::Banner;
use model::objects::printk::Printk;
use model::objects::kernel_image::KernelImage;
use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::BootTask;
use model::objects::vm::Vm;
use model::objects::vm::SwapperPageTable;
use model::objects::vm::swapper_fixmap_established;
use model::objects::vm::swapper_linear_map_established;
use model::objects::vm::swapper_kernel_map_established;
use model::objects::vm::swapper_fixmap_cleared;
use model::objects::vm::swapper_satp_switched;
use model::objects::vm::swapper_tlb_flush_completed;
use model::objects::vm::swapper_late_paging_mode_selected;
use model::objects::memblock::MemBlock;
use super::arch_head_interrupts_masked;

predicate early_boot_interrupts_enabled() -> bool;

type EarlyBootType {
    initial_state: State::Online;
    continuation: true;

    state State::Online {
        actions {
            on Action::Enter;
            on Action::SetupBootmem;
            on Action::SetupVmFinal;
            on Action::Complete;
        }
    }
}

object EarlyBoot: EarlyBootType {
    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    arch_head_interrupts_masked();
                }

                drives {
                    Banner.Transition::Enable;
                    DtbBlob.Transition::Enable;
                    MemBlockMemory.Transition::Enable;
                    SbiCapability.Transition::Enable;
                    EarlyConsole.Transition::Enable;
                }

                ensures {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    Banner.state == State::Online;
                    DtbBlob.state == State::Online;
                    MemBlockMemory.state == State::Online;
                    SbiCapability.state == State::Online;
                    EarlyConsole.state == State::Online;
                    early_console_bound_from_registry(EarlyConsole, SbiConsole);
                    printk_console_registered(Printk, EarlyConsole);
                    BootCommandLine.has_key("earlycon");
                }
            }

            override on Action::SetupBootmem {
                depends_on {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    DtbBlob.state == State::Online;
                    MemBlock.state == State::Ready;
                    MemBlockMemory.state == State::Online;
                    MemBlockReserved.state == State::Ready;
                    KernelImage.state == State::Ready;
                    SbiCapability.state == State::Online;
                    EarlyConsole.state == State::Online;
                    arch_head_interrupts_masked();
                }

                drives {
                    MemBlockReserved.Transition::Enable;
                    MemBlock.Transition::Enable;
                }

                ensures {
                    MemBlockMemory.state == State::Online;
                    MemBlockReserved.state == State::Online;
                    MemBlock.state == State::Online;
                }
            }

            override on Action::SetupVmFinal {
                depends_on {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    Vm.state == State::Ready;
                    MemBlock.state == State::Online;
                    SwapperPageTable.state == State::Ready;
                    arch_head_interrupts_masked();
                }

                drives {
                    SwapperPageTable.Transition::Enable;
                }

                ensures {
                    SwapperPageTable.state == State::Online;
                }
            }

            override on Action::Complete {
                depends_on {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    MemBlock.state == State::Online;
                    SwapperPageTable.state == State::Online;
                    Cpu0Scheduler.state == State::Ready;
                    arch_head_interrupts_masked();
                    swapper_fixmap_established();
                    swapper_linear_map_established();
                    swapper_kernel_map_established();
                    swapper_fixmap_cleared();
                    swapper_satp_switched();
                    swapper_tlb_flush_completed();
                    swapper_late_paging_mode_selected();
                }

                drives {
                    Cpu0Scheduler.Transition::Enable;
                    CurrentCPU.InterruptControlRef.Action::Unmask;
                }

                establishes {
                    early_boot_interrupts_enabled();
                }
            }
        }
    }
}
