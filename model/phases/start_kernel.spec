/* StartKernel - starting kernel main phase. */

spec early_boot;
spec boot_setup;
spec boot_handoff;
spec boot_idle;

use model::phases::phase::PhaseType;
use self::early_boot::EarlyBoot;
use self::boot_setup::BootSetup;
use self::boot_handoff::BootHandoff;
use self::boot_idle::BootIdle;
use model::objects::vm::Vm;
use model::objects::kernel_image::KernelImage;
use model::objects::boot_stack::BootStack;
use model::objects::task::BootTask;

predicate arch_head_interrupts_masked() -> bool;
predicate arch_head_entry_pending_interrupts_cleared() -> bool;
predicate arch_head_virtual_global_pointer_ready() -> bool;
predicate arch_head_fpu_disabled() -> bool;
predicate arch_head_vector_disabled() -> bool;
predicate arch_head_boot_hart_id_recorded() -> bool;
predicate arch_head_current_task_reset() -> bool;
predicate arch_head_early_address_space_active() -> bool;
predicate arch_head_kernel_image_accessible() -> bool;
predicate arch_head_firmware_fdt_accessible() -> bool;
predicate arch_head_trap_context_ready() -> bool;
predicate arch_head_soc_early_init_complete() -> bool;

object StartKernel: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    CurrentTaskRef == BootTask;
                    KernelImage.state == State::Ready;
                    BootStack.state == State::Ready;
                    Vm.state == State::Ready;
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

                resumes EarlyBoot.Action::Enter;
                resumes BootSetup.Action::Enter;
                resumes BootHandoff.Action::Enter;
                resumes BootIdle.Action::Enter;
            }
        }
    }
}
