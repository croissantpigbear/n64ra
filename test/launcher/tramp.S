/* Copied to 80740000h and run from there: moves the test game over the
   launcher (to 80000400h), flushes the caches and enters the agent's
   patcher with t1 = the game's entry point -- where a real boot is
   after IPL3 has loaded the game and the menu has patched its last
   "jr t1". Position-independent: branches only. */
    .set noreorder
    .section .text
    .global tramp_start, tramp_end
tramp_start:                    /* a0 src, a1 dst, a2 bytes, a3 patcher, t1 entry */
1:  lw    $t0, 0($a0)
    addiu $a0, $a0, 4
    sw    $t0, 0($a1)
    addiu $a2, $a2, -4
    bgtz  $a2, 1b
    addiu $a1, $a1, 4
    lui   $t0, 0x8000           /* D cache: index writeback invalidate, 8 KB */
    li    $t2, 0x2000
2:  cache 0x01, 0($t0)
    addiu $t2, $t2, -16
    bgtz  $t2, 2b
    addiu $t0, $t0, 16
    lui   $t0, 0x8000           /* I cache: index invalidate, 16 KB */
    li    $t2, 0x4000
3:  cache 0x00, 0($t0)
    addiu $t2, $t2, -32
    bgtz  $t2, 3b
    addiu $t0, $t0, 32
    jr    $a3
    nop
tramp_end:
