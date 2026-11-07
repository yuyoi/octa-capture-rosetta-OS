#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
static uint8_t *sdram, l1a[0x4000], l1b[0x4000], scratch[0x1000], mmr[0x10000];
unsigned flushed_a, flushed_n, faults;
void (*host_tx)(unsigned);
void host_set_tx(void (*f)(unsigned)) { host_tx = f; }
uint8_t *host_map(unsigned a)
{
    static uint8_t trap[8];
    if (!sdram) sdram = calloc(1, 0x1000000);
    if (a < 0x1000000) return sdram + a;
    if (a >= 0xFF800000u && a < 0xFF804000u) return l1a + (a - 0xFF800000u);
    if (a >= 0xFF900000u && a < 0xFF904000u) return l1b + (a - 0xFF900000u);
    if (a >= 0xFFB00000u && a < 0xFFB01000u) return scratch + (a - 0xFFB00000u);
    if (a >= 0xFFC00000u) return mmr + (a & 0xFFFF);
    faults++;                       /* the firmware's ok() must never let this happen */
    return trap;
}
unsigned host_call(unsigned a, unsigned x, unsigned y, unsigned z) { return a == 0x3B0000u ? x + y + z : 0xDEAD; }
void host_iflush(unsigned a, unsigned n) { flushed_a = a; flushed_n = n; }
unsigned host_faults(void) { return faults; }
unsigned host_flushed(void) { return flushed_a ^ (flushed_n << 1); }
