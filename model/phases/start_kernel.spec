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

predicate arch_head_early_cpu_state_ready() -> bool;
predicate arch_head_kernel_image_execution_environment_ready() -> bool;
predicate arch_head_boot_hart_identity_ready() -> bool;
predicate arch_head_early_address_space_active() -> bool;
predicate arch_head_virtual_boot_task_stack_context_ready() -> bool;
predicate arch_head_trap_context_ready() -> bool;
predicate arch_head_soc_early_init_complete() -> bool;

object StartKernel: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    Vm.state == State::Ready;
                    arch_head_early_cpu_state_ready();
                    arch_head_kernel_image_execution_environment_ready();
                    arch_head_boot_hart_identity_ready();
                    arch_head_early_address_space_active();
                    arch_head_virtual_boot_task_stack_context_ready();
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
