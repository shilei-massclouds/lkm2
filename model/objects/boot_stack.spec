/* BootTask-owned physical and early-virtual stack lifecycle. */

use model::objects::task::BootTask;

predicate boot_stack_physical_ready() -> bool;
predicate boot_stack_early_virtual_ready() -> bool;

type BootStackType {
    initial_state: State::Base;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
                establishes {
                    boot_stack_physical_ready();
                }
            }
        }
    }

    state State::Prepared {
        invariant {
            boot_stack_physical_ready();
        }

        transitions {
            on Transition::Setup -> State::Ready {
                establishes {
                    boot_stack_early_virtual_ready();
                }
            }
        }
    }

    state State::Ready {
        invariant {
            boot_stack_physical_ready();
            boot_stack_early_virtual_ready();
        }
    }
}

object BootStack: BootStackType {
    parent: BootTask;
}
