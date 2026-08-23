//! Architecture-specific machine entry.

use super::asm_macros::load_global_pointer;

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
        "1:",
        "wfi",
        "j 1b",
    );
}
