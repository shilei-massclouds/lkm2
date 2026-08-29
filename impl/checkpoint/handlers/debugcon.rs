//! Raw SBI DBCN checkpoint observation handler.

use core::arch::asm;

const SBI_EXT_DBCN: usize = 0x4442_434e;
const SBI_EXT_DBCN_CONSOLE_WRITE_BYTE: usize = 2;

#[inline(always)]
fn write_byte(byte: u8) {
    // SAFETY: checkpoint handlers run in S-mode with the SBI calling
    // convention active. DBCN write-byte consumes only a0, a6 and a7.
    unsafe {
        asm!(
            "ecall",
            inlateout("a0") byte as usize => _,
            inlateout("a1") 0_usize => _,
            inlateout("a6") SBI_EXT_DBCN_CONSOLE_WRITE_BYTE => _,
            inlateout("a7") SBI_EXT_DBCN => _,
            options(nostack),
        );
    }
}

#[inline(always)]
fn write_bytes(bytes: &[u8]) {
    for &byte in bytes {
        write_byte(byte);
    }
}

#[inline(always)]
fn write_hex(value: u64) {
    let digits = *b"0123456789abcdef";
    let mut shift = 60_u32;
    loop {
        write_byte(digits[((value >> shift) & 0xf) as usize]);
        if shift == 0 {
            break;
        }
        shift -= 4;
    }
}

pub(crate) fn checkpoint(id: &[u8], hash: &[u8], parameters: &[(&[u8], u64)]) {
    write_bytes(b"LKMCP1 id=");
    write_bytes(id);
    write_bytes(b" hash=");
    write_bytes(hash);
    for (name, value) in parameters {
        write_byte(b' ');
        write_bytes(name);
        write_bytes(b"=0x");
        write_hex(*value);
    }
    write_byte(b'\n');
}

pub(crate) fn range(kind: &[u8], index: u64, base: u64, end: u64) {
    write_bytes(b"LKMRNG1 kind=");
    write_bytes(kind);
    write_bytes(b" index=0x");
    write_hex(index);
    write_bytes(b" base=0x");
    write_hex(base);
    write_bytes(b" end=0x");
    write_hex(end);
    write_byte(b'\n');
}

fn class_name(class: u64) -> &'static [u8] {
    match class {
        1 => b"fixmap",
        2 => b"linear",
        _ => b"kernel",
    }
}

pub(crate) fn content_chunk(class: u64, chunk: u64, count: u64, lo: u64, hi: u64) {
    write_bytes(b"LKMPTC1 class=");
    write_bytes(class_name(class));
    write_bytes(b" chunk=0x");
    write_hex(chunk);
    write_bytes(b" count=0x");
    write_hex(count);
    write_bytes(b" digest_lo=0x");
    write_hex(lo);
    write_bytes(b" digest_hi=0x");
    write_hex(hi);
    write_byte(b'\n');
}

pub(crate) fn content_item(class: u64, index: u64, va: u64, pa: u64, flags: u64) {
    write_bytes(b"LKMPTI1 class=");
    write_bytes(class_name(class));
    write_bytes(b" index=0x");
    write_hex(index);
    write_bytes(b" va=0x");
    write_hex(va);
    write_bytes(b" pa=0x");
    write_hex(pa);
    write_bytes(b" flags=0x");
    write_hex(flags);
    write_byte(b'\n');
}
