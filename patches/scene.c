/* Scenes (scene.h, docs/SCENES.md): TEMP + 16 bank slots in the free tail of the settings flash sector.
 *
 * Flash is reached ONLY through Roland's own "extmem" driver, with the same open / lseek / write / close calls its
 * settings save makes (0x31d160). Every access goes through fl_io(), which refuses anything outside
 * [SC_LO, SC_HI) = [0xF2000, 0xFF040) before the driver is even opened, and checks lseek landed where asked. The driver
 * rewrites the 64 KB sector on close (read-modify-write), the same as Roland's power-off save does every time.
 *
 * Recall goes through the strip mailbox, one filter at a time between two samples, then the single-word settings, then
 * the flags: the same path the app and the STRIP page use. No interrupt masking, no added delay.
 */
#include "scene.h"

#ifdef HOST_TEST
#include <string.h>
extern struct strip strip_host_mem;
#define SB (&strip_host_mem)
extern struct fx strip_host_fx;
#define FXB (&strip_host_fx)
extern struct gate strip_host_gate;
#define GTB (&strip_host_gate)
extern unsigned char host_flash[0x100000];
extern int host_open(const char *n, int m);
extern int host_seek(int fd, u32 off, int w);
extern int host_write(int fd, const void *b, u32 n);
extern int host_read(int fd, void *b, u32 n);
extern int host_close(int fd);
extern void strip_pre(int *in);
#define X_OPEN host_open
#define X_SEEK host_seek
#define X_WRITE host_write
#define X_READ host_read
#define X_CLOSE host_close
#define EXTMEM "extmem"
s16_t host_page_val[NCH][7][8];
s16_t *page_vals(void) { return &host_page_val[0][0][0]; }
void page_vals_loaded(void) { }
#else
#define SB ((volatile struct strip *)STRIP_ADDR)
#define FXB ((volatile struct fx *)FX_ADDR)
#define GTB ((volatile struct gate *)GATE_ADDR)
#define X_OPEN(n, m) ((int (*)(const char *, int))0x366aa4)(n, m)
#define X_SEEK(fd, o, w) ((int (*)(int, u32, int))0x3667b0)(fd, o, w)
#define X_WRITE(fd, b, n) ((int (*)(int, const void *, u32))0x366770)(fd, b, n)
#define X_READ(fd, b, n) ((int (*)(int, void *, u32))0x366730)(fd, b, n)
#define X_CLOSE(fd) ((int (*)(int))0x3667ec)(fd)
#define EXTMEM ((const char *)0x435dd4)  /* Roland's "extmem" string, the name its settings code opens */
s16_t *page_vals(void);                 /* page.c */
void page_vals_loaded(void);
#endif

typedef char size_check[sizeof(struct scene_rec) == SC_REC ? 1 : -1];
typedef char end_check[SC_HI <= 0x100000u && SC_LO >= 0xF1A78u ? 1 : -1];

static struct scene_ctl ctl;
static struct scene_rec work;           /* snapshot / recall buffer (UI task only) */
static struct scene_rec temp;           /* written into Roland's power-off save */

u32 scene_addr(void)
{
    return (u32)(unsigned long)&ctl;    /* 32-bit on the box */
}
#ifdef HOST_TEST
struct scene_ctl *scene_host_ctl(void) { return &ctl; }
#endif

/* ---------------- flash: the one gate ---------------- */
static int fl_io(int wr, u32 off, void *buf, u32 len)
{
    if (off < SC_LO || off >= SC_HI || len == 0 || len > SC_HI - off)
        return -1;                      /* never anything outside our records */
    int fd = X_OPEN(EXTMEM, wr ? 2 : 1);
    if (fd == -1)
        return -2;
    int r = -3;
    if (X_SEEK(fd, off, 0) == (int)off)
        r = (wr ? X_WRITE(fd, buf, len) : X_READ(fd, buf, len)) == (int)len ? 0 : -4;
    X_CLOSE(fd);
    return r;
}

