object Gate: T {
    initial_state: State::Ready;
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
