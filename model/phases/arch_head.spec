/* ArchHead - architecture-specific kernel entry phase. */

use model::flows::task_flow::BootInitFlow;
use model::objects::vm::Vm;
use model::phases::phase::PhaseType;
use super::start_kernel::StartKernel;

object ArchHead: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                drives {
                    Vm.Transition::Preset;
                    Vm.Transition::Setup;
                }

                // EntryPreludePhase.Preset migration candidates copied from LKM.
                // Keep every candidate disabled until it has been discussed and
                // adapted to the ArchHead action model.
                //
                // depends_on {
                //     Riscv64.state == State::Online;
                //     SbiSpec.state == State::Online;
                //     OpenSBI.state == State::Online;
                //     BootCpuRegisters.state == State::Online;
                //     Lds.state == State::Online;
                //     Config.state == State::Online;
                // }
                //
                // may_change {
                //     BootCpuRegisters.sstatus;
                // }
                //
                // drives {
                //     InterruptStream.Transition::Preset;
                //     KernelImage.Transition::Preset;
                //     KernelImage.Transition::Setup;
                //     BootCurrentCPU.Transition::Preset;
                //     BootCurrentCPU.Transition::Setup;
                //     CpuGroup.Transition::Preset;
                //     BootCurrentCPU.Transition::Enable;
                //     BootTaskEntryBinding.Transition::Preset;
                //     BootInitStack.Transition::Preset;
                //     EventStream.Transition::Preset;
                //     ExceptionStream.Transition::Preset;
                //     EventStream.Transition::Setup;
                //     BootTaskEntryBinding.Transition::Setup;
                //     BootInitStack.Transition::Setup;
                //     Soc.Transition::Preset;
                // }
                //
                // ensures {
                //     kernel_fpu_disabled(BootCpuRegisters.sstatus);
                //     kernel_vector_disabled(BootCpuRegisters.sstatus);
                //     BootTask.state == State::OnCpu;
                // }

                resumes StartKernel.Action::Enter;
            }
        }
    }
}
