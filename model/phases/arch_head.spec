/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;
use model::phases::phase::PhaseType;
use super::start_kernel::StartKernel;

object ArchHead: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                resumes StartKernel.Action::Enter;
            }
        }
    }
}
