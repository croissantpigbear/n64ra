/* n64ra test game, C part. Each VI interrupt (one per frame) updates,
   all at once, so any snapshot must see a consistent set:

   80100000  frames                          (u32)
   80100010  pointer to buf[sel], sel = (frames >> 6) & 1
   80100020  frames+0 .. frames+3            (4 x u32)
   80100040  frames << 8  (as rcheevos sees it: u16 frames at 100041h)
   80100100, 80100200  buf[0], buf[1]: word 1 = frames * 3
   80500000  frames ^ A5A5A5A5               (Expansion Pak)          */
#include <stdint.h>
#include "../../agent/isv.h"
typedef uint32_t u32;
#define IO32(a) (*(volatile u32 *)(a))
#define G ((volatile u32 *)0x80100000)

void set_status(u32);

static void vi_init(void)
{
    IO32(0xA4400000) = 0x0000320E;   /* control: 16 bpp, AA, resample */
    IO32(0xA4400004) = 0x00200000;   /* framebuffer */
    IO32(0xA4400008) = 320;          /* width */
    IO32(0xA440000C) = 2;            /* V_INTR line */
    IO32(0xA4400014) = 0x03E52239;   /* burst */
    IO32(0xA4400018) = 0x0000020D;   /* V_SYNC: 525 half-lines */
    IO32(0xA440001C) = 0x00000C15;   /* H_SYNC */
    IO32(0xA4400020) = 0x0C150C15;   /* leap */
    IO32(0xA4400024) = 0x006C02EC;   /* H_START */
    IO32(0xA4400028) = 0x002501FF;   /* V_START */
    IO32(0xA440002C) = 0x000E0204;   /* V_BURST */
    IO32(0xA4400030) = 0x00000200;   /* X scale */
    IO32(0xA4400034) = 0x00000400;   /* Y scale */
}

/* PI stress: the main loop streams 32 KB from the ROM over and over, as
   games stream data, and checks each copy against a reference read with
   CPU I/O at start-up. The agent must never corrupt a transfer or take
   the game's PI interrupt.
   80100050  DMAs completed    80100054  DMAs that came back wrong
   80100058  PI interrupts     */
#define DMA_LEN 0x8000
#define DMA_ROM 0x10001000
static u32 dma_buf[DMA_LEN / 4] __attribute__((aligned(16)));
static u32 dma_sum;
static volatile u32 pi_done;

void game_interrupt(u32 cause)
{
    u32 n, sel, intr = IO32(0xA4300008);
    volatile u32 *b;
    if (intr & 0x10) {
        IO32(0xA4600010) = 2;        /* clear the PI interrupt */
        G[22]++;
        pi_done = 1;
    }
    if (!(intr & 0x08)) return;
    IO32(0xA4400010) = 0;            /* acknowledge the VI interrupt */
    n = G[0] + 1;
    G[0] = n;
    sel = (n >> 6) & 1;
    b = (volatile u32 *)(sel ? 0x80100200 : 0x80100100);
    b[1] = n * 3;
    ((volatile u32 *)(sel ? 0x80100100 : 0x80100200))[1] = 0xDEADBEEF;
    G[4] = (u32)b;
    G[8] = n; G[9] = n + 1; G[10] = n + 2; G[11] = n + 3;
    G[16] = n << 8;
    if (IO32(0x80000318) > 0x400000) IO32(0x80500000) = n ^ 0xA5A5A5A5;
    if (!(n % 300)) { isv_hex("game: frame ", n); isv_hex("game: dmas ", G[20]); isv_hex("game: bad dmas ", G[21]); }
    (void)cause;
}

void game_main(void)
{
    G[0] = 0;
    isv_hex("game: started, memory ", IO32(0x80000318));
    isv_hex("game: vector 180 = ", IO32(0xA0000180)); /* j engine, if the agent is in */
    isv_hex("game: vector 120 = ", IO32(0xA0000120)); /* our preamble, moved there */
    vi_init();
    IO32(0xA430000C) = 0x0555;       /* MI mask: clear everything ... */
    IO32(0xA430000C) = 0x0080;       /* ... then VI only */
    for (u32 i = 0; i < DMA_LEN / 4; i++) {
        while (IO32(0xA4600010) & 3) {}
        dma_sum = dma_sum * 31 + IO32(0xA0000000 + DMA_ROM + 4 * i);
    }
    IO32(0xA430000C) = 0x0200;       /* ... and PI */
    set_status(0x00000401);          /* IE, IM2 (RCP) */
    for (;;) {
        u32 sum = 0;
        for (u32 i = 0; i < DMA_LEN; i += 16) __asm__ volatile("cache 0x15, 0(%0)" ::"r"((u32)dma_buf + i));
        pi_done = 0;
        while (IO32(0xA4600010) & 3) {}
        IO32(0xA4600000) = (u32)dma_buf & 0x1FFFFFFF;
        IO32(0xA4600004) = DMA_ROM;
        IO32(0xA460000C) = DMA_LEN - 1;
        while (!pi_done) {}
        for (u32 i = 0; i < DMA_LEN; i += 16) __asm__ volatile("cache 0x11, 0(%0)" ::"r"((u32)dma_buf + i));
        for (u32 i = 0; i < DMA_LEN / 4; i++) sum = sum * 31 + dma_buf[i];
        G[20]++;
        if (sum != dma_sum) G[21]++;
    }
}
