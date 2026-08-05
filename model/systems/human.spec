/* Human is an external source of signal. */

spec computer;

use computer.Computer;

external Human {
    drives {
        Computer.Transition::Preset;
        Computer.Transition::Setup;
    }

    emits {
        Computer.Transition::Enable;
    }
}
