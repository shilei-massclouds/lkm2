/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;
use model::objects::cpu::BootCPU;
use model::objects::task::BootTask;
use model::objects::kernel_image::KernelImage;
use model::objects::boot_stack::BootStack;
use model::objects::vm::Vm;
use model::phases::phase::PhaseType;
use model::systems::opensbi::OpenSBI;
use model::systems::opensbi::opensbi_kernel_entry_handoff_ready;
use super::start_kernel::arch_head_boot_hart_id_recorded;
use super::start_kernel::arch_head_current_task_reset;
use super::start_kernel::arch_head_early_address_space_active;
use super::start_kernel::arch_head_entry_pending_interrupts_cleared;
use super::start_kernel::arch_head_firmware_fdt_accessible;
use super::start_kernel::arch_head_fpu_disabled;
use super::start_kernel::arch_head_interrupts_masked;
use super::start_kernel::arch_head_kernel_image_accessible;
use super::start_kernel::arch_head_soc_early_init_complete;
use super::start_kernel::arch_head_trap_context_ready;
use super::start_kernel::arch_head_vector_disabled;
use super::start_kernel::arch_head_virtual_global_pointer_ready;
use super::start_kernel::StartKernel;

object ArchHead: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    OpenSBI.state == State::Online;
                    CurrentCPU == BootCPU;
                    BootCPU.state == State::Online;
                    BootTask.state == State::OnCpu;
                    KernelImage.state == State::Loaded;
                    BootStack.state == State::Base;
                    Vm.state == State::Base;
                    opensbi_kernel_entry_handoff_ready();
                }

                drives {
                    CurrentCPU.InterruptControlRef.Action::MaskAll;
                    CurrentCPU.InterruptControlRef.Action::ClearPending;
                    KernelImage.Action::ClearBss;
                    BootTask.Action::ResetCurrent;
                    BootStack.Transition::Preset;
                    Vm.Transition::Preset;
                    Vm.Transition::Setup;
                    BootStack.Transition::Setup;
                }

                ensures {
                    CurrentTaskRef == BootTask;
                    KernelImage.state == State::Ready;
                    BootStack.state == State::Ready;
                    Vm.state == State::Ready;
                }

                establishes {
                    arch_head_interrupts_masked();
                    arch_head_entry_pending_interrupts_cleared();
                    arch_head_virtual_global_pointer_ready();
                    arch_head_fpu_disabled();
                    arch_head_vector_disabled();
                    arch_head_boot_hart_id_recorded();
                    arch_head_current_task_reset();
                    arch_head_early_address_space_active();
                    arch_head_kernel_image_accessible();
                    arch_head_firmware_fdt_accessible();
                    arch_head_trap_context_ready();
                    arch_head_soc_early_init_complete();
                }

                resumes StartKernel.Action::Enter;
            }
        }
    }
}
