/* Rosetta Lab backend for the OCTA-CAPTURE (docs/LAB.md). Built with bfin-elf-gcc, freestanding.
 * Entered from lab_entry (lab1.S) for every USB-MIDI packet the dispatcher 0x369468 dequeues, only after ARMED.
 * Never blocks, never calls the RTOS; replies go out through the firmware's own MIDI send (FW_SEND).
 */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

#ifdef HOST_TEST
#include "lab_host.h"           /* fake memory, cycle counter and USB sender for tests/lab_host_test.c */
#else
#include "lab_cfg.h"            /* LAB_VER, UPLOAD_BASE, usb_midi_tx() */
#define M8(a) (*(volatile u8 *)(a))
#define M16(a) (*(volatile u16 *)(a))
#define M32(a) (*(volatile u32 *)(a))
#define CALL3(a, x, y, z) (((u32 (*)(u32, u32, u32))(a))(x, y, z))
#define CSYNC_() __asm__ volatile("CSYNC;" : : : "memory")
#endif

#define VARS 0x41d5e4u
#define RXMAX 320
#define TXMAX 320

static u8 st;                   /* 1 = inside a lab SysEx (F0 7D 4F ...) */
static u8 fin;                  /* a lab SysEx just ended (its F7 was in this packet) */
static u32 rcable;              /* cable the request came in on: the reply goes back there */
static u32 pcable;              /* cable of the packet being processed */
static u16 rlen;
static u8 rx[RXMAX];
static u8 tx[TXMAX];
static u16 tn;
static const u8 *cur;

static u32 getn(int n)
{
    u32 v = 0;
    while (n--)
        v = (v << 4) | (*cur++ & 0xf);
    return v;
}

static void putb(u32 b)
{
    tx[tn++] = (b >> 4) & 0xf;
    tx[tn++] = b & 0xf;
}

static void put32(u32 v)
{
    putb(v >> 24);
    putb(v >> 16);
    putb(v >> 8);
    putb(v);
}

#ifndef HOST_TEST
static inline u32 cycles(void)
{
    u32 t;
    __asm__ volatile("%0 = CYCLES;" : "=d"(t));
    return t;
}

static void iflush(u32 a, u32 n)
{
    u32 p = a & ~31u, end = a + n;
    __asm__ volatile("SSYNC;" : : : "memory");          /* drain the write buffer (D-cache is write-through) */
    for (; p < end; p += 32)                            /* NOP: IFLUSH must not end a hardware loop (05000443) */
        __asm__ volatile("IFLUSH [%0]; NOP;" : : "a"(p) : "memory");
    __asm__ volatile("SSYNC; CSYNC;" : : : "memory");
}
#endif

/* Only memory that can't hurt: SDRAM banks 0-1, L1 data SRAM halves, scratchpad, MMRs.
 * - L1 code 0xFFA0xxxx has no DCPLB: a normal load there faults.
 * - SDRAM 0x800000+ (controller banks 2/3) lands on the chip's banks 0/1 with the controller's open-row
 *   bookkeeping out of step: even a read there makes later code fetches return garbage (re/RAM_MAP.md). */
static int ok(u32 a, u32 n)
{
    u32 e = a + n;
    if (e < a)
        return 0;
    return (e <= 0x00800000u) ||
           (a >= 0xFF800000u && e <= 0xFF804000u) || (a >= 0xFF900000u && e <= 0xFF904000u) ||
           (a >= 0xFFB00000u && e <= 0xFFB01000u) || (a >= 0xFFC00000u);
}

static void peek(u32 a, u32 n, u32 w)
{
    if (n > 64)
        n = 64;
    if (w == 4) {
        a &= ~3u;
        n &= ~3u;
        for (u32 i = 0; i < n; i += 4) {
            u32 v = M32(a + i);
            putb(v); putb(v >> 8); putb(v >> 16); putb(v >> 24);     /* memory order (LE) */
        }
    } else if (w == 2) {
        a &= ~1u;
        n &= ~1u;
        for (u32 i = 0; i < n; i += 2) {
            u32 v = M16(a + i);
            putb(v); putb(v >> 8);
        }
    } else {
        for (u32 i = 0; i < n; i++)
            putb(M8(a + i));
    }
}

static void poke(u32 a, u32 n, u32 w)
{
    static u8 b[64];
    if (n > 64)
        n = 64;
    for (u32 i = 0; i < n; i++)
        b[i] = getn(2);
    if (w == 4) {
        a &= ~3u;
        for (u32 i = 0; i + 3 < n; i += 4)
            M32(a + i) = b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | ((u32)b[i + 3] << 24);
    } else if (w == 2) {
        a &= ~1u;
        for (u32 i = 0; i + 1 < n; i += 2)
            M16(a + i) = b[i] | (b[i + 1] << 8);
    } else {
        for (u32 i = 0; i < n; i++)
            M8(a + i) = b[i];
    }
}

/* SysEx -> USB-MIDI packets, queued one by one with the firmware's own packet sender (copies the word,
 * waits with dly_tsk(1) only while its 0x800-packet TX ring is full; task 2 drains it). */
static void send_sysex(u32 cable, const u8 *b, u32 n)
{
    u32 i = 0;
    while (i < n) {
        u32 k = n - i, cin;
        if (k > 3) {
            k = 3;
            cin = 4;
        } else {
            cin = 4 + k;                                /* 1 -> 5, 2 -> 6, 3 -> 7: SysEx ends here */
        }
        u32 w = (cable << 4) | cin | ((u32)b[i] << 8);
        if (k > 1)
            w |= (u32)b[i + 1] << 16;
        if (k > 2)
            w |= (u32)b[i + 2] << 24;
        usb_midi_tx(w);
        i += k;
    }
}

