/* Host test of patches/scene.c: store / recall / power-off TEMP / PC requests, and that every flash write lands inside
 * the scene area (or Roland's own block, for the simulated Roland save). Build: run_host_test.sh */
#include <stdio.h>
#include <string.h>
#include "scene.h"

extern struct strip strip_host_mem;
extern void strip_init(void);
extern unsigned char host_flash[0x100000];
extern unsigned wlog_lo[256], wlog_hi[256];
extern int wlog_n, opens, closes, bad_name;
extern void host_flash_reset(void);
extern int host_open(const char *n, int m);
extern int host_seek(int fd, u32 off, int w);
extern int host_write(int fd, const void *b, u32 n);
extern s16_t host_page_val[NCH][7][8];
extern struct fx strip_host_fx;

static int fails, checks;
static void check(int ok, const char *what)
{
    checks++;
    if (!ok)
        fails++;
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
}

static void dial_in(int k)              /* a recognisable chain on every input + the amp */
{
    struct strip *s = &strip_host_mem;
    for (int c = 0; c < NCH; c++) {
        struct chan *h = &s->ch[c];
        h->flags = (u32)(F_HPF | F_LOW | (c & 1 ? F_TUBE : F_COMP) | (c == 0 ? F_AMP : 0));
        h->gin = (1 << 27) + k * 1000 + c;
        h->gout = (1 << 27) - k;
        for (int b = 0; b < NBQ; b++)
            for (int i = 0; i < 5; i++)
                h->bq[b].c[i] = (i == 0 ? (1 << 30) : 0) + k * 7 + c * 13 + b * 101 + i;
        h->drive = 3 << 27; h->bias = k; h->c0 = c; h->tout = 1 << 26;
        h->thr = -k; h->khalf = 5; h->inv2k = 6; h->slope = 7; h->atk = 8; h->rel = 9; h->makeup = 10 + k;
    }
    s->amp.ch = 0;
    s->amp.d1 = k; s->amp.b1 = 2; s->amp.c1 = 3; s->amp.d2 = 4; s->amp.b2 = 5; s->amp.c2 = 6; s->amp.out = 1 << 27;
    for (int b = 0; b < NAMP; b++)
        for (int i = 0; i < 5; i++)
            s->amp.bq[b].c[i] = (i == 0 ? (1 << 30) : 0) + k * 3 + b * 17 + i;
    for (int c = 0; c < NCH; c++)
        for (int b = 0; b < 7; b++)
            for (int i = 0; i < 8; i++)
                host_page_val[c][b][i] = (s16_t)(k * 100 + c * 10 + b + i);
    strip_host_fx.dlen = 1000u + (u32)k; strip_host_fx.rmix = 77 * k; strip_host_fx.clen[3] = 900u + (u32)k;
}

static int same_as(int k)               /* the live strip holds exactly what dial_in(k) set */
{
    struct strip *s = &strip_host_mem;
    struct strip want;
    s16_t pv[NCH][7][8];
    memcpy(pv, host_page_val, sizeof pv);
    struct strip save = *s;
    u32 fd = strip_host_fx.dlen, fc = strip_host_fx.clen[3];
    int fr = strip_host_fx.rmix;
    dial_in(k);
    int fx_ok = fd == strip_host_fx.dlen && fr == strip_host_fx.rmix && fc == strip_host_fx.clen[3];
    strip_host_fx.dlen = fd; strip_host_fx.rmix = fr; strip_host_fx.clen[3] = fc;
    want = *s;
    s16_t pvw[NCH][7][8];
    memcpy(pvw, host_page_val, sizeof pvw);
    *s = save;
    memcpy(host_page_val, pv, sizeof pv);
    for (int c = 0; c < NCH; c++) {
        struct chan *a = &s->ch[c], *b = &want.ch[c];
        if (a->flags != b->flags || a->gin != b->gin || a->gout != b->gout || a->drive != b->drive ||
            a->bias != b->bias || a->c0 != b->c0 || a->tout != b->tout || a->thr != b->thr || a->makeup != b->makeup)
            return 0;
        for (int q = 0; q < NBQ; q++)
            if (memcmp(a->bq[q].c, b->bq[q].c, sizeof a->bq[q].c))
                return 0;
    }
    if (s->amp.ch != want.amp.ch || s->amp.d1 != want.amp.d1 || s->amp.out != want.amp.out)
        return 0;
    for (int q = 0; q < NAMP; q++)
        if (memcmp(s->amp.bq[q].c, want.amp.bq[q].c, sizeof s->amp.bq[q].c))
            return 0;
    return fx_ok && !memcmp(pv, pvw, sizeof pv);
}

