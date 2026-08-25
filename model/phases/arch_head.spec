/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;
use model::objects::cpu::BootCPU;
use model::objects::task::BootTask;
use model::objects::vm::Vm;
use model::phases::phase::PhaseType;
use model::systems::opensbi::OpenSBI;
use model::systems::opensbi::opensbi_kernel_entry_handoff_ready;
use super::start_kernel::arch_head_boot_hart_identity_ready;
use super::start_kernel::arch_head_early_address_space_active;
use super::start_kernel::arch_head_early_cpu_state_ready;
use super::start_kernel::arch_head_kernel_image_execution_environment_ready;
use super::start_kernel::arch_head_soc_early_init_complete;
use super::start_kernel::arch_head_trap_context_ready;
use super::start_kernel::arch_head_virtual_boot_task_stack_context_ready;
use super::start_kernel::StartKernel;

object ArchHead: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    OpenSBI.state == State::Online;
                    BootCPU.state == State::Online;
                    BootTask.state == State::OnCpu;
                    Vm.state == State::Base;
                    opensbi_kernel_entry_handoff_ready();
                }

                drives {
                    Vm.Transition::Preset;
                    Vm.Transition::Setup;
                }

                ensures {
                    Vm.state == State::Ready;
                }

                establishes {
                    arch_head_early_cpu_state_ready();
                    arch_head_kernel_image_execution_environment_ready();
                    arch_head_boot_hart_identity_ready();
                    arch_head_early_address_space_active();
                    arch_head_virtual_boot_task_stack_context_ready();
                    arch_head_trap_context_ready();
                    arch_head_soc_early_init_complete();
                }

                resumes StartKernel.Action::Enter;
            }
        }
    }
}
