//! Starting-kernel phase.

use crate::objects::early_console::{BootCommandLine, EarlyConsoleBackend, lookup_linked_backend};
use crate::objects::early_dtb_mapping;
use crate::systems::sbi::{Ecall, SbiCapability, SbiConsole};

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
    let backend = match lookup_linked_backend(&command_line) {
        Ok(backend) => backend,
        Err(_) => fail_stop(),
    };
    let mut sbi_call = Ecall;
    let capability = match SbiCapability::probe(&mut sbi_call) {
        Ok(capability) => capability,
        Err(_) => fail_stop(),
    };
    let mut console = match backend {
        EarlyConsoleBackend::Sbi => match SbiConsole::enable(capability, sbi_call) {
            Ok(console) => console,
            Err(_) => fail_stop(),
        },
    };
    if console.write(&[]).is_err() {
        fail_stop();
    }
    fail_stop()
}

fn fail_stop() -> ! {
    loop {
        core::hint::spin_loop();
    }
}
