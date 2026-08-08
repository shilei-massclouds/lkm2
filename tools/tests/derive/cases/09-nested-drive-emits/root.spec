object Logger: T {
    initial_state: State::Idle;
    state State::Idle { transitions { on Transition::Log -> State::Done {} } }
    state State::Done {}
}
object Child: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Work -> State::Done {
                emits { Logger.Transition::Log; }
            }
        }
    }
    state State::Done {}
}
object Parent: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Start -> State::Done {
                drives { Child.Transition::Work; }
                ensures { Logger == State::Done; }
            }
        }
    }
    state State::Done {}
}
external Human { drives { Parent.Transition::Start; } }
