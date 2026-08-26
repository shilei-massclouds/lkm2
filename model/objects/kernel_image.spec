/* Loaded kernel image and the early BSS clearing boundary. */

use model::systems::kernel::Kernel;

type KernelImageType;

object KernelImage: KernelImageType {
    parent: Kernel;
    initial_state: State::Loaded;

    state State::Loaded {
        transitions {
            on Transition::ClearBss -> State::Ready {
            }
        }

        actions {
            on Action::ClearBss {
                drives KernelImage.Transition::ClearBss;
                ensures {
                    KernelImage.state == State::Ready;
                }
            }
        }
    }

    state State::Ready {
    }
}
