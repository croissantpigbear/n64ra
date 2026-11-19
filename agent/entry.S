/*
  n64ra agent: blob header, patcher and exception engine.

  Patcher: entered from the end of the game's IPL3 instead of its
  "jr t1" (t1 = the game's entry point). Copies the blob from the stage
  address to its final address, puts "j engine" at the general
  exception vector (80000180h) and sets a watchpoint on that vector,
  then jumps to the game. Uses only k0, k1 and t3-t6, like the menu's
  cheat engine, so the game starts with IPL3's registers.

  Engine: every exception lands here first.
  - A watch exception means the game is writing its own handler over
    ours. Parasyte's trick (alt64, also in N64FlashcartMenu): rewrite
    the base register of that store so the game's handler goes to
    80000120h instead, and return. From then on our vector runs first
    and continues to the game's handler at 80000120h.
  - Anything else: save every register (64-bit: games keep 64-bit
    values in them), run agent_exception() on our own stack, restore,
    continue to the game's handler.
*/
#include "n64ra.h"

#define C0_WATCHLO $18
#define C0_WATCHHI $19
#define C0_CAUSE   $13
#define C0_EPC     $14

    .set noreorder
    .set noat
    .set mips3

    .section .header, "ax"
    .global n64ra_header
n64ra_header:
    .word N64RA_MAGIC
    .word N64RA_VERSION
    .word N64RA_FINAL
    .word __blob_size
    .word patcher - N64RA_FINAL + N64RA_STAGE
    .word N64RA_STAGE
    .word 0                 /* rom_size, from the menu */
    .word 0                 /* flags */
    .fill 64, 1, 0          /* rom_header */

    .text

/* ---- patcher (runs from the staged copy until it jumps) ---------------- */

    .global patcher
patcher:
    li    $t3, N64RA_STAGE
    li    $t4, N64RA_STAGE
    lui   $t6, %hi(__blob_size)
    addiu $t6, %lo(__blob_size)
    addu  $t4, $t4, $t6
    li    $t5, N64RA_FINAL
1:  lw    $k1, 0($t3)
    addiu $t3, $t3, 4
    sw    $k1, 0($t5)
    bne   $t3, $t4, 1b
    addiu $t5, $t5, 4

    /* write back D, invalidate I over the final copy */
    li    $t5, N64RA_FINAL
2:  cache 0x19, 0($t5)      /* hit writeback D */
    cache 0x10, 0($t5)      /* hit invalidate I */
    addiu $t6, $t6, -16
    bgtz  $t6, 2b
    addiu $t5, $t5, 16

    j     patcher_final
    nop

patcher_final:
    /* 80000180h: j engine; nop */
    lui   $k0, 0x8000
    lui   $k1, %hi(engine_j)
    lw    $k1, %lo(engine_j)($k1)
    sw    $k1, 0x180($k0)
    sw    $zero, 0x184($k0)
    cache 0x19, 0x180($k0)
    cache 0x10, 0x180($k0)

    /* watch stores to 80000180h-87h */
    ori   $k1, $zero, 0x0181
    mtc0  $k1, C0_WATCHLO
    mtc0  $zero, C0_WATCHHI
    nop

    jr    $t1
    nop

engine_j:
    j     engine
    nop

/* ---- engine ------------------------------------------------------------ */

    .global engine
engine:
    mfc0  $k0, C0_CAUSE
    nop
    andi  $k1, $k0, 0x1000      /* pre-NMI (reset pressed): drop the watch */
    beqz  $k1, 1f
    nop
    mtc0  $zero, C0_WATCHLO
    nop
1:  andi  $k0, $k0, 0x7C
    ori   $k1, $zero, 0x5C      /* exception code 23: watch */
    bne   $k0, $k1, not_watch
    nop

    /* The faulting store's base register (bits 21-25) becomes the
       target of "ori base, k0, 0x120" with k0 = 80000000h. */
    mfc0  $k1, C0_EPC
    nop
    lw    $k1, 0($k1)
    lui   $k0, 0x03E0
    and   $k1, $k0, $k1
    srl   $k1, $k1, 5
    lui   $k0, 0x3740           /* ori $zero, $k0, 0x0120 */
    ori   $k0, $k0, 0x0120
    or    $k0, $k0, $k1
    lui   $k1, %hi(relocate)
    sw    $k0, %lo(relocate)($k1)
    cache 0x19, %lo(relocate)($k1)
    cache 0x10, %lo(relocate)($k1)
    lui   $k0, 0x8000
relocate:
    nop                         /* becomes: ori base, k0, 0x120 */
    lui   $k1, %hi(n64ra_relocated)  /* count the handler writes (nonzero: relocated) */
    lw    $k0, %lo(n64ra_relocated)($k1)
    nop
    addiu $k0, $k0, 1
    sw    $k0, %lo(n64ra_relocated)($k1)
    eret

not_watch:
    lui   $k0, %hi(save_area)
    addiu $k0, %lo(save_area)
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
    sd    $16, 0x80($k0)
    sd    $17, 0x88($k0)
    sd    $18, 0x90($k0)
    sd    $19, 0x98($k0)
    sd    $20, 0xA0($k0)
    sd    $21, 0xA8($k0)
    sd    $22, 0xB0($k0)
    sd    $23, 0xB8($k0)
    sd    $24, 0xC0($k0)
    sd    $25, 0xC8($k0)
    sd    $28, 0xE0($k0)
    sd    $29, 0xE8($k0)
    sd    $30, 0xF0($k0)
    sd    $31, 0xF8($k0)
    mfhi  $k1
    sd    $k1, 0x100($k0)
    mflo  $k1
    sd    $k1, 0x108($k0)

    lui   $sp, %hi(agent_stack_top)
    addiu $sp, %lo(agent_stack_top)
    mfc0  $a0, C0_CAUSE
    nop
    jal   agent_exception
    addiu $sp, $sp, -32
    move  $k1, $v0              /* 1: continue to the game's handler */

    lui   $k0, %hi(save_area)
    addiu $k0, %lo(save_area)
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
    ld    $16, 0x80($k0)
    ld    $17, 0x88($k0)
    ld    $18, 0x90($k0)
    ld    $19, 0x98($k0)
    ld    $20, 0xA0($k0)
    ld    $21, 0xA8($k0)
    ld    $22, 0xB0($k0)
    ld    $23, 0xB8($k0)
    ld    $24, 0xC0($k0)
    ld    $25, 0xC8($k0)
    ld    $28, 0xE0($k0)
    ld    $29, 0xE8($k0)
    ld    $30, 0xF0($k0)
    ld    $31, 0xF8($k0)
    beqz  $k1, 3f
    nop
    j     0x80000120            /* the game's relocated handler */
    nop
3:  eret                        /* no game handler yet */

    .data
    .balign 8
save_area:
    .fill 0x110, 1, 0
    .global n64ra_relocated
n64ra_relocated:
    .word 0
    .balign 16
agent_stack:
    .fill 0x1000, 1, 0
agent_stack_top:
