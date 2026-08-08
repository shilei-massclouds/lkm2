predicate ready() -> bool;
predicate accepted() -> bool;
object Computer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Go -> State::Ready {
                establishes { ready(); }
            }
        }
    }
    state State::Ready { invariant { accepted(); } }
}
external Human { drives { Computer.Transition::Go; } }
