//! Architecture-specific machine entry.

use crate::config;
use crate::objects::cpu::CPUID_TO_HARTID_MAP;
use crate::objects::{BOOT_TASK, PT_SIZE_ON_STACK, SATP_MODE, setup_vm};

use super::asm_macros::load_global_pointer;
use super::csr::SR_FS_VS;

// SAFETY: these attributes define the image's unique firmware entry symbol and
// place a prologue-free naked assembly body in the linker entry section.
#[unsafe(naked)]
#[unsafe(export_name = "_start")]
#[unsafe(link_section = ".head.text.entry")]
/// # Safety
///
/// OpenSBI must enter with the RISC-V boot ABI (`a0 = hart_id`, `a1 = dtb_pa`),
/// supervisor privilege, SATP Bare, and writable RAM covering the linked image,
/// BSS, and boot stack. This entry preserves the firmware arguments and jumps
/// directly to `_start_kernel`; it never returns to firmware.
pub unsafe extern "C" fn boot_entry(_hart_id: usize, _dtb: usize) -> ! {
    core::arch::naked_asm!(
        ".option push",
        ".option rvc",
        /* Encoding of the executable "MZ" image prefix. */
        "c.li s4, -13",
        ".option pop",
        "j {start_kernel}",
        start_kernel = sym start_kernel_entry,
    );
}

// SAFETY: the naked C ABI reserves `a0` for the page-table root without a
// prologue. The current partial stub only relocates `ra` before fail-stop and
// deliberately ignores the page-table root.
#[unsafe(naked)]
// LLVM strips the `\u{1}` raw-name marker, so the ELF symbol remains exactly
// `relocate_enable_mmu` while this toolchain emits it before `_start_kernel`.
#[unsafe(export_name = "\u{1}relocate_enable_mmu")]
#[unsafe(link_section = ".head.text")]
/// # Safety
///
/// `_page_table_root` is reserved for a future Linux-style relocation path.
/// This partial implementation relocates `ra` from the runtime physical image
/// to `KERNEL_LINK_ADDR`, but does not inspect the root, write SATP, or return.
pub unsafe extern "C" fn relocate_enable_mmu(_page_table_root: usize) -> ! {
    core::arch::naked_asm!(
        /* Relocate return address */
        "li a1, {kernel_link_addr}",
        "la a2, _start",
        "sub a1, a1, a2",
        "add ra, ra, a1",
        /* Keep the remaining relocation path unreachable until it can switch SATP. */
        "j {secondary_park}",

        /* Point stvec to virtual address of instruction after satp write */
        "la a2, 1f",
        "add a2, a2, a1",
        "csrw stvec, a2",

        /* Compute satp for kernel page tables, but don't load it yet */
        "srli a2, a0, {page_shift}",
        "la a1, {satp_mode}",
        "ld a1, 0(a1)",
        "or a2, a2, a1",
        "1:",
        "j {secondary_park}",

        kernel_link_addr = const config::KERNEL_LINK_ADDR,
        page_shift = const config::PAGE_SHIFT,
        satp_mode = sym SATP_MODE,
        secondary_park = sym secondary_park,
    );
}

// SAFETY: this is the prologue-free continuation of `boot_entry`; all symbol
// references remain PC-relative until a later phase deliberately enables MMU.
#[unsafe(naked)]
#[unsafe(export_name = "_start_kernel")]
#[unsafe(link_section = ".head.text")]
/// # Safety
///
/// The caller must provide the same boot ABI and machine state documented by
/// [`boot_entry`]. The image's BSS and boot stack must be writable. This entry
/// establishes the early Rust execution environment and does not return.
pub unsafe extern "C" fn start_kernel_entry(_hart_id: usize, _dtb: usize) -> ! {
    core::arch::naked_asm!(
        /* Mask all interrupts */
        "csrw sie, zero",
        "csrw sip, zero",

        /* Load the global pointer */
        load_global_pointer!(),

        /*
         * Disable FPU & VECTOR to detect illegal usage of
         * floating point or vector in kernel space
         */
        "li t0, {sr_fs_vs}",
        "csrc sstatus, t0",

        /* Clear BSS for flat non-ELF images */
        "la a3, __bss_start",
        "la a4, __bss_stop",
        "ble a4, a3, .Lclear_bss_done",
    ".Lclear_bss:",
        "sd zero, (a3)",
        "addi a3, a3, {riscv_szptr}",
        "blt a3, a4, .Lclear_bss",
    ".Lclear_bss_done:",

        "la a2, {cpuid_to_hartid_map}",
        "sd a0, (a2)",

        /* Initialize the boot task and kernel stack */
        "la tp, {init_task}",
        "la sp, init_thread_union + {thread_size}",
        "addi sp, sp, -{pt_size_on_stack}",

        "mv a0, a1",

        /* Set trap vector to spin forever to help debug */
        "la a3, {secondary_park}",
        "csrw stvec, a3",

        "call {setup_vm}",

        /* `setup_vm` returns the early page-table root in `a0`. */
        "call {relocate_enable_mmu}",

        "j {secondary_park}",

        sr_fs_vs = const SR_FS_VS,
        riscv_szptr = const core::mem::size_of::<usize>(),
        cpuid_to_hartid_map = sym CPUID_TO_HARTID_MAP,
        init_task = sym BOOT_TASK,
        relocate_enable_mmu = sym relocate_enable_mmu,
        setup_vm = sym setup_vm,
        secondary_park = sym secondary_park,
        thread_size = const config::THREAD_SIZE,
        pt_size_on_stack = const PT_SIZE_ON_STACK,
    );
}

// SAFETY: this private naked function is a shared fail-stop target. It neither
// consumes an ABI nor returns, and is kept in the pre-MMU text section.
#[unsafe(naked)]
#[unsafe(link_section = ".head.text")]
unsafe extern "C" fn secondary_park() -> ! {
    core::arch::naked_asm!(
        /*
         * Park this hart if we:
         *  - have too many harts on CONFIG_RISCV_BOOT_SPINWAIT
         *  - receive an early trap, before setup_trap_vector finished
         *  - fail in smp_callin(), as a successful one wouldn't return
         */
        "wfi",
        "j {secondary_park}",
        secondary_park = sym secondary_park,
    );
}