static u32 rec_off(int slot)            /* 0 = TEMP, 1-16 = bank */
{
    return SC_LO + (u32)slot * SC_REC;
}

/* ---------------- record ---------------- */
static u32 crc32(const u8_t *p, u32 n)
{
    u32 c = 0xffffffffu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xedb88320u & -(c & 1));
    }
    return ~c;
}

static u32 rec_crc(const struct scene_rec *r)
{
    return crc32((const u8_t *)&r->seq, SC_REC - 12);
}

static int rec_ok(const struct scene_rec *r)
{
    return r->magic == SC_MAGIC && r->size == SC_REC && r->ver == SC_VER && r->crc == rec_crc(r);
}

static void snapshot(struct scene_rec *r)
{
    volatile struct strip *s = SB;
    for (int c = 0; c < NCH; c++) {
        volatile struct chan *k = &s->ch[c];
        u32 *w = r->ch[c];
        *w++ = k->flags;
        *w++ = (u32)k->gin;
        *w++ = (u32)k->gout;
        for (int b = 0; b < NBQ; b++)
            for (int i = 0; i < 5; i++)
                *w++ = (u32)k->bq[b].c[i];
        *w++ = (u32)k->drive; *w++ = (u32)k->bias; *w++ = (u32)k->c0; *w++ = (u32)k->tout;
        *w++ = (u32)k->thr; *w++ = (u32)k->khalf; *w++ = (u32)k->inv2k; *w++ = (u32)k->slope;
        *w++ = (u32)k->atk; *w++ = (u32)k->rel; *w = (u32)k->makeup;
    }
    volatile struct amp *a = &s->amp;
    u32 *w = r->amp;
    *w++ = a->ch;
    *w++ = (u32)a->d1; *w++ = (u32)a->b1; *w++ = (u32)a->c1;
    *w++ = (u32)a->d2; *w++ = (u32)a->b2; *w++ = (u32)a->c2; *w++ = (u32)a->out;
    for (int b = 0; b < NAMP; b++)
        for (int i = 0; i < 5; i++)
            *w++ = (u32)a->bq[b].c[i];
    const s16_t *v = page_vals();
    s16_t *d = &r->val[0][0][0];
    for (int i = 0; i < NCH * 7 * 8; i++)
        d[i] = v[i];
    const volatile u32 *fp = &FXB->dlen;
    for (int i = 0; i < 16; i++)
        r->fx[i] = i < FX_PARAM_WORDS ? fp[i] : 0;
    volatile struct gate *G = GTB;
    r->gate[0] = (u32)G->hold; r->gate[1] = (u32)G->att; r->gate[2] = (u32)G->rel; r->gate[3] = 0;
    for (int c = 0; c < NCH; c++) {
        r->gate[4 + 2 * c] = (u32)G->ch[c].open;
        r->gate[5 + 2 * c] = (u32)G->ch[c].close;
    }
}

static void seal(struct scene_rec *r, u32 seq)
{
    r->magic = SC_MAGIC;
    r->size = SC_REC;
    r->ver = SC_VER;
    r->seq = seq;
    for (int i = 0; i < 8; i++)
        r->pad[i] = 0;
    r->crc = rec_crc(r);
}

/* the strip mailbox (strip.c strip_pre takes it between two samples) */
static int mbox(u32 target, const u32 *k)
{
    volatile struct strip *s = SB;
    u32 done = s->mb_done;
    for (int i = 0; i < 5; i++)
        s->mb_c[i] = (int)k[i];
    s->mb_target = target;
    s->mb_seq = done + 1;
    for (int t = 0; t < 400000; t++) {
#ifdef HOST_TEST
        int z[10] = { 0 };
        strip_pre(z);
#endif
        if (s->mb_done == done + 1)
            return 1;
    }
    return 0;
}

static const u32 BQ_FLAG[NBQ] = { F_HPF, F_LOW, F_PEAK, F_HIGH, F_TUBE };

