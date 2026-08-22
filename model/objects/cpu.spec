/* Logical CPU objects and their runtime-signal receive boundary. */

use model::systems::kernel::Kernel;

type CPU {
    cpu_core: true;
    initial_state: State::Online;

    state State::Online {
        actions {
            on Action::OnSyscallExit(status: i32) {
                resumes self.SyscallExitFlowRef.Action::Enter(status);
            }
        }
    }
}

object BootCPU: CPU {
    logical_id: 0;
    parent: Kernel;
}
