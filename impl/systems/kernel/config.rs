//! Manually synchronized Linux configuration.

use core::arch::global_asm;

/// Maximum CPU count from Linux `CONFIG_NR_CPUS`.
pub(crate) const NR_CPUS: usize = 64;

/// Base-page shift from Linux `CONFIG_PAGE_SHIFT`.
pub(crate) const PAGE_SHIFT: usize = 12;

/// Kernel stack size from Linux
/// `PAGE_SIZE << (CONFIG_THREAD_SIZE_ORDER + KASAN_STACK_ORDER)`.
///
/// The synchronized configuration uses 4 KiB pages, thread-size order 2, and
/// no KASAN stack expansion.
pub(crate) const THREAD_SIZE: usize = 16 * 1024;

/// Kernel virtual link address from Linux's 64-bit MMU layout.
pub(crate) const KERNEL_LINK_ADDR: usize = 0xffff_ffff_8000_0000;

/// Direct-map bases from Linux `CONFIG_PAGE_OFFSET`, `PAGE_OFFSET_L4`, and
/// `PAGE_OFFSET_L3` for Sv57, Sv48, and Sv39 respectively.
pub(crate) const PAGE_OFFSET_SV57: usize = 0xff60_0000_0000_0000;
pub(crate) const PAGE_OFFSET_SV48: usize = 0xffff_af80_0000_0000;
pub(crate) const PAGE_OFFSET_SV39: usize = 0xffff_ffd6_0000_0000;

/// Early-DTB virtual addresses derived from Linux's per-mode fixmap layout.
pub(crate) const FIX_FDT_VA_SV57: usize = 0xff1b_ffff_fec0_0000;
pub(crate) const FIX_FDT_VA_SV48: usize = 0xffff_8d7f_fec0_0000;
pub(crate) const FIX_FDT_VA_SV39: usize = 0xffff_ffc4_fec0_0000;

// Make the Rust configuration value available to the linker script without
// duplicating its numeric value there.
global_asm!(
    ".globl KERNEL_LINK_ADDR",
    ".equ KERNEL_LINK_ADDR, {kernel_link_addr}",
    ".globl THREAD_SIZE",
    ".equ THREAD_SIZE, {thread_size}",
    kernel_link_addr = const KERNEL_LINK_ADDR,
    thread_size = const THREAD_SIZE,
);
