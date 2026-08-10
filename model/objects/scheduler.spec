/*
 * Scheduler - per-CPU scheduling interface and the current BootCPU instance.
 *
 * Cpu0Scheduler is the addressable scheduler for the current CPU0-only model
 * slice, not a global Scheduler singleton. Once CpuGroup and CPU objects are
 * modeled, this instance must be materialized as the Scheduler owned by
 * CpuGroup.cpus[0], and every other CPU must own a distinct Scheduler.
 */

use model::flows::task_flow::BootInitFlow;
use model::phases::phase::PhaseType;

type Scheduler {
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
        actions {
            on Action::Schedule;
        }
    }
}

object Cpu0Scheduler: Scheduler {
    state State::Online {
        actions {
            override on Action::Schedule {
                emits {
                    BootInitFlow.Action::Enter;
                }
            }
        }
    }
}
