object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        invariant { false; }
        actions { on Action::Refresh {} }
    }
}
external Human { drives { Computer.Action::Refresh; } }
