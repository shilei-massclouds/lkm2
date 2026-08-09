/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;

type ArchHeadPhase;

object ArchHead: ArchHeadPhase {
    parent: BootInitFlow;
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
    }
}
