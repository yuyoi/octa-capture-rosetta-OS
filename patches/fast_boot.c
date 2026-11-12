/* Power-up part of the fast audio path, in the firmware (lab1). Called from arm_stub after strip_init: post key scan, so a
 * bad build can always be reflashed with the update buttons.
 *
 * 1. copy the fast path (patches/fast.S + strip.c, built by build_fast.py) into free L1 code with memory DMA and verify it;
 *    only then does ui_hook install wrap48_fast instead of the SDRAM wrap48 (wrap_ptr). Any failure: SDRAM path, as before.
 * 2. if Roland's comp CALL still has its original bytes, point it at _comp_maybe: their comp is skipped while all 8 comp
 *    switches are off (it passes audio through unchanged then: measured), and runs again the sample one is switched on.
 * FAST_INFO tells the PC what happened and which build is in there.
 */
#include "strip.h"
#include "fast_syms.h"

typedef unsigned short u16;

extern char fast_img[], fast_img_end[];
extern u32 wrap_ptr;
extern int fast_copy(u32 dst, u32 src, u32 n);
extern int l1_read(u32 dst, u32 src, u32 n);
extern int l1_patch_call(u32 src);

#define SCR        0xFF803F00u
#define FAST_INFO  0xFF803FF4u          /* +0 stamp of the code in L1, +4 state: 1 running, 2 copy failed, 3 size wrong;
                                           +0x10 comp skip in, +0x20 Roland's call was not the original (left alone) */
#define COMP_SITE  0xFFA0832Au

void fast_boot(void)
{
    volatile u32 *info = (volatile u32 *)FAST_INFO;
    u32 n = (u32)(fast_img_end - fast_img);
    info[0] = 0;
    if (n != FAST_SIZE) {
        info[1] = 3;
        return;
    }
    struct slimc *sl = (struct slimc *)SLIM_ADDR;
    for (int ch = 0; ch < NCH; ch++) {
        sl[ch].pk = 0;
        sl[ch].n = ch;                  /* staggered: one input's comp maths per sample */
        sl[ch].lin = ONE;
    }
    if (fast_copy(FAST_BASE, (u32)fast_img, n)) {
        info[1] = 2;
        return;
    }
    u32 stamp = 0;
    for (u32 i = 0; i < n / 4; i++)
        stamp += ((u32 *)fast_img)[i];
    wrap_ptr = FAST_WRAP48;
    u32 state = 1;
    volatile u16 *s = (volatile u16 *)SCR;
    if (l1_read(SCR, COMP_SITE, 4) == 0 && s[0] == 0xE300 && s[1] == 0x0AE0) {
        int off = ((int)FAST_COMP_MAYBE - (int)COMP_SITE) >> 1;
        s[2] = (u16)(0xE300 | ((off >> 16) & 0xFF));
        s[3] = (u16)(off & 0xFFFF);
        if (l1_patch_call(SCR + 4) == 0)
            state |= 0x10;
    } else {
        state |= 0x20;
    }
    info[0] = stamp;
    info[1] = state;
}
