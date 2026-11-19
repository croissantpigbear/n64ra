/*
  n64ra: RetroAchievements on a real N64 through a SummerCart64.

  Shared by the agent (agent/), the menu patch (menu/) and, for the
  packet formats, the bridge (bridge/).

  The agent is one blob, linked to run at N64RA_FINAL. The menu copies
  it to N64RA_STAGE before rebooting into the game and points the end
  of the game's boot code (IPL3) at the blob's patcher, the same way
  its cheat engine does. The patcher copies the blob to N64RA_FINAL,
  takes over the exception vector and returns to the game.
*/
#ifndef N64RA_H
#define N64RA_H

#define N64RA_MAGIC      0x4E365241 /* "N6RA" */
#define N64RA_VERSION    1

/* Top 128 KB of the Expansion Pak; staged in memory the boot keeps. */
#define N64RA_FINAL      0x807E0000
#define N64RA_STAGE      0x80720000
#define N64RA_MAX_SIZE   0x20000

/* USB transfers go through the SC64's 8 KB data buffer (BlockRAM,
   1FFE0000h), outside the ROM's SDRAM, so even 64 MB ROMs leave it
   alone: 6 KB out, 2 KB in. Bigger snapshots go in parts, the watch
   list comes down in acknowledged pieces. */
#define N64RA_PI_OUT     0x1FFE0000 /* agent -> PC */
#define N64RA_PI_OUT_SIZE 0x1800
/* The out half's last 64 bytes: the agent's status, rewritten once a
   frame, which the bridge reads straight from the data buffer when
   snapshots stop coming (n64ra_status_t). */
#define N64RA_PI_STATUS  (N64RA_PI_OUT + N64RA_PI_OUT_SIZE - 64)
#define N64RA_STATUS_MAGIC 0x4E365354 /* "N6ST" */
#define N64RA_PI_IN      0x1FFE1800 /* PC -> agent */
#define N64RA_PI_IN_SIZE 0x800

/* USB data types (UNFLoader's own are below 0x10) */
#define N64RA_USB_HELLO      0x50 /* agent: here I am, this ROM */
#define N64RA_USB_SNAP       0x51 /* agent: one frame's snapshot (or part of it) */
#define N64RA_USB_LIST_ACK   0x52 /* agent: list piece taken: next offset, total, list state */
#define N64RA_USB_DELTA      0x53 /* agent: what changed since the bridge's last snapshot */
#define N64RA_USB_LIST       0x58 /* bridge: a piece of xeRAbora's watch list: total, offset, bytes */
#define N64RA_USB_HELLO_REQ  0x59 /* bridge: say hello again */
#define N64RA_USB_NOLIST     0x5A /* bridge: no achievements for this ROM */
#define N64RA_USB_KEY_REQ    0x5B /* bridge: send a whole snapshot next */

/* Snapshot packets are kept to one 512-byte USB transfer each (with the
   SC64's 12 bytes of framing and our 40-byte header): on a Mac the SC64's
   USB link stalled partway through bigger ones. A frame normally goes as
   a DELTA (n64ra_snap_t with magic "SND1", offset = the seq it applies
   to, then runs of big-endian u16 offset, u16 length, bytes); a whole
   snapshot (SNAP, in parts) goes every N64RA_KEY_EVERY frames, when the
   changes are too many for one packet, or when the bridge asks. */
#define N64RA_USB_SAFE       448
#define N64RA_KEY_EVERY      120
#define N64RA_DELTA_MAGIC    0x534E4431 /* "SND1" */

#ifndef __ASSEMBLER__
#include <stdint.h>

/* Start of the blob. Everything is big-endian (the N64's order). */
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t final_addr;
    uint32_t size;          /* bytes to copy */
    uint32_t patcher;       /* address of the patcher in the staged copy */
    uint32_t stage_addr;
    /* filled in by the menu */
    uint32_t rom_size;
    uint32_t flags;
    uint8_t rom_header[64];
} n64ra_header_t;

/* HELLO payload */
typedef struct {
    uint32_t magic;         /* "N6RA" */
    uint32_t version;
    uint32_t rom_size;
    uint32_t mem_size;      /* RDRAM, from osMemSize */
    uint32_t boots;         /* frames seen so far, to spot a reset */
    uint32_t list_state;    /* 0 none, 1 loaded, 2 refused */
    uint8_t rom_header[64];
} n64ra_hello_t;

/* SNAP payload: this header, then bytes [offset, offset + part) of
   xeRAbora's snapshot (the direct values in list order, then an
   address/value pair per chain node, little-endian as rcheevos sees N64
   memory). A snapshot bigger than one packet comes in several, with the
   same seq. */
typedef struct {
    uint32_t magic;         /* "SNP1" */
    uint32_t seq;
    uint32_t frame;         /* VBlanks seen */
    uint32_t count;         /* direct entries */
    uint32_t nodes;
    uint32_t bytes;         /* snapshot bytes that follow */
    uint32_t dropped;       /* snapshots never sent: the bus was busy */
    uint32_t covers;        /* frames this snapshot stands for (1, more if some went by unseen),
                               | N64RA_SNAP_LATE if taken after the game had handled its VI */
    uint32_t offset;        /* where this packet's bytes go in the snapshot */
    uint32_t part;          /* bytes in this packet */
} n64ra_snap_t;
#define N64RA_SNAP_LATE 0x80000000u

/* Status block (big-endian), at N64RA_PI_STATUS */
typedef struct {
    uint32_t magic;         /* "N6ST" */
    uint32_t exceptions;    /* times the agent ran */
    uint32_t watch_hits;    /* times the game wrote its exception handler */
    uint32_t frame;
    uint32_t seq;           /* snapshots taken */
    uint32_t sent;          /* snapshot packets handed to the SC64 */
    uint32_t dropped;
    uint32_t pi_busy;       /* exceptions with the cartridge bus busy */
    uint32_t usb_busy;      /* times the SC64 was still sending */
    uint32_t usb_busy_run;  /* ... in a row, now */
    uint32_t cmd_fail;      /* SC64 commands that failed or timed out */
    uint32_t list_state;
    uint32_t pending;       /* send_buf + 1, pend_buf + 1 (bytes 2, 3) */
    uint32_t count;         /* COP0 count */
    uint32_t mem_limit;
    uint32_t spare;
} n64ra_status_t;
#endif

#endif
