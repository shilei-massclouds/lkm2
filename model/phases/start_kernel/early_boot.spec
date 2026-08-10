/* EarlyBoot - early stage for boot task flow. */

use model::phases::phase::PhaseType;

object EarlyBoot: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                print "here";
            }
        }
    }
}
