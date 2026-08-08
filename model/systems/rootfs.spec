/* RootFs - layout for apps and tests. */

type RootFsType;

object RootFs: RootFsType {
    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
            }
        }
    }

    state State::Prepared {
        transitions {
            on Transition::Setup -> State::Ready {
            }
        }
    }

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
    }
}
