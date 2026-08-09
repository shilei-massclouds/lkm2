/* BootIdle - idle stage for boot task flow. */

use model::phases::phase::PhaseType;

object BootIdle: PhaseType {
    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
            }
        }
    }
}
