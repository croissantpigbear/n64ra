/* n64ra agent blob: header first, linked where the patcher puts it.
   .bss is emitted as zeros so the blob needs no clearing. */
OUTPUT_ARCH(mips)
ENTRY(patcher)
SECTIONS {
    . = 0x807E0000;
    .blob : {
        KEEP(*(.header))
        *(.text .text.*)
        *(.rodata .rodata.* .rodata1)
        *(.data .data.* .sdata .sdata.*)
        *(.bss .bss.* .sbss .sbss.* COMMON)
        . = ALIGN(16);
    }
    __blob_size = SIZEOF(.blob);
    /DISCARD/ : { *(.MIPS.*) *(.reginfo) *(.pdr) *(.comment) *(.gnu.attributes) *(.note.*) *(.eh_frame*) }
}
ASSERT(__blob_size <= 0x20000, "agent blob larger than its 128 KB slot")
