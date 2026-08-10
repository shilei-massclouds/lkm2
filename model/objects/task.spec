/* Schedulable task carrier and the boot task instance. */

use model::systems::kernel::Kernel;

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
