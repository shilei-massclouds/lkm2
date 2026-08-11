/*
 * Scheduler - per-CPU scheduling policy and the current BootCPU instance.
 *
 * Cpu0Scheduler is the addressable scheduler for the current CPU0-only model
 * slice, not a global Scheduler singleton. Once CpuGroup and CPU objects are
 * modeled, this instance must be materialized as the Scheduler owned by
 * CpuGroup.cpus[0], and every other CPU must own a distinct Scheduler.
 */

use model::objects::task::BootTask;

type Scheduler {
    sched_core: true;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
        actions {
            on Action::Schedule {
                drives CurrentTaskRef.Transition::Suspend;
                selects next_task_ref;
                drives next_task_ref.Transition::Resume;
            }
        }
    }
}

object Cpu0Scheduler: Scheduler {
    idle_task: BootTask;
}
