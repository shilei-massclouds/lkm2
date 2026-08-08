object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        actions { on Action::Refresh {} }
    }
}
external Human { drives { Computer.Action::Refresh; } }