/* ---- guarded work: every memory touch runs under lab_guard() so a fault becomes a FAULT reply ---- */
extern u32 lab_guard(void (*fn)(void));
extern volatile u32 lab_fx[5];          /* armed, SEQSTAT, DCPLB_FAULT_ADDR, DCPLB_STATUS, RETX */
static u32 g_cmd, g_a, g_n, g_w, g_pat, g_r0, g_r1, g_r2;

static void work(void)
{
    switch (g_cmd) {
    case 2:
        peek(g_a, g_n, g_w);
        break;
    case 3:
        poke(g_a, g_n, g_w);
        break;
    case 4: {
        u32 t0 = cycles();
        u32 r = CALL3(g_a, g_r0, g_r1, g_r2);
        u32 t1 = cycles();
        put32(r);
        put32(t1 - t0);
        break;
    }
    case 5:
        for (u32 i = 0; i < g_n; i++)
            M32(g_a + 4 * i) = g_pat;
        break;
    case 6: {
        u32 bad = 0, first = 0xffffffffu;
        for (u32 i = 0; i < g_n; i++)
            if (M32(g_a + 4 * i) != g_pat) {
                if (!bad)
                    first = g_a + 4 * i;
                bad++;
            }
        put32(bad);
        put32(first);
        break;
    }
    case 7: {
        u32 s = 0;
        for (u32 i = 0; i < g_n; i++)
            s += M32(g_a + 4 * i);
        put32(s);
        break;
    }
    case 8:
        iflush(g_a, g_n);
        break;
    }
}

static void reply_head(u32 cmd, u32 seq)
{
    tn = 0;
    tx[tn++] = 0xF0; tx[tn++] = 0x7D; tx[tn++] = 0x4F; tx[tn++] = cmd | 0x40; tx[tn++] = seq;
}

static void exec(void)
{
    if (rlen < 4 || rx[0] != 0x7D || rx[1] != 0x4F)
        return;
    u32 cmd = rx[2], seq = rx[3], have = rlen - 4, run = 1;
    static const u8 need[9] = {0, 0, 11, 11, 32, 24, 24, 16, 16};
    if (cmd < 1 || cmd > 8 || have < need[cmd])
        return;
    cur = rx + 4;
    reply_head(cmd, seq);
    g_cmd = cmd;
    switch (cmd) {
    case 1:                                             /* PING */
        putb(LAB_VER >> 8); putb(LAB_VER);
        put32(VARS);
        put32(UPLOAD_BASE);
        run = 0;
        break;
    case 2: case 3:                                     /* PEEK / POKE */
        g_a = getn(8); g_n = getn(2); g_w = getn(1);
        if (g_n > 64)
            g_n = 64;
        if (cmd == 3 && have < 11 + 2 * g_n)
            return;
        run = ok(g_a, g_n);
        break;
    case 4:                                             /* CALL */
        g_a = getn(8); g_r0 = getn(8); g_r1 = getn(8); g_r2 = getn(8);
        break;
    case 5: case 6: case 7:                             /* FILL / CHECK / SUM (words) */
        g_a = getn(8) & ~3u;
        g_n = getn(8) >> 2;
        g_pat = cmd == 7 ? 0 : getn(8);
        run = ok(g_a, 4 * g_n);
        break;
    case 8:                                             /* FLUSH */
        g_a = getn(8); g_n = getn(8);
        break;
    }
    if (run && lab_guard(work)) {                       /* faulted: report it instead of hanging */
        reply_head(0x3F, seq);                          /* reply cmd 0x7F = FAULT */
        for (u32 i = 1; i < 5; i++)
            put32(lab_fx[i]);
    }
    tx[tn++] = 0xF7;
    send_sysex(rcable, tx, tn);
}

static void byte_in(u32 b)
{
    if (b == 0xF0) {
        st = 1;
        rlen = 0;
        rcable = pcable;
    } else if (!st) {
        return;
    } else if (b == 0xF7) {
        st = 0;
        fin = 1;
        exec();
    } else if (b & 0x80) {
        st = 0;                                         /* status byte inside SysEx: abort */
    } else if (rlen < RXMAX) {
        rx[rlen++] = b;
        if ((rlen == 1 && b != 0x7D) || (rlen == 2 && b != 0x4F))
            st = 0;                                     /* not a lab message: leave it to Roland */
    } else {
        st = 0;
    }
}

/* Returns 1 if the packet belonged to a lab message (the caller then hides it from Roland's listeners).
 * Only SysEx from cables 1/2 (CTRL) ever reaches the dispatcher; cable 0 (DIN MIDI) never does. */
extern void lab_exc(void);

int lab_rx(u32 p)
{
    static u8 exc_on;
    u32 cin = p & 0xf, n, mine = st;
    if (!exc_on) {                                      /* post-scan only: lab_rx runs only when ARMED */
        exc_on = 1;
        M32(0xFFE0200Cu) = (u32)lab_exc;                /* EVT3: our catcher (stock behaviour when idle) */
        CSYNC_();
    }
    pcable = (p >> 4) & 0xf;
    if (pcable != 1 && pcable != 2)
        return 0;
    n = (cin == 4 || cin == 7) ? 3 : cin == 6 ? 2 : cin == 5 ? 1 : 0;
    fin = 0;
    for (u32 i = 0; i < n; i++)
        byte_in((p >> (8 + 8 * i)) & 0xff);
    return mine || st || fin;
}
