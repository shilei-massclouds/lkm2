//! LKM2 Kernel

#![no_std]
#![no_main]

mod checkpoint;
#[path = "systems/kernel/config.rs"]
mod config;
mod objects;
mod phases;
mod systems;

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo<'_>) -> ! {
    loop {
        core::hint::spin_loop();
    }
}
