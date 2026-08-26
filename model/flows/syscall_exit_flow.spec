/* Inference-owned CPU EventFlow for the non-returning exit syscall. */

use model::flows::event_flow::EventFlow;

type SyscallExitFlow: EventFlow {
    continuation: true;
    syscall_exit_flow: true;

    state State::Online {
        actions {
            on Action::Enter(status: i32) {
                drives CurrentTaskRef.Action::Exit(status);
            }
        }
    }
}
