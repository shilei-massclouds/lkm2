/* BootSetup - setup stage for boot task flow. */

use model::objects::scheduler::Cpu0Scheduler;
use model::objects::task::KernelInitTask;
use model::phases::phase::PhaseType;

object BootSetup: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                drives {
                    Cpu0Scheduler.Transition::Enable;
                    KernelInitTask.Transition::Preset;
                    KernelInitTask.Transition::Setup;
                    KernelInitTask.Transition::Enable;
                }
            }
        }
    }
}
