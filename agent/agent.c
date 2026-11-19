/*
  n64ra agent core: runs on every exception, before the game's handler,
  with interrupts off and every register saved (entry.S).

  On a VI interrupt (one per field) it takes a snapshot of the watch
  list into its own RAM. Whenever the cartridge bus (PI) is free it
  copies the pending snapshot into the SC64's data buffer and asks it to send
  it over USB, and checks for packets from the bridge (the watch list).

  The game owns the PI. The agent touches it only when no DMA or I/O is
  under way, and leaves the PI interrupt as it found it: pending if the
  game's last DMA had finished and its handler has yet to run (our DMA
  raises the same interrupt), clear otherwise. If the bus stays busy,
  a snapshot waits for the next free moment, and is replaced by the
  next frame's if that comes first.

  Memory is read as rcheevos sees N64 memory: RetroAchievements address
  A is the byte at 80000000h + (A ^ 3), and multi-byte values are
  little-endian over those bytes (the emulators keep RDRAM in host-order
  32-bit words).
*/
#include <stdint.h>

#include "n64ra.h"

#ifdef N64RA_DEBUG
#include "isv.h"
#define DBG(label, v) isv_hex(label, v)
#else
#define DBG(label, v) ((void)0)
#endif

typedef uint8_t u8;
typedef uint32_t u32;

#define IO32(a)        (*(volatile u32 *)(a))
#define PI_DRAM_ADDR   IO32(0xA4600000)
#define PI_CART_ADDR   IO32(0xA4600004)
#define PI_RD_LEN      IO32(0xA4600008) /* RDRAM -> cart */
#define PI_WR_LEN      IO32(0xA460000C) /* cart -> RDRAM */
#define PI_STATUS      IO32(0xA4600010)
#define PI_DMA_BUSY    1
#define PI_IO_BUSY     2
#define PI_CLR_INTR    2
#define MI_INTR        IO32(0xA4300008)
#define MI_MASK        IO32(0xA430000C)
#define MI_INTR_VI     0x08
#define MI_INTR_PI     0x10
#define OS_MEM_SIZE    IO32(0x80000318)
#define OS_MEM_SIZE_6105 IO32(0x800003F0) /* where CIC-6105 boot code leaves it (Conker, Banjo-Tooie...) */

#define SC64_SCR       0xBFFF0000
#define SC64_DATA0     0xBFFF0004
#define SC64_DATA1     0xBFFF0008
#define SC64_ID        0xBFFF000C
#define SC64_KEY       0xBFFF0010
#define SC64_BUSY      (1u << 31)
#define SC64_ERROR     (1u << 30)
#define SC64_IDENT     0x53437632 /* "SCv2" */

#define CAUSE_IP2      (1u << 10)  /* RCP interrupt line */

#define MAX_ENTRIES    2048 /* addresses (Conker's set has 131); room for the delta reference */
#define MAX_NODES      1024 /* pointer chain nodes: Conker's set has 180 */
#define LIST_BYTES     (16 + 4 * MAX_ENTRIES + 8 + 12 * MAX_NODES)
#define SNAP_BYTES     (4 * MAX_ENTRIES + 8 * MAX_NODES)
#define PART_MAX       N64RA_USB_SAFE

extern n64ra_header_t n64ra_header;
extern volatile u32 n64ra_relocated;

/* state, all in the blob (no .bss to clear) */
static struct {
    int sc64;              /* 0 unknown, 1 present, -1 absent: stay quiet */
    u32 frame, last_count, seq, dropped;
    int send_buf;          /* snapshot being sent, in parts (-1: none) */
    u32 send_off;
    int pend_buf;          /* snapshot waiting to be sent (-1: none) */
    int list_state;        /* 0 none, 1 loaded, 2 refused */
    u32 list_got;          /* bytes of the list received so far */
    int ack_req;           /* acknowledge the last list piece */
    u32 hello_due;         /* frame at which to say hello again */
    int hello_req;         /* the bridge asked for one */
    int poll_due;
    u32 count, nodes;
    u32 mem_limit;
    u32 period;            /* COP0 counts per frame, learned */
    u32 exceptions, sent, pi_busy, usb_busy, usb_busy_run, cmd_fail, status_frame;
    u32 ref_seq, ref_bytes;  /* the snapshot the bridge has (0: none): deltas are against it */
    u32 key_due;           /* seq from which the next whole snapshot is due */
    int key_req;           /* the bridge asked for one */
    u32 keys;              /* whole snapshots sent */
} st = {.period = 781250, .send_buf = -1, .pend_buf = -1}; /* NTSC: 46.875 MHz / 60 */

