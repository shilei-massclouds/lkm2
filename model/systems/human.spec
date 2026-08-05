/* Human is an external source of signal. */

use super::computer::Computer;

external Human {
    drives {
        Computer.Transition::Preset;
        Computer.Transition::Setup;
    }

    emits {
        Computer.Transition::Enable;
    }
}
