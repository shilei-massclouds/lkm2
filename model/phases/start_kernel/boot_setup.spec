/* BootSetup - setup stage for boot task flow. */

use model::phases::phase::PhaseType;

object BootSetup: PhaseType {
    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
            }
        }
    }
}
