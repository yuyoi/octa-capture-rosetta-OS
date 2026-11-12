/* Copies code into L1 instruction memory with memory DMA stream 1 (the core can't write there, and even reading it with a
 * normal load faults). Stock firmware only uses stream 0, at boot. 16-bit transfers, the same as Roland's own boot copy.
 *
 * fast_sum()  sum of an L1 code range, read back by DMA through the scratch below
 * fast_copy() write src -> dst, then read it all back and compare. Returns 0 or a nonzero reason.
 * Lives in the upload area for live tests, and goes into the firmware unchanged, called from arm_stub (post-scan).
 */
typedef unsigned int u32;
typedef unsigned short u16;

#define MDMA_D1   0xFFC00F80u
#define MDMA_S1   0xFFC00FC0u
#define START     0x04
#define CONFIG    0x08
#define XCOUNT    0x10
#define XMOD      0x14
#define STATUS    0x28
#define R16(a)    (*(volatile u16 *)(a))
#define R32(a)    (*(volatile u32 *)(a))
#define ST_RUN    8
#define ST_ERR    2
#define SCRATCH   0xFF803F00u           /* free tail of L1 data, not cached, past sizeof(struct strip) */
#define SCRATCH_N 256u

static inline void ssync(void) { __asm__("SSYNC;"); }

static void flush_range(u32 a, u32 n)   /* DMA reads RAM, not the data cache */
{
    u32 p = a & ~31u, end = a + n;
    while (p < end) {
        __asm__("FLUSH [%0];" : : "a"(p));
        p += 32;
    }
    ssync();
}

static int dma16(u32 dst, u32 src, u32 nbytes)
{
    if ((R16(MDMA_D1 + CONFIG) | R16(MDMA_S1 + CONFIG)) & 1)
        return 1;                       /* somebody is using the stream: leave it alone */
    R32(MDMA_S1 + START) = src;
    R16(MDMA_S1 + XCOUNT) = (u16)(nbytes / 2);
    R16(MDMA_S1 + XMOD) = 2;
    R32(MDMA_D1 + START) = dst;
    R16(MDMA_D1 + XCOUNT) = (u16)(nbytes / 2);
    R16(MDMA_D1 + XMOD) = 2;
    R16(MDMA_D1 + STATUS) = 3;
    R16(MDMA_S1 + STATUS) = 3;
    ssync();
    R16(MDMA_S1 + CONFIG) = 0x05;       /* enable, 16 bit */
    R16(MDMA_D1 + CONFIG) = 0x07;       /* enable, write to memory, 16 bit */
    ssync();
    u32 guard = 0;
    while ((R16(MDMA_D1 + STATUS) | R16(MDMA_S1 + STATUS)) & ST_RUN)
        if (++guard > 2000000u) {
            R16(MDMA_D1 + CONFIG) = 0;
            R16(MDMA_S1 + CONFIG) = 0;
            ssync();
            return 2;                   /* never finished */
        }
    u32 err = (R16(MDMA_D1 + STATUS) | R16(MDMA_S1 + STATUS)) & ST_ERR;
    R16(MDMA_D1 + CONFIG) = 0;
    R16(MDMA_S1 + CONFIG) = 0;
    R16(MDMA_D1 + STATUS) = 3;
    R16(MDMA_S1 + STATUS) = 3;
    ssync();
    return err ? 3 : 0;
}

static int bad_range(u32 dst, u32 n)
{
    return (dst & 1) || (n & 1) || !n || dst < 0xFFA04900u || dst + n > 0xFFA08000u;     /* the free 14 KB and nothing else */
}

u32 fast_sum(u32 a, u32 n)
{
    if (bad_range(a, n))
        return 0xFFFFFFFFu;
    u32 sum = 0;
    while (n) {
        u32 k = n < SCRATCH_N ? n : SCRATCH_N;
        if (dma16(SCRATCH, a, k))
            return 0xFFFFFFFEu;
        for (u32 i = 0; i < k / 2; i++)
            sum += ((volatile u16 *)SCRATCH)[i];
        a += k;
        n -= k;
    }
    return sum;
}

/* Read any L1 code into the L1 data scratch (for checking what is there before touching it). */
int l1_read(u32 dst, u32 src, u32 n)
{
    if ((src & 1) || (n & 1) || !n || n > 64 || src < 0xFFA00000u || src + n > 0xFFA0C000u ||
        dst < SCRATCH || dst + n > SCRATCH + SCRATCH_N)
        return 10;
    return dma16(dst, src, n);
}

/* Write 4 bytes over the one CALL in Roland's 48k chain that calls their comp, with interrupts off so the audio can never
 * run a half-written instruction. Nothing else in Roland's code can be written with this. */
#define COMP_CALL_SITE 0xFFA0832Au
int l1_patch_call(u32 src)
{
    if (src < SCRATCH || src + 4 > SCRATCH + SCRATCH_N || (src & 1))
        return 10;
    u32 m;
    __asm__ volatile("CLI %0;" : "=d"(m));
    int r = dma16(COMP_CALL_SITE, src, 4);
    __asm__ volatile("STI %0;" : : "d"(m));
    return r;
}

int fast_copy(u32 dst, u32 src, u32 n)
{
    if (bad_range(dst, n) || (src & 1))
        return 10;
    flush_range(src, n);
    int r = dma16(dst, src, n);
    if (r)
        return 20 + r;
    u32 d = dst, s = src, left = n;
    while (left) {                       /* every word back through the scratch, compared with what was meant to land */
        u32 k = left < SCRATCH_N ? left : SCRATCH_N;
        r = dma16(SCRATCH, d, k);
        if (r)
            return 30 + r;
        for (u32 i = 0; i < k / 2; i++)
            if (((volatile u16 *)SCRATCH)[i] != ((volatile u16 *)s)[i])
                return 40;
        d += k;
        s += k;
        left -= k;
    }
    return 0;
}
