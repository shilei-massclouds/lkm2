/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;
use model::phases::phase::PhaseType;

object ArchHead: PhaseType {
    parent: BootInitFlow;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
            }
        }
    }
}