/* .bss is part of the blob (agent.ld), so all of this starts at zero */
static u8 list_buf[LIST_BYTES] __attribute__((aligned(16)));
static u8 snap_buf[2][SNAP_BYTES] __attribute__((aligned(16)));
static n64ra_snap_t snap_meta[2];
static u8 pkt_buf[N64RA_PI_OUT_SIZE] __attribute__((aligned(16)));
static u8 in_buf[N64RA_PI_IN_SIZE] __attribute__((aligned(16)));
static n64ra_status_t status_buf __attribute__((aligned(16)));
static u8 ref_buf[SNAP_BYTES]; /* what the bridge has */
static u32 entries[MAX_ENTRIES];
static u32 node_w[MAX_NODES][3];
static u32 node_val[MAX_NODES];

static inline u32 count_reg(void)
{
    u32 c;
    __asm__ volatile("mfc0 %0, $9" : "=r"(c));
    return c;
}

/* ---- caches ----------------------------------------------------------- */

static void dcache_writeback(void *p, u32 n)
{
    u32 a = (u32)p & ~15u, end = (u32)p + n;
    for (; a < end; a += 16) __asm__ volatile("cache 0x19, 0(%0)" ::"r"(a));
}

static void dcache_invalidate(void *p, u32 n)
{
    u32 a = (u32)p & ~15u, end = (u32)p + n;
    for (; a < end; a += 16) __asm__ volatile("cache 0x11, 0(%0)" ::"r"(a));
}

/* ---- PI and SC64 ------------------------------------------------------ */

/* The bus is ours when no DMA or I/O is under way. If the game's last
   DMA has finished but its interrupt is still pending, our own DMA
   raises the same interrupt again, so we leave it pending for the
   game's handler; otherwise we clear the one we caused. */
static int pi_free(void) { return !(PI_STATUS & (PI_DMA_BUSY | PI_IO_BUSY)); }

static void pi_wait(void)
{
    while (PI_STATUS & (PI_DMA_BUSY | PI_IO_BUSY)) {}
}

static u32 io_read(u32 a)
{
    pi_wait();
    return IO32(a);
}

static void io_write(u32 a, u32 v)
{
    pi_wait();
    IO32(a) = v;
}

/* Our own DMA, finished before we return, leaving the PI interrupt as
   the game left it. */
static void pi_dma(void *dram, u32 cart, u32 len, int to_cart)
{
    int game_irq = (MI_INTR & MI_INTR_PI) != 0;
    pi_wait();
    PI_DRAM_ADDR = (u32)dram & 0x1FFFFFFF;
    PI_CART_ADDR = cart & 0x1FFFFFFF;
    if (to_cart) PI_RD_LEN = len - 1;
    else PI_WR_LEN = len - 1;
    pi_wait();
    if (!game_irq) PI_STATUS = PI_CLR_INTR;
}

static int sc64_cmd(u8 id, u32 a0, u32 a1, u32 *r0, u32 *r1)
{
    u32 sr, spin = 0;
    io_write(SC64_DATA0, a0);
    io_write(SC64_DATA1, a1);
    io_write(SC64_SCR, id);
    do {
        sr = io_read(SC64_SCR);
        if (++spin > 200000) { st.cmd_fail++; return -1; } /* give up rather than stall the game */
    } while (sr & SC64_BUSY);
    if (sr & SC64_ERROR) st.cmd_fail++;
    if (r0) *r0 = io_read(SC64_DATA0);
    if (r1) *r1 = io_read(SC64_DATA1);
    return (sr & SC64_ERROR) ? -1 : 0;
}

static int sc64_init(void)
{
    io_write(SC64_KEY, 0);
    io_write(SC64_KEY, 0x5F554E4C);
    io_write(SC64_KEY, 0x4F434B5F);
    if (io_read(SC64_ID) != SC64_IDENT) return -1;
    return 1;
}

static int usb_write_busy(void)
{
    u32 r0 = SC64_BUSY;
    int busy = sc64_cmd('U', 0, 0, &r0, 0) || (r0 & SC64_BUSY);
    if (busy) {
        st.usb_busy++;
        st.usb_busy_run++;
    } else {
        st.usb_busy_run = 0;
    }
    return busy;
}

