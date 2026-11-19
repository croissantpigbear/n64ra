/* n64ra test "game": installs its exception handler the way libultra's
   osInitialize does (a 4-word preamble copied with ordinary stores to
   each vector, 80000180h included), starts the VI and counts frames in
   its VI interrupt handler. See game.c for what it keeps in memory. */
    .set noreorder
    .set noat
    .set mips3
    .section .text.start
    .global _start
_start:
    lui   $sp, 0x8030
    /* copy the preamble to 80000000h, 80000080h, 80000100h, 80000180h */
    lui   $a1, 0x8000
    li    $t2, 4
1:  la    $a0, preamble
    li    $t3, 4
2:  lw    $t0, 0($a0)
    addiu $a0, $a0, 4
    sw    $t0, 0($a1)           /* libultra's bcopy: store through a1 */
    addiu $t3, $t3, -1
    bnez  $t3, 2b
    addiu $a1, $a1, 4
    addiu $a1, $a1, 0x80 - 16
    addiu $t2, $t2, -1
    bnez  $t2, 1b
    nop
    /* write back D, invalidate I for the vectors (a1 may have moved) */
    lui   $t0, 0x8000
    li    $t1, 0x200
3:  cache 0x19, 0($t0)
    cache 0x10, 0($t0)
    addiu $t1, $t1, -16
    bgtz  $t1, 3b
    addiu $t0, $t0, 16
    jal   game_main
    nop
4:  b     4b
    nop

preamble:
    lui   $k0, %hi(game_exception)
    addiu $k0, $k0, %lo(game_exception)
    jr    $k0
    nop

    .global game_exception
game_exception:
    lui   $k0, %hi(gsave)
    addiu $k0, %lo(gsave)
    sd    $1, 0x08($k0)
    sd    $2, 0x10($k0)
    sd    $3, 0x18($k0)
    sd    $4, 0x20($k0)
    sd    $5, 0x28($k0)
    sd    $6, 0x30($k0)
    sd    $7, 0x38($k0)
    sd    $8, 0x40($k0)
    sd    $9, 0x48($k0)
    sd    $10, 0x50($k0)
    sd    $11, 0x58($k0)
    sd    $12, 0x60($k0)
    sd    $13, 0x68($k0)
    sd    $14, 0x70($k0)
    sd    $15, 0x78($k0)
    sd    $24, 0xC0($k0)
    sd    $25, 0xC8($k0)
    sd    $29, 0xE8($k0)
    sd    $31, 0xF8($k0)
    mfhi  $k1
    sd    $k1, 0x100($k0)
    mflo  $k1
    sd    $k1, 0x108($k0)
    lui   $sp, 0x8031
    mfc0  $a0, $13
    jal   game_interrupt
    addiu $sp, $sp, -32
    lui   $k0, %hi(gsave)
    addiu $k0, %lo(gsave)
    ld    $at, 0x100($k0)
    mthi  $at
    ld    $at, 0x108($k0)
    mtlo  $at
    ld    $1, 0x08($k0)
    ld    $2, 0x10($k0)
    ld    $3, 0x18($k0)
    ld    $4, 0x20($k0)
    ld    $5, 0x28($k0)
    ld    $6, 0x30($k0)
    ld    $7, 0x38($k0)
    ld    $8, 0x40($k0)
    ld    $9, 0x48($k0)
    ld    $10, 0x50($k0)
    ld    $11, 0x58($k0)
    ld    $12, 0x60($k0)
    ld    $13, 0x68($k0)
    ld    $14, 0x70($k0)
    ld    $15, 0x78($k0)
    ld    $24, 0xC0($k0)
    ld    $25, 0xC8($k0)
    ld    $29, 0xE8($k0)
    ld    $31, 0xF8($k0)
    eret

    .global set_status
set_status:
    mtc0  $a0, $12
    jr    $ra
    nop

    .data
    .balign 8
gsave:
    .fill 0x110, 1, 0
