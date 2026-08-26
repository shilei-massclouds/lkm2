/* BootSetup - initialize KernelInitTask after the EarlyBoot handoff. */

use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::BootTask;
use model::objects::task::KernelInitTask;
use model::phases::phase::PhaseType;
use super::early_boot::early_boot_interrupts_enabled;

object BootSetup: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    Cpu0Scheduler.state == State::Online;
                    early_boot_interrupts_enabled();
                }

                drives {
                    KernelInitTask.Transition::Preset;
                    KernelInitTask.Transition::Setup;
                    KernelInitTask.Transition::Enable;
                }
            }
        }
    }
}
