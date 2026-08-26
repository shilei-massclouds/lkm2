predicate early_console_bound_from_registry(
    console: EarlyConsoleType,
    backend: EarlyConBackendType,
) -> bool;

type EarlyConBackendType {
    initial_state: State::Offline;
    state State::Offline {}
    state State::Online {}
}

type EarlyConsoleType {
    initial_state: State::Ready;
    mutable backend: EarlyConBackendType;

    state State::Ready {
        transitions {
            on Transition::Enable -> State::Online {}
        }
    }
    state State::Online {}
}

object BootCommandLine: Relation<String, String> {
    initial_state: State::Ready;
    state State::Ready {}
}

object EarlyConTable: Map<String, EarlyConBackendType> {
    initial_state: State::Ready;
    state State::Ready {}
}

object SbiConsole: EarlyConBackendType {
    initial_state: State::Online;
}

object EarlyConsole: EarlyConsoleType {
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
}

object Setup: SetupType {
    initial_state: State::Ready;
    state State::Ready {
        actions {
            override on Action::Install {
                establishes {
                    BootCommandLine.contains("earlycon", "sbi");
                    EarlyConTable.contains("sbi", SbiConsole);
                }
            }
        }
    }
}

type SetupType {
    initial_state: State::Ready;
    state State::Ready {
        actions { on Action::Install; }
    }
}

external Test {
    drives {
        Setup.Action::Install;
        EarlyConsole.Transition::Enable;
    }
}
