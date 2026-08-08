predicate ready() -> bool;
object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Go -> State::Ready {
                establishes { ready(); }
            }
        }
    }
    state State::Ready { invariant { ready(); } }
}
external Human { drives { Computer.Transition::Go; } }
