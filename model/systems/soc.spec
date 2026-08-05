/* Soc is the abstract underlying physical platform. */

predicate soc_preset_semantics_deferred<T>(soc: T) -> bool;

object Soc: SystemObject {
    initial_state: State::Base;
    parent: Kernel;

    state State::Base {
        transitions {
            on Transition::Preset -> State::Prepared {
                depends_on {
                    CpuGroup.state == State::Prepared;
                }

                ensures {
                    soc_preset_semantics_deferred(self);
                }

                deferred soc.001 {
                    category: DeferredCategory::ModelDetail;
                    summary: "Define the Soc Preset semantics for the supported physical platform.";
                    evidence { soc_preset_semantics_deferred(self); }
                    close_when: "Soc Preset behavior and its validation are explicitly modeled.";
                }
            }
        }
    }

    state State::Prepared {
        invariant {
            CpuGroup.state == State::Prepared;
            soc_preset_semantics_deferred(self);
        }
    }
}
