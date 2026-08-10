/* KernelInitPhase - kernel initialization work run by KernelInitFlow. */

use model::phases::phase::PhaseType;

object KernelInitPhase: PhaseType {
    state State::Online {
        actions {
            override on Action::Enter {
                print "kernel init";
            }
        }
    }
}
