//! Basic allocation/release test executed at `MemBlock.Online`.

use core::arch::asm;

use crate::objects::memblock::{MemBlock, MemBlockError};
use crate::systems::sbi::{
    Ecall, SBI_SRST_REASON_NO_REASON, SBI_SRST_REASON_SYSTEM_FAILURE, system_reset,
};

const PAGE_SIZE: u64 = 4096;
const MAX_MEMORY_RANGES: usize = 16;
const MAX_RESERVED_RANGES: usize = 64;

#[derive(Clone, Copy)]
struct Snapshot {
    memory: [(u64, u64); MAX_MEMORY_RANGES],
    memory_len: usize,
    reserved: [(u64, u64); MAX_RESERVED_RANGES],
    reserved_len: usize,
}

fn snapshot(memblock: &MemBlock) -> Option<Snapshot> {
    let mut result = Snapshot {
        memory: [(0, 0); MAX_MEMORY_RANGES],
        memory_len: 0,
        reserved: [(0, 0); MAX_RESERVED_RANGES],
        reserved_len: 0,
    };
    for range in memblock.memory_ranges() {
        let slot = result.memory.get_mut(result.memory_len)?;
        *slot = range;
        result.memory_len += 1;
    }
    for range in memblock.reserved_ranges() {
        let slot = result.reserved.get_mut(result.reserved_len)?;
        *slot = range;
        result.reserved_len += 1;
    }
    Some(result)
}

fn same_snapshot(memblock: &MemBlock, before: Snapshot) -> bool {
    let Some(after) = snapshot(memblock) else {
        return false;
    };
    after.memory_len == before.memory_len
        && after.reserved_len == before.reserved_len
        && after.memory[..after.memory_len] == before.memory[..before.memory_len]
        && after.reserved[..after.reserved_len] == before.reserved[..before.reserved_len]
}

#[inline(always)]
fn write_byte(byte: u8) {
    // SAFETY: PhaseTests run in S-mode with the SBI calling convention active.
    unsafe {
        asm!(
            "ecall",
            inlateout("a0") byte as usize => _,
            inlateout("a1") 0_usize => _,
            inlateout("a6") 2_usize => _,
            inlateout("a7") 0x4442_434e_usize => _,
            options(nostack),
        );
    }
}

fn write(bytes: &[u8]) {
    for &byte in bytes {
        write_byte(byte);
    }
}

fn report(result: &[u8], case: Option<&[u8]>) {
    write(b"LKMPT1 test=memblock-basic checkpoint=MemBlock.Online result=");
    write(result);
    if let Some(case) = case {
        write(b" case=");
        write(case);
    }
    write(b"\n");
}

fn fail(case: &'static [u8]) -> ! {
    report(b"fail", Some(case));
    let mut call = Ecall;
    system_reset(&mut call, SBI_SRST_REASON_SYSTEM_FAILURE)
}

pub(crate) fn run(memblock: &mut MemBlock) -> ! {
    let Some(before) = snapshot(memblock) else {
        fail(b"capture");
    };
    let base = match memblock.allocate_phys(PAGE_SIZE, PAGE_SIZE) {
        Ok(base) => base,
        Err(MemBlockError::AllocationExhausted) => fail(b"allocate-exhausted"),
        Err(_) => fail(b"allocate-error"),
    };
    let end = match base.checked_add(PAGE_SIZE) {
        Some(end) => end,
        None => fail(b"allocation-overflow"),
    };
    if !base.is_multiple_of(PAGE_SIZE) {
        fail(b"alignment");
    }
    if !memblock
        .memory_ranges()
        .any(|(memory_base, memory_end)| memory_base <= base && end <= memory_end)
    {
        fail(b"memory-membership");
    }
    if before
        .reserved
        .iter()
        .take(before.reserved_len)
        .any(|&(reserved_base, reserved_end)| reserved_base < end && base < reserved_end)
    {
        fail(b"reserved-avoidance");
    }
    if !memblock
        .reserved_ranges()
        .any(|(reserved_base, reserved_end)| reserved_base <= base && end <= reserved_end)
    {
        fail(b"reserved-insert");
    }
    if memblock.free_phys(base, PAGE_SIZE).is_err() {
        fail(b"free");
    }
    if !same_snapshot(memblock, before) {
        fail(b"restore");
    }
    report(b"pass", None);
    let mut call = Ecall;
    system_reset(&mut call, SBI_SRST_REASON_NO_REASON)
}
