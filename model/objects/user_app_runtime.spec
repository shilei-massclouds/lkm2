/* User application runtime owned by KernelInitFlow. */

use model::flows::task_flow::KernelInitFlow;

type UserAppRuntime {
    initial_state: State::Base;

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
        actions {
            on Action::Enter;
        }
    }
}

object KernelInitUserAppRuntime: UserAppRuntime {
    parent: KernelInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                drives {
                }
            }
        }
    }
}
