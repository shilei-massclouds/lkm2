object Computer: T {
    initial_state: State::Idle;
    state State::Idle {}
}
external Human { drives { Computer.Transition::Go; } }
