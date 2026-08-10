/* TaskFlow - flow of task. */

use model::objects::task::BootTask;
use model::objects::scheduler::Cpu0Scheduler;
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
                    ArchHead.Action::Enter;
                }
            }
        }
    }
}
