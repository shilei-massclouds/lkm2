/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;
use model::phases::phase::PhaseType;
use super::start_kernel::StartKernel;

object ArchHead: PhaseType {
    parent: BootInitFlow;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                drives {
                    StartKernel.Transition::Enable;
                }
            }
        }
    }
}
