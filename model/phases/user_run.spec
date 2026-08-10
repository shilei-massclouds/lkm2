/* UserRunPhase - enter user execution from KernelInitFlow. */

use model::objects::scheduler::Cpu0Scheduler;
use model::phases::phase::PhaseType;

object UserRunPhase: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                yields Cpu0Scheduler.Action::Schedule;
            }
        }
    }
}
