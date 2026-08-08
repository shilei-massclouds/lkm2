object Gate: T {
    initial_state: State::Blocked;
    state State::Blocked {}
    state State::Ready {}
}
object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Go -> State::Ready {
                depends_on { Gate == State::Ready; }
            }
        }
    }
    state State::Ready {}
}
external Human { drives { Computer.Transition::Go; } }
