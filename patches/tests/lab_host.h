/* Host build of patches/lab.c for tests: fake memory map, cycle counter, USB sender. */
#include <stdint.h>
uint8_t *host_map(unsigned a);
unsigned host_call(unsigned a, unsigned x, unsigned y, unsigned z);
void host_iflush(unsigned a, unsigned n);
extern void (*host_tx)(unsigned);
#define M8(a) (*(volatile u8 *)host_map(a))
#define M16(a) (*(volatile u16 *)host_map(a))
#define M32(a) (*(volatile u32 *)host_map(a))
#define CALL3(a, x, y, z) host_call(a, x, y, z)
#define usb_midi_tx(w) host_tx(w)
#define LAB_VER 0x0107
#define UPLOAD_BASE 0x003B0000u
static unsigned host_cyc;
static inline unsigned cycles(void) { return host_cyc += 100; }
static inline void iflush(unsigned a, unsigned n) { host_iflush(a, n); }
#define CSYNC_() do { } while (0)
