/* Human is an external source of signal. */

use super::computer::Computer;

external Human {
    drives {
        /* Define the specification and design the computer system. */
        Computer.Transition::Preset;
        /* Build and integrate the hardware, firmware, kernel, and applications. */
        Computer.Transition::Setup;
    }

    emits {
        /* Start the computer system to run applications or conduct evaluations. */
        Computer.Transition::Enable;
    }
}
