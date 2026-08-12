/* UserRunPhase - prepare KernelInitFlow's runtime and enter user execution. */

use model::phases::phase::PhaseType;

object UserRunPhase: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                drives CurrentUserAppRuntimeRef.Transition::Preset;
                drives CurrentUserAppRuntimeRef.Transition::Setup;
                drives CurrentUserAppRuntimeRef.Transition::Enable;
                yields CurrentUserAppRuntimeRef.Action::Enter;
            }
        }
    }
}
