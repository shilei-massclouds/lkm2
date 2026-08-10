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

object StartKernel: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                drives {
                    EarlyBoot.Action::Enter;
                    BootSetup.Action::Enter;
                    BootHandoff.Action::Enter;
                    BootIdle.Action::Enter;
                }
            }
        }
    }
}
