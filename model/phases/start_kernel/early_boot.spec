/* EarlyBoot - start_kernel entry through global S-mode interrupt enable. */

use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::BootTask;
use model::objects::dtb_blob::DtbBlob;
use model::objects::early_console::BootCommandLine;
use model::objects::early_console::EarlyConsole;
use model::objects::early_console::SbiCapability;
use model::objects::early_console::SbiConsole;
use model::objects::early_console::early_console_bound_from_registry;
use model::objects::early_console::printk_console_registered;
use model::objects::printk::Banner;
use model::objects::printk::Printk;
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
                    Cpu0Scheduler.state == State::Ready;
                    arch_head_interrupts_masked();
                }

                drives {
                    Banner.Transition::Enable;
                    DtbBlob.Transition::Enable;
                    SbiCapability.Transition::Enable;
                    EarlyConsole.Transition::Enable;
                    Cpu0Scheduler.Transition::Enable;
                    CurrentCPU.InterruptControlRef.Action::Unmask;
                }

                ensures {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    Banner.state == State::Online;
                    DtbBlob.state == State::Online;
                    SbiCapability.state == State::Online;
                    EarlyConsole.state == State::Online;
                    early_console_bound_from_registry(EarlyConsole, SbiConsole);
                    printk_console_registered(Printk, EarlyConsole);
                    Cpu0Scheduler.state == State::Online;
                    BootCommandLine.has_key("earlycon");
                }

                establishes {
                    early_boot_interrupts_enabled();
                }
            }
        }
    }
}
