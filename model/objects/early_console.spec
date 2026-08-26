/* Early console selection from the boot command line and backend registry. */

use model::systems::kernel::Kernel;

predicate early_console_bound_from_registry(
    console: EarlyConsoleType,
    backend: EarlyConsoleBackendType,
) -> bool;

type EarlyConsoleBackendType {
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

type EarlyConsoleType {
    initial_state: State::Ready;
    mutable backend: EarlyConsoleBackendType;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {
            }
        }
    }

    state State::Online {
    }
}

object BootCommandLine: Relation<String, String> {
    parent: Kernel;
    initial_state: State::Ready;

    state State::Ready {
    }
}

object EarlyConTable: Map<String, EarlyConsoleBackendType> {
    parent: Kernel;
    initial_state: State::Ready;

    state State::Ready {
    }
}

object SbiConsole: EarlyConsoleBackendType {
    parent: Kernel;
    initial_state: State::Online;
}

object EarlyConsole: EarlyConsoleType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    BootCommandLine.state == State::Ready;
                    EarlyConTable.state == State::Ready;
                }

                binds {
                    value := BootCommandLine.unique_value("earlycon");
                    backend := EarlyConTable.lookup(value);
                }

                depends_on {
                    backend.state == State::Online;
                }

                updates {
                    self.backend = backend;
                }

                establishes {
                    early_console_bound_from_registry(self, backend);
                }
            }
        }
    }

    state State::Online {
        invariant {
            early_console_bound_from_registry(self, self.backend);
        }
    }
}
