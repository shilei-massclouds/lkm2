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
predicate sbi_dbcn_available(capability: SbiCapabilityType) -> bool;
predicate sbi_v01_console_available(capability: SbiCapabilityType) -> bool;
predicate sbi_console_uses_dbcn(console: EarlyConsoleBackendType) -> bool;
predicate sbi_console_uses_v01(console: EarlyConsoleBackendType) -> bool;

type SbiCapabilityType {
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

object BootCommandLine: Relation<String, String> {
    parent: Kernel;
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

object SbiCapability: SbiCapabilityType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                establishes {
                    sbi_dbcn_available(self);
                }
            }
        }
    }
}

object SbiConsole: EarlyConsoleBackendType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                depends_on {
                    SbiCapability.state == State::Online;
                    sbi_dbcn_available(SbiCapability) ||
                        sbi_v01_console_available(SbiCapability);
                }

                establishes {
                    sbi_console_uses_dbcn(self);
                }
            }
        }
    }

    state State::Online {
        invariant {
            sbi_console_uses_dbcn(self) || sbi_console_uses_v01(self);
            !(sbi_console_uses_dbcn(self) && sbi_console_uses_v01(self));
            !sbi_console_uses_dbcn(self) ||
                sbi_dbcn_available(SbiCapability);
            !sbi_console_uses_v01(self) ||
                (sbi_v01_console_available(SbiCapability) &&
                 !sbi_dbcn_available(SbiCapability));
        }
    }
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

                drives SbiConsole.Transition::Enable;

                ensures {
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
