/* Early console selection from the boot command line and backend registry. */

use model::systems::kernel::Kernel;
use model::objects::kernel_image::KernelImage;
use model::objects::printk::Printk;
use model::objects::printk::PrintkType;

predicate early_console_bound_from_registry(
    console: ConsoleType,
    backend: EarlyConsoleBackendType,
) -> bool;
predicate printk_console_registered(
    printk: PrintkType,
    console: ConsoleType,
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

type ConsoleType {
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

object BootCommandLine: Relation<String, String> {
    parent: Kernel;
    initial_state: State::Ready;

    state State::Ready {
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

object EarlyConTable: Map<String, EarlyConsoleBackendType> {
    parent: KernelImage;
    initial_state: State::Base;

    state State::Base {
        transitions {
            on Transition::Link -> State::Ready {
                establishes {
                    EarlyConTable.contains("sbi", SbiConsole);
                }
            }
        }
    }

    state State::Ready {
        invariant {
            EarlyConTable.contains("sbi", SbiConsole);
        }
    }
}

object SbiConsole: EarlyConsoleBackendType {
    parent: Kernel;
    initial_state: State::Online;
}

object EarlyConsole: ConsoleType {
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
                    printk_console_registered(Printk, self);
                }
            }
        }
    }

    state State::Online {
        invariant {
            early_console_bound_from_registry(self, self.backend);
            printk_console_registered(Printk, self);
        }
    }
}
