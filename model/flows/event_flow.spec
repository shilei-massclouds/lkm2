/* Inference-owned CPU event flows with a shared handled contract. */

predicate event_flow_handled<F>(flow: F) -> bool;

type EventFlow {
    event_flow: true;
    initial_state: State::Online;

    state State::Online {
    }
}

type InterruptFlow: EventFlow {
    continuation: true;

    state State::Online {
        actions {
            on Action::Enter {
                establishes {
                    event_flow_handled(self);
                }
            }
        }
    }
}

type ExceptionFlow: EventFlow {
    continuation: true;

    state State::Online {
        actions {
            on Action::Enter {
                establishes {
                    event_flow_handled(self);
                }
            }
        }
    }
}
