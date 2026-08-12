/* UserRunPhase - prepare KernelInitFlow's runtime and enter user execution. */

use model::objects::user_app_runtime::KernelInitUserAppRuntime;
use model::phases::phase::PhaseType;

object UserRunPhase: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                drives KernelInitUserAppRuntime.Transition::Preset;
                drives KernelInitUserAppRuntime.Transition::Setup;
                drives KernelInitUserAppRuntime.Transition::Enable;
                yields KernelInitUserAppRuntime.Action::Enter;
            }
        }
    }
}
