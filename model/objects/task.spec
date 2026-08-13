/* Schedulable task carrier and the boot task instance. */

use model::systems::kernel::Kernel;
use model::objects::scheduler::Cpu0Scheduler;

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
            on Transition::Enable -> State::Online {
                drives Cpu0Scheduler.Action::Enqueue;
            }
        }
    }

    state State::Online {
        transitions {
            on Transition::Resume -> State::OnCpu {
                resumes self.ResumeTargetRef.Action::Enter;
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
    initial_state: State::Online;
    parent: Kernel;

    state State::Online {
        transitions {
            override on Transition::Resume -> State::OnCpu {
                resumes self.ResumeTargetRef.Action::Enter;
            }
        }
    }

}

object KernelInitTask: Task {
    parent: Kernel;
}
