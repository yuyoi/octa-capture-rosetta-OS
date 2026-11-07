#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <setjmp.h>
static jmp_buf jb;
static int armed;
volatile unsigned lab_fx[5];
void lab_exc(void) {}
unsigned lab_guard(void (*fn)(void))
{
    if (setjmp(jb)) { armed = 0; return 1; }
    armed = 1; lab_fx[0] = 1;
    fn();
    armed = 0; lab_fx[0] = 0;
    return 0;
}
static uint8_t *sdram, l1a[0x4000], l1b[0x4000], scratch[0x1000], mmr[0x10000];
unsigned flushed_a, flushed_n, faults;
void (*host_tx)(unsigned);
void host_set_tx(void (*f)(unsigned)) { host_tx = f; }
uint8_t *host_map(unsigned a)
{
    static uint8_t trap[8];
    if (!sdram) sdram = calloc(1, 0x1000000);
    if (a >= 0x007FFF00u && a < 0x00800000u && armed) {   /* simulated faulting window */
        lab_fx[1] = 0x26; lab_fx[2] = a; lab_fx[3] = 0x1234; lab_fx[4] = 0x3A3000;
        longjmp(jb, 1);
    }
    if (a < 0x800000) return sdram + a;          /* 0x800000+ = controller banks 2/3: must be refused */
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
