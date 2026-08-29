//! LKM2 Kernel

#![no_std]
#![no_main]

#[rustfmt::skip]
#[path = "build/checkpoints.rs"]
mod checkpoints;
#[path = "build/memblock_checkpoints.rs"]
#[rustfmt::skip]
mod memblock_checkpoints;
#[path = "build/swapper_checkpoints.rs"]
#[rustfmt::skip]
mod swapper_checkpoints;
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
