/*
 * Scheduler - per-CPU scheduling interface and the current BootCPU instance.
 *
 * Cpu0Scheduler is the addressable scheduler for the current CPU0-only model
 * slice, not a global Scheduler singleton. Once CpuGroup and CPU objects are
 * modeled, this instance must be materialized as the Scheduler owned by
 * CpuGroup.cpus[0], and every other CPU must own a distinct Scheduler.
 */

use model::flows::task_flow::BootInitFlow;
use model::flows::task_flow::KernelInitFlow;
use model::objects::task::BootTask;
use model::objects::task::KernelInitTask;

type Scheduler {
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::BootTaskRunning {
            }
        }
    }

    state State::BootTaskRunning {
        transitions {
            on Transition::SwitchToKernelInitTask -> State::KernelInitTaskRunning {
            }
        }

        actions {
            on Action::Schedule;
        }
    }

    state State::KernelInitTaskRunning {
        transitions {
            on Transition::SwitchToBootTask -> State::BootTaskRunning {
            }
        }

        actions {
            on Action::Schedule;
        }
    }
}

object Cpu0Scheduler: Scheduler {
    state State::BootTaskRunning {
        actions {
            override on Action::Schedule {
                drives {
                    BootTask.Transition::Suspend;
                    KernelInitTask.Transition::Resume;
                    Cpu0Scheduler.Transition::SwitchToKernelInitTask;
                }

                emits {
                    KernelInitFlow.Action::Enter;
                }
            }
        }
    }

    state State::KernelInitTaskRunning {
        actions {
            override on Action::Schedule {
                drives {
                    KernelInitTask.Transition::Suspend;
                    BootTask.Transition::Resume;
                    Cpu0Scheduler.Transition::SwitchToBootTask;
                }

                emits {
                    BootInitFlow.Action::Enter;
                }
            }
        }
    }
}
