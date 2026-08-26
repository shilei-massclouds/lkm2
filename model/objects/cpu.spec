/* Logical CPU objects and their runtime-signal receive boundary. */

use model::systems::kernel::Kernel;

type CPU {
    cpu_core: true;
    initial_state: State::Online;

    state State::Online {
        actions {
            on Action::OnInterrupt {
                resumes self.InterruptFlowRef.Action::Enter;
            }

            on Action::OnException {
                resumes self.ExceptionFlowRef.Action::Enter;
            }

            on Action::OnSyscallExit(status: i32) {
                resumes self.SyscallExitFlowRef.Action::Enter(status);
            }
        }
    }
}

/* Inference-owned per-CPU interrupt delivery gate. */
type InterruptControl {
    initial_state: State::Online;

    state State::Online {
        actions {
            on Action::MaskAll;
            on Action::ClearPending;
            on Action::Unmask;
        }
    }
}

object BootCPU: CPU {
    logical_id: 0;
    parent: Kernel;
}