static int apply_rec(const struct scene_rec *r)
{
    volatile struct strip *s = SB;
    if (s->magic != MAGIC)
        return 0;
    int ok = 1;
    for (int c = 0; c < NCH; c++) {
        volatile struct chan *k = &s->ch[c];
        const u32 *w = r->ch[c];
        u32 f0 = k->flags, f1 = w[0];
        for (int b = 0; b < NBQ; b++)   /* a block that comes on starts from silence; one that stays on keeps going */
            ok &= mbox(((u32)c << 4) | (u32)b | ((f1 & ~f0 & BQ_FLAG[b]) ? 0x100u : 0), w + 3 + 5 * b);
        const u32 *x = w + 3 + 5 * NBQ;
        k->gin = (int)w[1]; k->gout = (int)w[2];
        k->drive = (int)x[0]; k->bias = (int)x[1]; k->c0 = (int)x[2]; k->tout = (int)x[3];
        k->thr = (int)x[4]; k->khalf = (int)x[5]; k->inv2k = (int)x[6]; k->slope = (int)x[7];
        k->atk = (int)x[8]; k->rel = (int)x[9]; k->makeup = (int)x[10];
        k->flags = f1;
    }
    volatile struct amp *a = &s->amp;
    const u32 *w = r->amp;
    int fresh = a->ch != w[0];
    for (int b = 0; b < NAMP; b++)
        ok &= mbox((u32)(NBQ + b) | (fresh ? 0x100u : 0), w + 8 + 5 * b);
    a->d1 = (int)w[1]; a->b1 = (int)w[2]; a->c1 = (int)w[3];
    a->d2 = (int)w[4]; a->b2 = (int)w[5]; a->c2 = (int)w[6]; a->out = (int)w[7];
    a->ch = w[0];
    s16_t *v = page_vals();
    const s16_t *d = &r->val[0][0][0];
    for (int i = 0; i < NCH * 7 * 8; i++)
        v[i] = d[i];
    page_vals_loaded();
    volatile u32 *fp = &FXB->dlen;      /* single words; the ISR bounds-checks every length itself */
    for (int i = 0; i < FX_PARAM_WORDS; i++)
        fp[i] = r->fx[i];
    volatile struct gate *G = GTB;      /* single words; the gate's flag rides in the strip flags above */
    G->hold = (int)r->gate[0]; G->att = (int)r->gate[1]; G->rel = (int)r->gate[2];
    for (int c = 0; c < NCH; c++) {
        G->ch[c].open = (int)r->gate[4 + 2 * c];
        G->ch[c].close = (int)r->gate[5 + 2 * c];
    }
    return ok;
}

/* ---------------- operations ---------------- */
int scene_recall(int slot)
{
    if (slot < 0 || slot > SC_NBANK || fl_io(0, rec_off(slot), &work, SC_REC) || !rec_ok(&work))
        return 0;
    if (!apply_rec(&work))
        return 0;
    for (int i = 0; i < SC_APP; i++)    /* TEMP now carries this scene's app block */
        temp.app[i] = work.app[i];
    for (int i = 0; i < 16; i++)
        temp.name[i] = work.name[i];
    ctl.loaded = (u32)slot;
    ctl.loads++;
    return 1;
}

int scene_store(int slot, const char *name)
{
    if (slot < 1 || slot > SC_NBANK)
        return 0;
    snapshot(&work);
    for (int i = 0; i < 16; i++)
        work.name[i] = name ? name[i] : temp.name[i];
    for (int i = 0; i < SC_APP; i++)
        work.app[i] = temp.app[i];
    seal(&work, ++ctl.done);
    if (fl_io(1, rec_off(slot), &work, SC_REC))
        return 0;
    for (int i = 0; i < 16; i++)
        ctl.names[slot][i] = work.name[i];
    return 1;
}

