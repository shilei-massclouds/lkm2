OUTPUT_ARCH(riscv)
ENTRY(_start)

/*
 * Match the 64-bit RISC-V kernel virtual link base.  The raw image is loaded
 * by OpenSBI at its physical kernel address; the position-independent head
 * stub does not use the virtual address before later boot phases establish it.
 * KERNEL_LINK_ADDR is an absolute symbol exported from the Rust configuration.
 */
LOAD_OFFSET = KERNEL_LINK_ADDR;

SECTIONS
{
    . = LOAD_OFFSET;

    .head.text : AT(ADDR(.head.text) - LOAD_OFFSET) ALIGN(4K)
    {
        KEEP(*(.head.text.entry))
        KEEP(*(.head.text .head.text.*))
    }
    ASSERT(_start == ADDR(.head.text), "_start must begin .head.text")
    ASSERT(SIZEOF(.head.text) <= 2M, ".head.text must fit in the first 2 MiB")

    _text_start = ADDR(.head.text);

    .text : AT(ADDR(.text) - LOAD_OFFSET) ALIGN(4K)
    {
        *(.text .text.*)
    }
    _text_end = ADDR(.text) + SIZEOF(.text);

    _rodata_start = ADDR(.rodata);
    .rodata : AT(ADDR(.rodata) - LOAD_OFFSET) ALIGN(4K)
    {
        *(.rodata .rodata.*)

        . = ALIGN(8);
        __earlycon_table_start = .;
        KEEP(*(.earlycon.table))
        __earlycon_table_end = .;
    }
    ASSERT((__earlycon_table_end - __earlycon_table_start) == 24,
           "exactly one earlycon backend must be linked")
    _rodata_end = ADDR(.rodata) + SIZEOF(.rodata);

    _data_start = ADDR(.data);
    .data : AT(ADDR(.data) - LOAD_OFFSET) ALIGN(4K)
    {
        *(.data .data.*)
    }

    .sdata : AT(ADDR(.sdata) - LOAD_OFFSET) ALIGN(4K)
    {
        __global_pointer$ = . + 0x800;
        *(.sdata .sdata.*)
    }
    _data_end = ADDR(.sdata) + SIZEOF(.sdata);

    .bss (NOLOAD) : AT(ADDR(.bss) - LOAD_OFFSET) ALIGN(4K)
    {
        __bss_start = .;
        *(.bss .bss.*)
        *(COMMON)

        /* Linux INIT_TASK_DATA-style boot stack, kept in zero-filled BSS. */
        . = ALIGN(THREAD_SIZE);
        __start_init_stack = .;
        init_thread_union = .;
        init_stack = .;
        . = __start_init_stack + THREAD_SIZE;
        __end_init_stack = .;

        . = ALIGN(8);
        __bss_stop = .;
    }

    . = ALIGN(4K);
    _end = .;

    /DISCARD/ :
    {
        *(.eh_frame .eh_frame.*)
        *(.comment)
    }
}
