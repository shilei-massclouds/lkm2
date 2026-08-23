//! Architecture-specific machine entry.

use core::arch::global_asm;

global_asm!(
    r#"
    .section .head.text.entry, "ax"
    .align 2
    .globl _start
    .type _start, @function
_start:
1:
    wfi
    j 1b
    .size _start, . - _start
"#,
);
