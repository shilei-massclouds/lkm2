/* StartKernel - starting kernel main phase. */

spec early_boot;
spec boot_setup;
spec boot_handoff;
spec boot_idle;

use model::phases::phase::PhaseType;
use self::early_boot::EarlyBoot;
use self::boot_setup::BootSetup;
use self::boot_handoff::BootHandoff;

object StartKernel: PhaseType {
    parent: BootInitFlow;

    state State::Online {
        actions {
            override on Action::Enter {
                resumes EarlyBoot.Action::Enter;
                resumes BootSetup.Action::Enter;
                resumes BootHandoff.Action::Enter;
            }
        }
    }
}