/* USB_WRITE_STATUS without the counters: is the last packet still going? */
static int sc64_usb_sending(void)
{
    u32 r0 = SC64_BUSY;
    return sc64_cmd('U', 0, 0, &r0, 0) || (r0 & SC64_BUSY);
}

/* Once a frame, into the data buffer's last 64 bytes: the bridge reads
   it from the PC side if snapshots stop. */
static void write_status(void)
{
    n64ra_status_t *s = &status_buf;
    if (st.status_frame == st.frame) return;
    st.status_frame = st.frame;
    s->magic = N64RA_STATUS_MAGIC;
    s->exceptions = st.exceptions;
    s->watch_hits = n64ra_relocated;
    s->frame = st.frame;
    s->seq = st.seq;
    s->sent = st.sent;
    s->dropped = st.dropped;
    s->pi_busy = st.pi_busy;
    s->usb_busy = st.usb_busy;
    s->usb_busy_run = st.usb_busy_run;
    s->cmd_fail = st.cmd_fail;
    s->list_state = (u32)st.list_state;
    s->pending = ((u32)(st.send_buf + 1) << 8) | (u32)(st.pend_buf + 1);
    s->count = count_reg();
    s->mem_limit = st.mem_limit;
    s->spare = st.keys;
    dcache_writeback(s, sizeof(*s));
    pi_dma(s, N64RA_PI_STATUS, sizeof(*s), 1);
}

/* buf must be 16-byte aligned; len rounded up to 8 for the DMA */
static void usb_send(u8 type, void *buf, u32 len)
{
    u32 dlen = (len + 7) & ~7u;
    dcache_writeback(buf, dlen);
    pi_dma(buf, N64RA_PI_OUT, dlen, 1);
    sc64_cmd('M', N64RA_PI_OUT, ((u32)type << 24) | (len & 0xFFFFFF), 0, 0);
}

/* ---- the watch list (xeRAbora's format, little-endian) ----------------- */

static u32 le32(const u8 *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((u32)p[3] << 24); }

static int load_list(u32 len)
{
    u32 i, count, p;
    if (len < 16 || le32(list_buf) != 0x4C574152) return -1; /* "RAWL" */
    count = le32(list_buf + 8);
    if (count > MAX_ENTRIES || 16 + 4 * count > len) return -1;
    for (i = 0; i < count; i++) entries[i] = le32(list_buf + 16 + 4 * i);
    st.nodes = 0;
    p = 16 + 4 * count;
    if (p + 8 <= len && le32(list_buf + p) == 0x324E4152) { /* "RAN2" */
        u32 n = le32(list_buf + p + 4);
        if (n > MAX_NODES || p + 8 + 12 * n > len) return -1;
        for (i = 0; i < n; i++) {
            node_w[i][0] = le32(list_buf + p + 8 + 12 * i);
            node_w[i][1] = le32(list_buf + p + 12 + 12 * i);
            node_w[i][2] = le32(list_buf + p + 16 + 12 * i);
        }
        st.nodes = n;
    }
    st.count = count;
    return 0;
}

/* ---- memory as rcheevos sees it ---------------------------------------- */

static int ra_valid(u32 a, u32 n) { return a < st.mem_limit && n <= st.mem_limit - a; }

static u32 ra_read(u32 a, u32 n)
{
    u32 v = 0, i;
    if (!ra_valid(a, n)) return 0;
    for (i = 0; i < n; i++) v |= (u32)(*(volatile u8 *)(0x80000000u | ((a + i) ^ 3))) << (8 * i);
    return v;
}

/* Into the buffer not being sent; a snapshot still waiting there is
   replaced (and counted as dropped). */
