/* n64ra test launcher: does what N64FlashcartMenu does before handing
   over to a game -- stages the agent where the boot keeps it -- and
   what the end of IPL3 does once patched: enters the agent's patcher
   with the game loaded. The "game" is ../game, embedded here. */
#include <libdragon.h>
#include <string.h>

#include "../../agent/n64ra.h"
#include "../../agent/agent_blob.h"
#include "game_bin.h"

extern char tramp_start[], tramp_end[];

#define TRAMP 0x80740000
#define GAME_STAGE 0x80600000
#define GAME_ENTRY 0x80000400

int main(void)
{
    n64ra_header_t *h = (n64ra_header_t *)N64RA_STAGE;

    memcpy((void *)N64RA_STAGE, n64ra_blob, sizeof(n64ra_blob));
    for (int i = 0; i < 16; i++) {
        uint32_t w = io_read(0x10000000 + 4 * i);
        memcpy(&h->rom_header[4 * i], &w, 4);
    }
    h->rom_size = 0; /* the bridge is given the ROM file */
    memcpy((void *)GAME_STAGE, game_bin, sizeof(game_bin));
    memcpy((void *)TRAMP, tramp_start, tramp_end - tramp_start);
    data_cache_hit_writeback((void *)N64RA_STAGE, sizeof(n64ra_blob));
    data_cache_hit_writeback((void *)GAME_STAGE, sizeof(game_bin));
    data_cache_hit_writeback((void *)TRAMP, tramp_end - tramp_start);
    inst_cache_hit_invalidate((void *)TRAMP, tramp_end - tramp_start);

    debug_init_isviewer();
    debugf("launcher: agent staged, patcher %08lx, game %u bytes\n", (unsigned long)h->patcher, (unsigned)sizeof(game_bin));
    disable_interrupts();
    *(volatile uint32_t *)0xA430000C = 0x0555; /* MI: mask everything */
    *(volatile uint32_t *)0xA4400010 = 0;      /* ack VI */

    register uint32_t a0 asm("a0") = GAME_STAGE;
    register uint32_t a1 asm("a1") = GAME_ENTRY;
    register uint32_t a2 asm("a2") = (sizeof(game_bin) + 3) & ~3;
    register uint32_t a3 asm("a3") = h->patcher;
    register uint32_t t1 asm("t1") = GAME_ENTRY;
    asm volatile("lui $t3, 0x8074\n jr $t3\n nop" ::"r"(a0), "r"(a1), "r"(a2), "r"(a3), "r"(t1));
    for (;;) {}
}
