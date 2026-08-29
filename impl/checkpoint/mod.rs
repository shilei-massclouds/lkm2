//! Runtime checkpoint facade.
//!
//! Generated ABI declarations and milestone wrappers live in `build/`; this
//! module gives implementation code one stable, typed namespace and keeps
//! optional PhaseTest behavior at the MemBlock boundary.

#[path = "../build/checkpoints.rs"]
#[rustfmt::skip]
pub(crate) mod vm;
#[path = "../build/memblock_checkpoints.rs"]
#[rustfmt::skip]
pub(crate) mod memblock;
#[path = "../build/swapper_checkpoints.rs"]
#[rustfmt::skip]
pub(crate) mod swapper;
#[path = "../build/swapper_content_checkpoints.rs"]
#[rustfmt::skip]
pub(crate) mod swapper_content;

pub(crate) mod handlers;

use crate::objects::memblock::MemBlock;

/// Execute the generated MemBlock.Online observations and, when selected at
/// build time, hand only the typed `&mut MemBlock` facade to the PhaseTest.
pub(crate) fn memblock_online(
    memblock_object: &mut MemBlock,
    memory: memblock::MemBlockRangeObservation,
    reserved: memblock::MemBlockRangeObservation,
) {
    memblock::memblock_online(memory, reserved);
    #[cfg(phase_test_memblock_basic)]
    handlers::phase_test::memblock_basic::run(memblock_object);
    #[cfg(not(phase_test_memblock_basic))]
    {
        let _ = memblock_object;
    }
}