static void take_snapshot(u32 covers)
{
    int b = st.send_buf == 0 ? 1 : 0;
    n64ra_snap_t *h = &snap_meta[b];
    u8 *o = snap_buf[b];
    u32 i, k;

    for (i = 0; i < st.count; i++) {
        u32 e = entries[i], a = e & 0x0FFFFFFF, s = e >> 28;
        int ok = ra_valid(a, s);
        for (k = 0; k < s; k++) *o++ = ok ? *(volatile u8 *)(0x80000000u | ((a + k) ^ 3)) : 0;
    }
    for (i = 0; i < st.nodes; i++) {
        u32 w = node_w[i][0], parent = w & 0x0FFF, from_node = (w >> 12) & 1, size = (w >> 13) & 7;
        u32 shift = (w >> 16) & 31, width = (w >> 21) & 63;
        u32 base = 0, bits, addr, v = 0;
        if (from_node && parent < i) {
            base = node_val[parent];
        } else if (!from_node && parent < st.count) {
            u32 e = entries[parent];
            base = ra_read(e & 0x0FFFFFFF, (e >> 28) == 3 ? 4 : (e >> 28));
        }
        bits = base >> shift;
        if (width && width < 32) bits &= (1u << width) - 1;
        addr = bits * node_w[i][2] + node_w[i][1];
        if ((size == 1 || size == 2 || size == 4) && ra_valid(addr, size)) {
            v = ra_read(addr, size);
        } else {
            addr = 0;
        }
        node_val[i] = v;
        for (k = 0; k < 4; k++) *o++ = (u8)(addr >> (8 * k));
        for (k = 0; k < 4; k++) *o++ = (u8)(v >> (8 * k));
    }
    if (st.pend_buf >= 0) st.dropped++;
    h->magic = 0x534E5031; /* "SNP1" */
    h->seq = ++st.seq;
    h->frame = st.frame;
    h->count = st.count;
    h->nodes = st.nodes;
    h->bytes = (u32)(o - snap_buf[b]);
    h->covers = covers;
    st.pend_buf = b;
}

/* Runs where cur differs from ref (gaps of up to 4 equal bytes are
   carried along), into out: big-endian u16 offset, u16 length, bytes.
   Length of it all, or max + 1 if it would not fit in max. */
static u32 make_delta(const u8 *cur, u32 n, u8 *out, u32 max)
{
    u32 i = 0, o = 0, k;
    while (i < n) {
        u32 start, last, j;
        if (cur[i] == ref_buf[i]) {
            i++;
            continue;
        }
        start = last = i;
        for (j = i + 1; j < n; j++) {
            if (cur[j] != ref_buf[j]) last = j;
            else if (j - last > 4) break;
        }
        j = last - start + 1;
        if (o + 4 + j > max) return max + 1;
        out[o++] = (u8)(start >> 8);
        out[o++] = (u8)start;
        out[o++] = (u8)(j >> 8);
        out[o++] = (u8)j;
        for (k = 0; k < j; k++) out[o++] = cur[start + k];
        i = last + 1;
    }
    return o;
}

static void copy_header(n64ra_snap_t *h, const n64ra_snap_t *m)
{
    u32 i;
    for (i = 0; i < sizeof(*h) / 4; i++) ((volatile u32 *)h)[i] = ((const u32 *)m)[i]; /* no memcpy here */
    h->dropped = st.dropped;
}

static void set_reference(int b)
{
    const n64ra_snap_t *m = &snap_meta[b];
    u32 i;
    for (i = 0; i < m->bytes; i++) ref_buf[i] = snap_buf[b][i];
    st.ref_seq = m->seq;
    st.ref_bytes = m->bytes;
}

/* One packet: a frame's changes, or the next part of a whole snapshot. */
static void send_snapshot_part(void)
{
    n64ra_snap_t *h = (n64ra_snap_t *)pkt_buf;
    const n64ra_snap_t *m;
    u32 n, i;
    if (st.send_buf < 0) {
        if (st.pend_buf < 0) return;
        st.send_buf = st.pend_buf;
        st.pend_buf = -1;
        st.send_off = 0;
        m = &snap_meta[st.send_buf];
        if (!st.key_req && st.ref_seq && st.ref_bytes == m->bytes && (int)(m->seq - st.key_due) < 0) {
            n = make_delta(snap_buf[st.send_buf], m->bytes, pkt_buf + sizeof(*h), N64RA_USB_SAFE);
            if (n <= N64RA_USB_SAFE) {
                copy_header(h, m);
                h->magic = N64RA_DELTA_MAGIC;
                h->offset = st.ref_seq;
                h->part = n;
                usb_send(N64RA_USB_DELTA, pkt_buf, sizeof(*h) + n);
                st.sent++;
                set_reference(st.send_buf);
                st.send_buf = -1;
                return;
            }
        }
        /* a whole one, in parts */
        st.key_req = 0;
        st.key_due = m->seq + N64RA_KEY_EVERY;
        st.ref_seq = 0;
        st.keys++;
    }
    m = &snap_meta[st.send_buf];
    n = m->bytes - st.send_off;
    if (n > PART_MAX) n = PART_MAX;
    copy_header(h, m);
    h->offset = st.send_off;
    h->part = n;
    for (i = 0; i < n; i++) pkt_buf[sizeof(*h) + i] = snap_buf[st.send_buf][st.send_off + i];
    usb_send(N64RA_USB_SNAP, pkt_buf, sizeof(*h) + n);
    st.sent++;
    st.send_off += n;
    if (st.send_off >= m->bytes) {
        set_reference(st.send_buf);
        st.send_buf = -1;
    }
}

