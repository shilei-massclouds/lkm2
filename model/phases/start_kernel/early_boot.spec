/* EarlyBoot - start_kernel entry through early-console registration. */

use model::objects::task::BootTask;
use model::objects::dtb_blob::DtbBlob;
use model::objects::early_console::BootCommandLine;
use model::objects::early_console::EarlyConsole;
use model::objects::early_console::SbiCapability;
use model::objects::early_console::SbiConsole;
use model::objects::early_console::early_console_bound_from_registry;
use model::objects::early_console::printk_console_registered;
use model::objects::memblock::MemBlockMemory;
use model::objects::printk::Banner;
use model::objects::printk::Printk;
use model::phases::phase::PhaseType;
use super::arch_head_interrupts_masked;

predicate early_boot_interrupts_enabled() -> bool;

object EarlyBoot: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                depends_on {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    arch_head_interrupts_masked();
                }

                drives {
                    Banner.Transition::Enable;
                    DtbBlob.Transition::Enable;
                    MemBlockMemory.Transition::Enable;
                    SbiCapability.Transition::Enable;
                    EarlyConsole.Transition::Enable;
                }

                ensures {
                    CurrentTaskRef == BootTask;
                    BootTask.state == State::OnCpu;
                    Banner.state == State::Online;
                    DtbBlob.state == State::Online;
                    MemBlockMemory.state == State::Online;
                    SbiCapability.state == State::Online;
                    EarlyConsole.state == State::Online;
                    early_console_bound_from_registry(EarlyConsole, SbiConsole);
                    printk_console_registered(Printk, EarlyConsole);
                    BootCommandLine.has_key("earlycon");
                }
            }
        }
    }
}
