//! RISC-V saved-register layout.

use core::mem::size_of;

/// Linux RISC-V `struct pt_regs` layout for RV64.
#[allow(dead_code)]
#[repr(C)]
pub(crate) struct PtRegs {
    epc: usize,
    ra: usize,
    sp: usize,
    gp: usize,
    tp: usize,
    t0: usize,
    t1: usize,
    t2: usize,
    s0: usize,
    s1: usize,
    a0: usize,
    a1: usize,
    a2: usize,
    a3: usize,
    a4: usize,
    a5: usize,
    a6: usize,
    a7: usize,
    s2: usize,
    s3: usize,
    s4: usize,
    s5: usize,
    s6: usize,
    s7: usize,
    s8: usize,
    s9: usize,
    s10: usize,
    s11: usize,
    t3: usize,
    t4: usize,
    t5: usize,
    t6: usize,
    status: usize,
    badaddr: usize,
    cause: usize,
    orig_a0: usize,
}

pub(crate) const STACK_ALIGN: usize = 16;

const fn align_up(value: usize, alignment: usize) -> usize {
    (value + alignment - 1) & !(alignment - 1)
}

pub(crate) const PT_SIZE_ON_STACK: usize = align_up(size_of::<PtRegs>(), STACK_ALIGN);

const _: () = assert!(size_of::<PtRegs>() == 288);
const _: () = assert!(PT_SIZE_ON_STACK == 288);