int scene_names(void)
{
    int fd = X_OPEN(EXTMEM, 1);         /* read-only: one open for all 17 headers */
    if (fd == -1)
        return 0;
    for (int s = 0; s < SC_NREC; s++) {
        u32 off = rec_off(s);
        struct { u32 magic; u16_t size, ver; u32 crc, seq; char name[16]; } h;
        int ok = off >= SC_LO && off + sizeof h <= SC_HI && X_SEEK(fd, off, 0) == (int)off &&
                 X_READ(fd, &h, sizeof h) == (int)sizeof h && h.magic == SC_MAGIC && h.size == SC_REC;
        for (int i = 0; i < 16; i++)
            ctl.names[s][i] = ok ? h.name[i] : 0;
        if (ok && !ctl.names[s][0])
            ctl.names[s][0] = '?';
    }
    X_CLOSE(fd);
    return 1;
}

const char *scene_name(int slot)
{
    return slot >= 0 && slot < SC_NREC ? ctl.names[slot] : "";
}

int scene_loaded(void)
{
    return (int)ctl.loaded;
}

/* power-up: called once after strip_init (task context). TEMP = how she sounded at the last power-off. */
void scene_boot(void)
{
    ctl.magic = SCTL_MAGIC;
    ctl.req = SR_IDLE;
    ctl.status = SS_IDLE;
    if (fl_io(0, rec_off(0), &temp, SC_REC) == 0 && rec_ok(&temp)) {
        apply_rec(&temp);
    } else {
        for (int i = 0; i < SC_APP; i++)
            temp.app[i] = 0;
        for (int i = 0; i < 16; i++)
            temp.name[i] = 0;
    }
    scene_names();
}

/* Roland's settings save (power-off, BACKUP), in place of its close(fd): TEMP goes into the same open session, so
 * the sector is still erased and written once. Then the real close. */
int scene_close_hook(int fd)
{
    if (SB->magic == MAGIC && fd != -1) {
        snapshot(&temp);
        seal(&temp, temp.seq + 1);
        if (X_SEEK(fd, SC_LO, 0) == (int)SC_LO)
            X_WRITE(fd, &temp, SC_REC);
    }
    return X_CLOSE(fd);
}

/* UI tick (every 50 ms): one request from the PC */
void scene_tick(void)
{
    if (ctl.magic != SCTL_MAGIC || ctl.req == SR_IDLE)
        return;
    u32 req = ctl.req, slot = ctl.slot;
    int ok = 0;
    ctl.status = SS_BUSY;
    switch (req) {
    case SR_STORE:                      /* live -> bank slot, name + app block from ctl.rec */
        for (int i = 0; i < SC_APP; i++)
            temp.app[i] = ctl.rec.app[i];
        ok = scene_store((int)slot, ctl.rec.name);
        break;
    case SR_RECALL:
        ok = scene_recall((int)slot);
        break;
    case SR_READ:
        ok = slot < SC_NREC && fl_io(0, rec_off((int)slot), &ctl.rec, SC_REC) == 0 && rec_ok(&ctl.rec);
        break;
    case SR_WRITE_RAW:                  /* a record from the PC library, checked before it goes anywhere */
        if (slot >= 1 && slot <= SC_NBANK && rec_ok(&ctl.rec)) {
            ok = fl_io(1, rec_off((int)slot), &ctl.rec, SC_REC) == 0;
            if (ok)
                for (int i = 0; i < 16; i++)
                    ctl.names[slot][i] = ctl.rec.name[i];
        }
        break;
    case SR_SAVE_TEMP:                  /* the PC's knob block into TEMP (kept in RAM, written at power-off) */
        for (int i = 0; i < SC_APP; i++)
            temp.app[i] = ctl.rec.app[i];
        ok = 1;
        break;
    case SR_NAMES:
        ok = scene_names();
        break;
    }
    ctl.err = ok ? 0 : req;
    ctl.status = ok ? SS_OK : SS_ERR;
    ctl.done++;
    ctl.req = SR_IDLE;
}
