object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions { on Transition::Go -> State::Ready {} }
    }
    state State::Ready {}
}
external Human { drives { Computer.Transition::Go; } }
