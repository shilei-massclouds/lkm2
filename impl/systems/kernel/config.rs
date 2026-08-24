//! Manually synchronized Linux configuration.

use core::arch::global_asm;

/// Maximum CPU count from Linux `CONFIG_NR_CPUS`.
pub(crate) const NR_CPUS: usize = 64;

/// Kernel stack size from Linux
/// `PAGE_SIZE << (CONFIG_THREAD_SIZE_ORDER + KASAN_STACK_ORDER)`.
///
/// The synchronized configuration uses 4 KiB pages, thread-size order 2, and
/// no KASAN stack expansion.
pub(crate) const THREAD_SIZE: usize = 16 * 1024;

// Make the Rust configuration value available to the linker script without
// duplicating its numeric value there.
global_asm!(
    ".globl THREAD_SIZE",
    ".equ THREAD_SIZE, {thread_size}",
    thread_size = const THREAD_SIZE,
);