int main(void)
{
    extern struct scene_ctl *scene_host_ctl(void);
    struct scene_ctl *ctl = scene_host_ctl();
    check(sizeof(struct scene_rec) == SC_REC && SC_HI == 0xFF040u, "record 0xC40 bytes, 17 records end at 0xFF040");

    host_flash_reset();
    strip_init();
    scene_boot();
    check(ctl->magic == SCTL_MAGIC && ctl->loaded == 0, "fresh unit: control block up, nothing loaded");
    int empty = 1;
    for (int s = 0; s < SC_NREC; s++)
        empty &= ctl->names[s][0] == 0;
    check(empty && wlog_n == 0, "fresh unit: all 17 records empty (0xDEAD filler), nothing written");

    dial_in(1);
    check(scene_store(5, "SLUDGE\0\0\0\0\0\0\0\0\0\0"), "store bank 5");
    check(wlog_n == 1 && wlog_lo[0] == SC_LO + 5 * SC_REC && wlog_hi[0] == SC_LO + 6 * SC_REC, "store wrote record 5 and nothing else");
    check(!strcmp(scene_name(5), "SLUDGE"), "bank 5 named SLUDGE");

    strip_init();                       /* everything off, like a cold box */
    memset(host_page_val, 0, sizeof host_page_val);
    check(!same_as(1), "strip (and echo/reverb) wiped before recall");
    check(scene_recall(5) && same_as(1), "recall bank 5: every coefficient, setting, flag, the amp, echo/reverb and the page values back");
    check(ctl->loaded == 5 && ctl->loads == 1, "recall counted for the PC");
    check(!scene_recall(6), "recall of an empty slot refused");
    check(same_as(1), "refused recall left the sound alone");

    /* Roland's power-off save with our hook in place of its close() */
    dial_in(2);
    int before = wlog_n;
    int fd = host_open("extmem", 2);
    host_seek(fd, 0xF0040, 0);
    unsigned char roland[0xD1C];
    memset(roland, 0x77, sizeof roland);
    host_write(fd, roland, sizeof roland);
    scene_close_hook(fd);
    check(closes > 0 && wlog_n == before + 2 && wlog_lo[before + 1] == SC_LO && wlog_hi[before + 1] == SC_LO + SC_REC,
          "power-off: Roland's block, then TEMP at 0xF2000, one session");
    strip_init();
    memset(host_page_val, 0, sizeof host_page_val);
    scene_boot();
    check(same_as(2), "power-up: TEMP puts the power-off sound back");

    /* corrupted TEMP is ignored */
    host_flash[SC_LO + 100] ^= 0xff;
    strip_init();
    scene_boot();
    check(!same_as(2) && strip_host_mem.ch[0].flags == 0, "TEMP with a bad CRC: not applied, strip stays off");
    host_flash[SC_LO + 100] ^= 0xff;

    /* PC requests */
    dial_in(3);
    memcpy(ctl->rec.name, "DOOM", 5);
    memset(ctl->rec.app, 0xab, SC_APP);
    ctl->slot = 9; ctl->req = SR_STORE;
    scene_tick();
    check(ctl->status == SS_OK && ctl->req == SR_IDLE && !strcmp(scene_name(9), "DOOM"), "PC: store live into bank 9 as DOOM");
    ctl->slot = 9; ctl->req = SR_READ;
    scene_tick();
    check(ctl->status == SS_OK && ctl->rec.app[0] == 0xab && ctl->rec.app[SC_APP - 1] == 0xab, "PC: read bank 9 back, app block intact");
    struct scene_rec copy = ctl->rec;
    int nw = wlog_n;
    ctl->slot = 17; ctl->req = SR_WRITE_RAW;
    scene_tick();
    check(ctl->status == SS_ERR && wlog_n == nw, "PC: raw write to slot 17 refused, nothing written");
    ctl->slot = 0; ctl->req = SR_WRITE_RAW;
    scene_tick();
    check(ctl->status == SS_ERR && wlog_n == nw, "PC: raw write over TEMP refused");
    ctl->rec = copy;
    ctl->rec.val[0][0][0] ^= 1;         /* changed without a new CRC */
    ctl->slot = 3; ctl->req = SR_WRITE_RAW;
    scene_tick();
    check(ctl->status == SS_ERR && wlog_n == nw, "PC: record with a bad CRC refused");
    ctl->rec = copy;
    ctl->slot = 3; ctl->req = SR_WRITE_RAW;
    scene_tick();
    check(ctl->status == SS_OK && wlog_n == nw + 1 && wlog_lo[nw] == SC_LO + 3 * SC_REC, "PC: library record into bank 3");
    strip_init();
    ctl->slot = 3; ctl->req = SR_RECALL;
    scene_tick();
    check(ctl->status == SS_OK && same_as(3) && ctl->loaded == 3, "PC: recall bank 3 = the DOOM sound");

    /* every write this whole run: Roland's own block or our records, nothing else */
    int ours = 1;
    for (int i = 0; i < wlog_n; i++)
        if (!(wlog_lo[i] >= SC_LO && wlog_hi[i] <= SC_HI) && !(wlog_lo[i] == 0xF0040 && wlog_hi[i] == 0xF0040 + 0xD1C))
            ours = 0;
    check(ours && !memcmp(host_flash + 0xF0000, "ISRL00TEST00", 12) && bad_name == 0,
          "all writes inside 0xF2000-0xFF040 (or the simulated Roland save); serial ID untouched");
    check(host_flash[SC_HI] == 0xde && host_flash[SC_LO - 1] == 0xad, "bytes either side of the scene area untouched");
    printf("SCENES: %s (%d checks)\n", fails ? "FAILED" : "ALL PASSED", checks);
    return fails != 0;
}
