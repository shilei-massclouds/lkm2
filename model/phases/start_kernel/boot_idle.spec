/* BootIdle - idle stage for boot task flow. */

use model::phases::phase::PhaseType;
use model::objects::scheduler::Cpu0Scheduler;

object BootIdle: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                yields Cpu0Scheduler.Action::Schedule;
                panic "boot idle repeated!";
            }
        }
    }
}
