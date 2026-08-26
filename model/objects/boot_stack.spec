/* BootTask-owned physical and early-virtual stack lifecycle. */

use model::objects::task::BootTask;

type BootStackType {
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
    }
}

object BootStack: BootStackType {
    parent: BootTask;
}
