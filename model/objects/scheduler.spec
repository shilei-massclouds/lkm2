/*
 * Scheduler - per-CPU scheduling interface and the current BootCPU instance.
 *
 * Cpu0Scheduler is the addressable scheduler for the current CPU0-only model
 * slice, not a global Scheduler singleton. Once CpuGroup and CPU objects are
 * modeled, this instance must be materialized as the Scheduler owned by
 * CpuGroup.cpus[0], and every other CPU must own a distinct Scheduler.
 */

type TaskRef;

object BootTaskRef: TaskRef {
}

object KernelInitTaskRef: TaskRef {
}

object Cpu0RunQ: Collection<TaskRef> {
}

type Scheduler {
    initial_state: State::Ready;

    mutable curr: TaskRef = BootTaskRef;
    mutable idle: TaskRef = BootTaskRef;
    runq: Collection<TaskRef> = Cpu0RunQ;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
        actions {
            on Action::SetIdleTask(task_ref: TaskRef) {
                updates {
                    self.idle = task_ref;
                }
            }

            on Action::SetCurrentTask(task_ref: TaskRef) {
                updates {
                    self.curr = task_ref;
                }
            }

            on Action::Schedule {
                panic "impl sched";
            }

            on Action::Enqueue(task_ref: TaskRef) {
                drives Cpu0RunQ.Action::Enqueue(task_ref);
            }
        }
    }
}

object Cpu0Scheduler: Scheduler {
}
