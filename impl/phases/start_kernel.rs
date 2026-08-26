//! Starting-kernel phase.

use crate::objects::early_console::{BootCommandLine, lookup_linked_backend};
use crate::objects::early_dtb_mapping;

// SAFETY: this is the sole definition of the linker-visible `start_kernel`
// symbol, and its C ABI matches the tail call from the naked ArchHead entry.
#[unsafe(export_name = "start_kernel")]
pub(crate) extern "C" fn start_kernel() -> ! {
    let dtb = match early_dtb_mapping() {
        Some(dtb) => dtb,
        None => fail_stop(),
    };
    let command_line = match BootCommandLine::from_dtb(dtb.as_bytes()) {
        Ok(command_line) => command_line,
        Err(_) => fail_stop(),
    };
    match lookup_linked_backend(&command_line) {
        Ok(_) => {}
        Err(_) => fail_stop(),
    }
    fail_stop()
}

fn fail_stop() -> ! {
    loop {
        core::hint::spin_loop();
    }
}
