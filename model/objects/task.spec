/* Schedulable task carrier and the boot task instance. */

use model::systems::kernel::Kernel;
use model::objects::scheduler::Cpu0Scheduler;
use model::objects::scheduler::TaskRef;

type Task {
    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
            }
        }
    }

    state State::Prepared {
        transitions {
            on Transition::Setup -> State::Ready {
            }
        }
    }

    state State::Ready {
        transitions {
            on Transition::Enable(task_ref: TaskRef) -> State::Online {
                drives Cpu0Scheduler.Action::Enqueue(task_ref);
            }
        }
    }

    state State::Online {
        transitions {
            on Transition::Resume -> State::OnCpu {
            }
        }
    }

    state State::OnCpu {
        transitions {
            on Transition::Suspend -> State::Online {
            }
        }
    }
}

object BootTask: Task {
    initial_state: State::OnCpu;
    parent: Kernel;
}

object KernelInitTask: Task {
    parent: Kernel;
}
