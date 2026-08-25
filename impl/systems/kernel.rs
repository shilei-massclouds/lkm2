//! Kernel system initialization.

// SAFETY: this is the sole definition of the linker-visible `soc_early_init`
// symbol, and its C ABI matches the naked ArchHead call site.
#[unsafe(export_name = "soc_early_init")]
pub(crate) extern "C" fn soc_early_init() {
    // SAFETY: the empty volatile assembly has no operands and emits no machine
    // instruction. Its compiler side effect keeps this intentional stub from
    // being merged with generated empty checkpoint handlers.
    unsafe { core::arch::asm!("", options(nomem, nostack)) };
}
