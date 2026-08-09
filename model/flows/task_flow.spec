/* TaskFlow - flow of task. */

use model::objects::task::BootTask;
use model::phases::arch_head::ArchHead;

type TaskFlow {
    continuation: true;
    initial_state: State::Online;

    state State::Online {
        actions {
            on Action::Enter;
        }
    }
}

object BootInitFlow: TaskFlow {
    parent: BootTask;

    state State::Online {
        actions {
            override on Action::Enter {
                drives {
                    ArchHead.Transition::Enable;
                }
            }
        }
    }
}
