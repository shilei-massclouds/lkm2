/* UserRunPhase - prepare KernelInitFlow's runtime and enter user execution. */

use model::phases::phase::PhaseType;

object UserRunPhase: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                drives CurrentTaskRef.UserAppRuntimeRef.Transition::Preset;
                drives CurrentTaskRef.UserAppRuntimeRef.Transition::Setup;
                drives CurrentTaskRef.UserAppRuntimeRef.Transition::Enable;
                yields CurrentTaskRef.UserAppRuntimeRef.Action::Enter;
            }
        }
    }
}
