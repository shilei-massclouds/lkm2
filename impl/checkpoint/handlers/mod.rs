//! Checkpoint handler support.
//!
//! The generated ABI symbols remain in each generated suite so canonical IDs
//! and signatures are deterministic. These modules contain the hand-written
//! runtime handler utilities and the PhaseTest-specific handler namespace.

#[allow(dead_code)]
pub(crate) mod debugcon;
#[allow(dead_code)]
pub(crate) mod empty;

#[cfg(phase_test_memblock_basic)]
pub(crate) mod phase_test;