/* ---- talking to the bridge --------------------------------------------- */

/* RDRAM size: libultra's osMemSize, or, before the game has set it,
   where the 6105 boot code put it. 0 if neither is known yet. */
static u32 rdram_size(void)
{
    u32 m = OS_MEM_SIZE;
    if (m < 0x400000 || m > 0x800000) m = OS_MEM_SIZE_6105;
    return (m >= 0x400000 && m <= 0x800000) ? m : 0;
}

static void send_hello(void)
{
    static n64ra_hello_t hello __attribute__((aligned(16)));
    u32 i;
    hello.magic = N64RA_MAGIC;
    hello.version = N64RA_VERSION;
    hello.rom_size = n64ra_header.rom_size;
    hello.mem_size = rdram_size();
    hello.boots = st.frame;
    hello.list_state = st.list_state;
    for (i = 0; i < 64; i++) hello.rom_header[i] = n64ra_header.rom_header[i];
    usb_send(N64RA_USB_HELLO, &hello, sizeof(hello));
    DBG("n64ra: hello, frame ", st.frame);
}

static void send_list_ack(void)
{
    static u32 ack[4] __attribute__((aligned(16)));
    ack[0] = st.list_got;
    ack[1] = 0;
    ack[2] = (u32)st.list_state;
    ack[3] = 0;
    usb_send(N64RA_USB_LIST_ACK, ack, sizeof(ack));
}

static u32 be32(const u8 *p) { return ((u32)p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]; }

/* A piece of the watch list: total and offset (big-endian), then bytes.
   Pieces are taken in order and each one acknowledged with the next
   offset wanted, so the bridge never has two in flight. */
static void list_piece(u32 len)
{
    u32 total, off, n, i;
    if (len < 8) return;
    total = be32(in_buf);
    off = be32(in_buf + 4);
    n = len - 8;
    if (off == 0) {
        st.list_got = 0;
        st.list_state = 0;
    }
    if (total <= LIST_BYTES && off == st.list_got && off + n <= total) {
        for (i = 0; i < n; i++) list_buf[off + i] = in_buf[8 + i];
        st.list_got += n;
        if (st.list_got == total) {
            st.list_state = load_list(total) ? 2 : 1;
            DBG("n64ra: list, entries ", st.list_state == 1 ? st.count : 0xFFFFFFFF);
        }
    }
    st.ack_req = 1;
}

/* USB_READ_STATUS: type in rsp0 bits 0-7, length in rsp1; a packet is
   waiting when the length is not zero. USB_READ copies it into the
   in buffer, busy (rsp0 bit 31) until done. As libdragon's usb.c does. */
static void poll_usb(void)
{
    u32 status = 0, len = 0, spin = 0;
    if (sc64_cmd('u', 0, 0, &status, &len)) return;
    len &= 0xFFFFFF;
    if (!len) return;
    {
        u32 type = status & 0xFF;
        u32 take = len > N64RA_PI_IN_SIZE ? N64RA_PI_IN_SIZE : len;
        if (sc64_cmd('m', N64RA_PI_IN, take, 0, 0)) return;
        do {
            if (sc64_cmd('u', 0, 0, &status, 0) || ++spin > 100000) return;
        } while (status & SC64_BUSY);
        if (type == N64RA_USB_LIST) {
            u32 dlen = (take + 7) & ~7u;
            dcache_invalidate(in_buf, dlen);
            pi_dma(in_buf, N64RA_PI_IN, dlen, 0);
            dcache_invalidate(in_buf, dlen);
            list_piece(take);
        } else if (type == N64RA_USB_NOLIST) {
            st.list_state = 2;
            st.count = st.nodes = 0;
        } else if (type == N64RA_USB_HELLO_REQ) {
            st.hello_req = 1;
        } else if (type == N64RA_USB_KEY_REQ) {
            st.key_req = 1;
        }
    }
}

