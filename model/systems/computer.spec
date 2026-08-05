/* Computer is the unique top-level system. */

type ComputerType;

object Computer: ComputerType {
}

/*
use super::riscv64_platform::Riscv64Platform;
use super::opensbi::OpenSBI;
use super::kernel::Kernel;

predicate computer_assembled_from<P, F, K>(platform: P, firmware: F, kernel: K) -> bool;


object Computer: ComputerType {
    initial_state: State::Base;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
                drives {
                    Riscv64Platform.Transition::Preset;
                    OpenSBI.Transition::Preset;
                    Kernel.Transition::Preset;
                }

                ensures {
                    Riscv64Platform.state == State::Prepared;
                    OpenSBI.state == State::Prepared;
                    Kernel.state == State::Prepared;
                }
            }
        }
    }

    state State::Prepared {
        invariant {
            Riscv64Platform.state == State::Prepared;
            OpenSBI.state == State::Prepared;
            Kernel.state == State::Prepared;
        }

        transitions {
            on Transition::Setup -> State::Ready {
                drives {
                    Riscv64Platform.Transition::Setup;
                    OpenSBI.Transition::Setup;
                    Kernel.Transition::Setup;
                }

                ensures {
                    Riscv64Platform.state == State::Ready;
                    OpenSBI.state == State::Ready;
                    Kernel.state == State::Ready;
                    computer_assembled_from(Riscv64Platform, OpenSBI, Kernel);
                }

            }
        }
    }

    state State::Ready {
        invariant {
            Riscv64Platform.state == State::Ready;
            OpenSBI.state == State::Ready;
            Kernel.state == State::Ready;
            computer_assembled_from(Riscv64Platform, OpenSBI, Kernel);
        }

        transitions {
            on Transition::Enable -> State::Online {
                depends_on {
                    computer_assembled_from(Riscv64Platform, OpenSBI, Kernel);
                }

                emits {
                    Riscv64Platform.Transition::Enable;
                }
            }
        }
    }

    state State::Online {
        invariant {
            computer_assembled_from(Riscv64Platform, OpenSBI, Kernel);
        }
    }
}

*/
