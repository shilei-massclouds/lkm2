object First: T {
    initial_state: State::Idle;
    state State::Idle { transitions { on Transition::Mark -> State::Done {} } }
    state State::Done {}
}
object Second: T {
    initial_state: State::Idle;
    state State::Idle { transitions { on Transition::Mark -> State::Done {} } }
    state State::Done {}
}
object Producer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Start -> State::Done {
                emits {
                    First.Transition::Mark;
                    Second.Transition::Mark;
                }
            }
        }
    }
    state State::Done {}
}
object Observer: T {
    initial_state: State::Idle;
    state State::Idle {
        transitions {
            on Transition::Check -> State::Done {
                depends_on {
                    First == State::Done && Second == State::Done;
                }
            }
        }
    }
    state State::Done {}
}
external Human {
    drives {
        Producer.Transition::Start;
        Observer.Transition::Check;
    }
}
