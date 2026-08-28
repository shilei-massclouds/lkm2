/* Firmware DTB input observed into the boot command line. */

use model::systems::kernel::Kernel;
use model::objects::early_console::BootCommandLine;

type DtbBlobType {
    initial_state: State::Ready;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
    }
}

object DtbBlob: DtbBlobType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    ChosenBootArgs.state == State::Ready;
                    BootCommandLine.state == State::Ready;
                }

                binds {
                    value := ChosenBootArgs.unique_value("earlycon");
                }

                establishes {
                    BootCommandLine.contains("earlycon", value);
                }

                ensures {
                    ChosenBootArgs.contains("earlycon", value);
                    BootCommandLine.contains("earlycon", value);
                }
            }
        }
    }
}

object ChosenBootArgs: Relation<String, String> {
    parent: DtbBlob;
    initial_state: State::Ready;

    state State::Ready {
    }
}
