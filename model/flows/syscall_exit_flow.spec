/* Inference-owned CPU EventFlow for the non-returning exit syscall. */

type SyscallExitFlow {
    continuation: true;
    syscall_exit_flow: true;
    initial_state: State::Online;

    state State::Online {
        actions {
            on Action::Enter(status: i32) {
                drives CurrentTaskRef.Action::Exit(status);
            }
        }
    }
}
