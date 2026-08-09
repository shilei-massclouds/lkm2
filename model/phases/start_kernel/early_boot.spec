/* EarlyBoot - early stage for boot task flow. */

use model::phases::phase::PhaseType;

object EarlyBoot: PhaseType {
    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
            }
        }
    }
}
