/* EarlyBoot - start_kernel entry through global S-mode interrupt enable. */

use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::BootTask;
use model::objects::early_console::EarlyConsole;
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
                    EarlyConsole.Transition::Enable;
                    Cpu0Scheduler.Transition::Enable;
                    CurrentCPU.InterruptControlRef.Action::Unmask;
                }

                ensures {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    EarlyConsole.state == State::Online;
                    Cpu0Scheduler.state == State::Online;
                }

                establishes {
                    early_boot_interrupts_enabled();
                }
            }
        }
    }
}
