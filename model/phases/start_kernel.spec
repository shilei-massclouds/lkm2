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

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                drives {
                    EarlyBoot.Transition::Enable;
                    BootSetup.Transition::Enable;
                    BootHandoff.Transition::Enable;
                    BootIdle.Transition::Enable;
                }
            }
        }
    }
}
