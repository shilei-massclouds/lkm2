//! LKM2 Kernel

#![no_std]
#![no_main]

#[rustfmt::skip]
#[path = "build/checkpoints.rs"]
mod checkpoints;
#[path = "systems/kernel/config.rs"]
mod config;
mod objects;
mod phases;

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo<'_>) -> ! {
    loop {
        core::hint::spin_loop();
    }
}
