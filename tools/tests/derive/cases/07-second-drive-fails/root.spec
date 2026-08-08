object First: T {
    initial_state: State::Idle;
    state State::Idle { transitions { on Transition::Work -> State::Done {} } }
    state State::Done {}
}
object Second: T {
    initial_state: State::Idle;
    state State::Idle {}
}
object Parent: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Start -> State::Done {
                drives {
                    First.Transition::Work;
                    Second.Transition::Work;
                }
            }
        }
    }
    state State::Done {}
}
external Human { drives { Parent.Transition::Start; } }
