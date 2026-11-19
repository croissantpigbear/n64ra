OUTPUT_ARCH(mips)
ENTRY(_start)
SECTIONS {
    . = 0x80000400;
    .text : { *(.text.start) *(.text .text.*) *(.rodata .rodata.*) *(.data .data.*) *(.bss .bss.* COMMON) }
    /DISCARD/ : { *(.MIPS.*) *(.reginfo) *(.pdr) *(.comment) *(.gnu.attributes) *(.note.*) *(.eh_frame*) }
}
