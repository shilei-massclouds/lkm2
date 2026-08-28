/* PagingInit - setup_bootmem completion followed by final page-table setup. */

use model::objects::dtb_blob::DtbBlob;
use model::objects::early_console::EarlyConsole;
use model::objects::early_console::SbiCapability;
use model::objects::kernel_image::KernelImage;
use model::objects::memblock::MemBlock;
use model::objects::memblock::MemBlockMemory;
use model::objects::memblock::MemBlockReserved;
use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::BootTask;
use model::objects::vm::FinalPageTable;
use model::objects::vm::Vm;
use model::phases::phase::PhaseType;
use super::arch_head_interrupts_masked;
use super::early_boot::early_boot_interrupts_enabled;

object PagingInit: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
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
                    Cpu0Scheduler.state == State::Ready;
                    Vm.state == State::Ready;
                    FinalPageTable.state == State::Ready;
                    arch_head_interrupts_masked();
                }

                drives {
                    MemBlockReserved.Transition::Enable;
                    MemBlock.Transition::Enable;
                    FinalPageTable.Transition::Enable;
                    Cpu0Scheduler.Transition::Enable;
                    CurrentCPU.InterruptControlRef.Action::Unmask;
                }

                ensures {
                    MemBlockMemory.state == State::Online;
                    MemBlockReserved.state == State::Online;
                    MemBlock.state == State::Online;
                    FinalPageTable.state == State::Online;
                    Cpu0Scheduler.state == State::Online;
                }

                establishes {
                    early_boot_interrupts_enabled();
                }
            }
        }
    }
}