/* With the PI free: unlock the SC64 once, look for packets (once a
   frame, or every time while the watch list is coming in), then send
   one packet -- a list acknowledgement, a hello, or the next part of a
   snapshot. One at a time: the SC64 reads the out buffer after
   USB_WRITE returns, so the next can only go once it is done. */
static void service(void)
{
    if (st.sc64 == 0) {
        st.sc64 = sc64_init();
        DBG("n64ra: sc64 ", (u32)st.sc64);
    }
    if (st.sc64 < 0) return;
    write_status();
    if (st.poll_due || (st.list_got && !st.list_state)) {
        st.poll_due = 0;
        poll_usb();
    }
    if (usb_write_busy()) return;
    if (st.ack_req) {
        st.ack_req = 0;
        send_list_ack();
        return;
    }
    if (st.list_state == 0 && !st.list_got && (int)(st.frame - st.hello_due) >= 0) {
        st.hello_due = st.frame + 120;
        send_hello();
        return;
    }
    if (st.hello_req) {
        st.hello_req = 0;
        send_hello();
        return;
    }
    /* Packets are small, so a whole snapshot takes several: send them
       back to back while the SC64 keeps up (each one waits for the last
       to have gone), up to 16, and leave the rest for the next time. */
    {
        int k;
        for (k = 0; k < 16; k++) {
            u32 spin;
            send_snapshot_part();
            if (st.send_buf < 0) break; /* that was the frame's last packet */
            for (spin = 0; spin < 400 && sc64_usb_sending(); spin++) {}
            if (spin == 400) break;
        }
    }
}

/* Frames. A VI interrupt starts one: snapshot before the game's
   handler runs. The interrupt can also arrive in the instant between
   our last look and the game's handler reading it; then the game
   handles a frame we never saw. So frames are also timed with the COP0
   count: once the frame period is known (st.period, learned from the
   VI interrupts), more than 1.25 periods without a snapshot means one
   was missed, and we take it late -- at the first exception after,
   when the game's state is still that frame's. A gap of several frames
   (no exception at all in between) is reported in the snapshot's
   "covers" so the bridge can count them. */
static void new_frame(u32 at, u32 covers, u32 late)
{
    st.frame += covers;
    st.last_count = at;
    st.poll_due = 1;
    if (st.mem_limit < 0x800000) {
        /* known once the game has run osInitialize; until then 4 MB */
        u32 m = rdram_size();
        st.mem_limit = m ? m : 0x400000;
    }
    if (st.list_state == 1) take_snapshot(covers | (late ? N64RA_SNAP_LATE : 0));
}

static void check_vi(void)
{
    u32 now = count_reg(), since = now - st.last_count, p = st.period;
    if (MI_INTR & MI_MASK & MI_INTR_VI) {
        if (st.frame == 0) {
            new_frame(now, 1, 0);
        } else if (since >= p / 2) {
            u32 covers = 1;
            if (since >= p + p / 4) covers = (since + p / 2) / p; /* missed some, no exception between */
            if (covers > 4) covers = 1;                           /* VI was off: a pause, not lost frames */
            if (covers == 1) p = (3 * p + since) / 4;             /* learn the period (NTSC, PAL, MPAL) */
            st.period = p;
            new_frame(now, covers, 0);
        }
    } else if (st.frame && since >= p + p / 4 && since < 5 * p) {
        /* VIs went by unseen: snapshot now, late, for all of them */
        u32 n = (since + p / 8) / p;
        new_frame(st.last_count + n * p, n, 1);
    }
}

u32 agent_exception(u32 cause)
{
    static int said;
    if (!said) {
        said = 1;
        DBG("n64ra: first exception, cause ", cause);
    }
    st.exceptions++;
    if (cause & CAUSE_IP2) check_vi();
    if (st.sc64 >= 0) {
        if (pi_free()) service();
        else st.pi_busy++;
    }
    /* A VI that came in while we were busy belongs to this exception's
       game handler: take its snapshot now. */
    check_vi();
    return n64ra_relocated != 0;
}
