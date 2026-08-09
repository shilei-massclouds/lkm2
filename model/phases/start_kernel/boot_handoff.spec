/* BootHandoff - handoff stage for boot task flow. */

use model::phases::phase::PhaseType;

object BootHandoff: PhaseType {
    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
            }
        }
    }
}
