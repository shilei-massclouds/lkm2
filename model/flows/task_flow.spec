/* TaskFlow - flow of task. */

use model::objects::task::BootTask;
use model::objects::task::KernelInitTask;
use model::objects::cpu::BootCPU;
use model::objects::cpu::CPU;
use model::phases::arch_head::ArchHead;
use model::phases::kernel_init::KernelInitPhase;
use model::phases::user_run::UserRunPhase;

type TaskFlow {
    continuation: true;
    mutable cpu_ref: CPU;
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
                updates {
                    self.cpu_ref = BootCPU;
                }
                resumes ArchHead.Action::Enter;
            }
        }
    }
}

object KernelInitFlow: TaskFlow {
    parent: KernelInitTask;

    state State::Online {
        actions {
            override on Action::Enter {
                resumes KernelInitPhase.Action::Enter;
                resumes UserRunPhase.Action::Enter;
            }
        }
    }
}
