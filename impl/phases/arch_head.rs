//! Architecture-specific machine entry.

use crate::objects::cpu::CPUID_TO_HARTID_MAP;

use super::asm_macros::load_global_pointer;
use super::csr::SR_FS_VS;

#[unsafe(naked)]
#[unsafe(export_name = "_start")]
#[unsafe(link_section = ".head.text.entry")]
pub unsafe extern "C" fn boot_entry(_hart_id: usize, _dtb: usize) -> ! {
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

        "1:",
        "wfi",
        "j 1b",

        sr_fs_vs = const SR_FS_VS,
        riscv_szptr = const core::mem::size_of::<usize>(),
        cpuid_to_hartid_map = sym CPUID_TO_HARTID_MAP,
    );
}
