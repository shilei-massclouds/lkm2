//! Architecture-specific machine entry.

use crate::config;
use crate::objects::cpu::CPUID_TO_HARTID_MAP;
use crate::objects::{BOOT_TASK, PT_SIZE_ON_STACK};

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

        /* Initialize the boot task and kernel stack */
        "la tp, {init_task}",
        "la sp, init_thread_union + {thread_size}",
        "addi sp, sp, -{pt_size_on_stack}",

        "mv a0, a1",

        /* Set trap vector to spin forever to help debug */
        "la a3, .Lsecondary_park",
        "csrw stvec, a3",

        "call setup_vm",

    ".align 2",
    ".Lsecondary_park:",
        /*
         * Park this hart if we:
         *  - have too many harts on CONFIG_RISCV_BOOT_SPINWAIT
         *  - receive an early trap, before setup_trap_vector finished
         *  - fail in smp_callin(), as a successful one wouldn't return
         */
        "wfi",
        "j .Lsecondary_park",

        sr_fs_vs = const SR_FS_VS,
        riscv_szptr = const core::mem::size_of::<usize>(),
        cpuid_to_hartid_map = sym CPUID_TO_HARTID_MAP,
        init_task = sym BOOT_TASK,
        thread_size = const config::THREAD_SIZE,
        pt_size_on_stack = const PT_SIZE_ON_STACK,
    );
}
