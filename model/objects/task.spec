/* Schedulable task carrier and the boot task instance. */

use model::systems::kernel::Kernel;

type Task;

object BootTask: Task {
    initial_state: State::OnCpu;
    parent: Kernel;

    state State::OnCpu {
        transitions {
            on Transition::Suspend -> State::Online {
            }
        }
    }

    state State::Online {
        transitions {
            on Transition::Dispatch -> State::OnCpu {
            }
        }
    }
}
