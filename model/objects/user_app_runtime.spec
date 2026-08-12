/* User application runtime protocol implemented by the inference engine. */

type UserAppRuntime {
    user_runtime: true;
    initial_state: State::Base;

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
        actions {
            on Action::Enter;
        }
    }
}
