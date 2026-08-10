/* Common lifecycle for kernel execution phases. */

type PhaseType {
    initial_state: State::Online;
    continuation: true;

    state State::Online {
        actions {
            on Action::Enter;
        }
    }
}
