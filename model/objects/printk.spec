/* Static printk availability and the one-shot kernel banner lifecycle. */

use model::systems::kernel::Kernel;

type PrintkType {
    initial_state: State::Online;

    state State::Online {
    }
}

type BannerType {
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

object Printk: PrintkType {
    parent: Kernel;
}

object Banner: BannerType {
    parent: Kernel;

    state State::Ready {
        transitions {
            override on Transition::Enable -> State::Online {
                ensures {
                    Printk.state == State::Online;
                }
            }
        }
    }
}
