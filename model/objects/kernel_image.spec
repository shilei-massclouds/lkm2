/* Loaded kernel image and the early BSS clearing boundary. */

use model::systems::kernel::Kernel;

predicate kernel_image_bss_cleared() -> bool;

type KernelImageType;

object KernelImage: KernelImageType {
    parent: Kernel;
    initial_state: State::Loaded;

    state State::Loaded {
        transitions {
            on Transition::ClearBss -> State::Ready {
                establishes {
                    kernel_image_bss_cleared();
                }
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
        invariant {
            kernel_image_bss_cleared();
        }
    }
}
