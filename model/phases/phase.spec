/* Common lifecycle for kernel execution phases. */

type PhaseType {
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable;
        }
    }

    state State::Online {
    }
}
