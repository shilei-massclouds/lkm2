//! Starting-kernel phase.

use core::arch::asm;

use crate::objects::dtb_blob::DtbBlob;
use crate::objects::early_console::{BootCommandLine, EarlyConsoleBackend, lookup_linked_backend};
use crate::objects::memblock::{MemBlock, MemBlockMemory};
use crate::objects::printk::EarlyPrintk;
use crate::objects::{early_dtb_mapping, kernel_image_physical_range, setup_vm_final};
use crate::systems::sbi::{Ecall, SbiCapability, SbiConsole};

const BANNER: &[u8] = b"LKM2 kernel\n";

// SAFETY: this is the sole definition of the linker-visible `start_kernel`
// symbol, and its C ABI matches the tail call from the naked ArchHead entry.
#[unsafe(export_name = "start_kernel")]
pub(crate) extern "C" fn start_kernel() -> ! {
    let mut printk = EarlyPrintk::<SbiConsole<Ecall>>::new();
    if printk.record(BANNER).is_err() {
        fail_stop();
    }
    let dtb = match early_dtb_mapping() {
        Some(dtb) => dtb,
        None => fail_stop(),
    };
    let dtb_blob = match DtbBlob::from_bytes(dtb.as_bytes()) {
        Ok(dtb_blob) => dtb_blob,
        Err(_) => fail_stop(),
    };
    let bootargs = match dtb_blob.chosen_bootargs() {
        Ok(bootargs) => bootargs,
        Err(_) => fail_stop(),
    };
    let command_line = match BootCommandLine::from_chosen_bootargs(bootargs) {
        Ok(command_line) => command_line,
        Err(_) => fail_stop(),
    };
    let memory = match MemBlockMemory::derive_from_dtb(&dtb_blob) {
        Ok(memory) => memory,
        Err(_) => fail_stop(),
    };
    let mut sbi_call = Ecall;
    let capability = match SbiCapability::probe(&mut sbi_call) {
        Ok(capability) => capability,
        Err(_) => fail_stop(),
    };
    let backend = match lookup_linked_backend(&command_line) {
        Ok(backend) => backend,
        Err(_) => fail_stop(),
    };
    let console = match backend {
        EarlyConsoleBackend::Sbi => match SbiConsole::enable(capability, sbi_call) {
            Ok(console) => console,
            Err(_) => fail_stop(),
        },
    };
    if printk.register_console(console).is_err() {
        fail_stop();
    }
    let kernel_image = match kernel_image_physical_range() {
        Some(range) => range,
        None => fail_stop(),
    };
    let mut memblock = match MemBlock::setup_bootmem(
        memory,
        &dtb_blob,
        dtb.physical_address() as u64,
        kernel_image,
    ) {
        Ok(memblock) => memblock,
        Err(_) => fail_stop(),
    };
    if memblock.memory_region_count() == 0 || memblock.reserved_region_count() == 0 {
        fail_stop();
    }
    if setup_vm_final(&mut memblock).is_err() {
        fail_stop();
    }
    // M1 deliberately stops here.  Scheduler enable and interrupt unmask are
    // retained as model-only Complete actions for the next milestone.
    park()
}

fn fail_stop() -> ! {
    loop {
        core::hint::spin_loop();
    }
}

fn park() -> ! {
    loop {
        // SAFETY: the boot hart deliberately parks here with interrupts still
        // masked. WFI may resume spuriously, so the instruction remains looped.
        unsafe { asm!("wfi", options(nomem, nostack)) };
    }
}
