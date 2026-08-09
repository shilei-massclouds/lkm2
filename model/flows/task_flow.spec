/* TaskFlow - flow of task. */

use model::objects::task::BootTask;

type TaskFlow {
    initial_state: State::Online;

    state State::Online {
        actions {
            on Action::Enter {
                drives {
                    /* Resume from registers-context */
                }
            }
        }
    }
}

object BootInitFlow: TaskFlow {
    parent: BootTask;
}
