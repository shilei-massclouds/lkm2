//! Architecture-specific machine entry.

#[unsafe(naked)]
#[unsafe(export_name = "_start")]
#[unsafe(link_section = ".head.text.entry")]
pub unsafe extern "C" fn boot_entry(_hart_id: usize, _dtb: usize) -> ! {
    core::arch::naked_asm!("1:", "wfi", "j 1b",);
}
