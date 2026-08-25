//! Starting-kernel phase.

// SAFETY: this is the sole definition of the linker-visible `start_kernel`
// symbol, and its C ABI matches the tail call from the naked ArchHead entry.
#[unsafe(export_name = "start_kernel")]
pub(crate) extern "C" fn start_kernel() -> ! {
    loop {
        core::hint::spin_loop();
    }
}
