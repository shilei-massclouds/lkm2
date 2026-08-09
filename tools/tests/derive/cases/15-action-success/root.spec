object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        actions { on Action::Refresh { drives {} } }
    }
}
external Human { drives { Computer.Action::Refresh; } }
