/* EarlyBoot - start_kernel entry through scheduler and interrupt handoff. */

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
use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::BootTask;
use model::objects::vm::SwapperPageTable;
use model::objects::vm::swapper_fixmap_established;
use model::objects::vm::swapper_linear_map_established;
use model::objects::vm::swapper_kernel_map_established;
use model::objects::vm::swapper_fixmap_cleared;
use model::objects::vm::swapper_satp_switched;
use model::objects::vm::swapper_tlb_flush_completed;
use model::objects::vm::swapper_late_paging_mode_selected;
use model::objects::memblock::MemBlock;
use model::phases::phase::PhaseType;
use super::arch_head_interrupts_masked;

predicate early_boot_interrupts_enabled() -> bool;

object EarlyBoot: PhaseType {
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
                    MemBlockReserved.Transition::Enable;
                    MemBlock.Transition::Enable;
                    SwapperPageTable.Transition::Enable;
                    Cpu0Scheduler.Transition::Enable;
                    CurrentCPU.InterruptControlRef.Action::Unmask;
                }

                ensures {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    Banner.state == State::Online;
                    DtbBlob.state == State::Online;
                    MemBlockMemory.state == State::Online;
                    SbiCapability.state == State::Online;
                    EarlyConsole.state == State::Online;
                    MemBlockReserved.state == State::Online;
                    MemBlock.state == State::Online;
                    SwapperPageTable.state == State::Online;
                    Cpu0Scheduler.state == State::Online;
                    early_console_bound_from_registry(EarlyConsole, SbiConsole);
                    printk_console_registered(Printk, EarlyConsole);
                    BootCommandLine.has_key("earlycon");
                    swapper_fixmap_established();
                    swapper_linear_map_established();
                    swapper_kernel_map_established();
                    swapper_fixmap_cleared();
                    swapper_satp_switched();
                    swapper_tlb_flush_completed();
                    swapper_late_paging_mode_selected();
                }

                establishes {
                    early_boot_interrupts_enabled();
                }
            }
        }
    }
}
