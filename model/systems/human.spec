/* Human is an external source of signal. */

use super::computer::Computer;

external Human {
    drives {
        /* 制订规格和设计计算机 */
        Computer.Transition::Preset;
        Computer.Transition::Setup;
    }

    emits {
        Computer.Transition::Enable;
    }
}
