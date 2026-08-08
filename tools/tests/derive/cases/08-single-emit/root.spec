object Logger: T {
    initial_state: State::Idle;
    state State::Idle { transitions { on Transition::Log -> State::Done {} } }
    state State::Done {}
}
object Producer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Start -> State::Done {
                emits { Logger.Transition::Log; }
            }
        }
    }
    state State::Done {}
}
external Human { drives { Producer.Transition::Start; } }
