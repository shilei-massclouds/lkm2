/* BootIdle - idle stage for boot task flow. */

use model::phases::phase::PhaseType;

object BootIdle: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                emits {
                    BootIdle.Action::Enter;
                }
            }
        }
    }
}
