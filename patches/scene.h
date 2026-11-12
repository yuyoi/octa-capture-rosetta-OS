/* Scenes: TEMP + a bank of 16 whole-box scenes (all 8 strips + the amp + the STRIP page's values + a block the PC app
 * keeps its own knob positions in), stored in the free tail of the settings flash sector. docs/SCENES.md */
#ifndef SCENE_H
#define SCENE_H
#include "strip.h"

typedef unsigned short u16_t;
typedef short s16_t;
typedef unsigned char u8_t;

#define SC_MAGIC 0x31435352u            /* 'RSC1' */
#define SC_VER 1
#define SC_NBANK 16
#define SC_NREC (1 + SC_NBANK)          /* record 0 = TEMP, 1-16 = bank */
#define SC_REC 0xC40                    /* bytes per record */
#define SC_LO 0xF2000u                  /* flash: first byte we may ever write (Roland's data ends at 0xF1A77) */
#define SC_HI (SC_LO + SC_NREC * SC_REC) /* one past the last: 0xFF040, inside the 0xF0000-0xFFFFF settings sector */
#define SC_CH_WORDS 39
#define SC_AMP_WORDS 38
#define SC_APP 656

struct scene_rec {
    u32 magic;
    u16_t size, ver;
    u32 crc;                            /* crc32 of everything after this word's record header (from seq on) */
    u32 seq;                            /* write counter */
    char name[16];                      /* NUL-padded, shown on the box */
    u32 ch[NCH][SC_CH_WORDS];           /* flags gin gout bq[5].c[5] drive bias c0 tout thr khalf inv2k slope atk rel makeup */
    u32 amp[SC_AMP_WORDS];              /* ch d1 b1 c1 d2 b2 c2 out bq[6].c[5] */
    s16_t val[NCH][7][8];               /* the box STRIP page's own values (page.c val[][][]) */
    u32 fx[16];                         /* echo + reverb settings (struct fx dlen..alen, FX_PARAM_WORDS) */
    u32 gate[20];                       /* hold att rel, then open/close per input */
    u8_t app[SC_APP];                   /* the PC app's knob positions etc.; the box never reads it */
    u8_t pad[8];
};

/* The PC asks through this block (address in the lab PING reply, lab >= 1.8); the UI tick does the work, never the
 * MIDI task (a flash write takes a moment and must not stall it). */
enum { SR_IDLE, SR_STORE, SR_RECALL, SR_READ, SR_WRITE_RAW, SR_SAVE_TEMP, SR_NAMES };
enum { SS_IDLE, SS_BUSY, SS_OK, SS_ERR };
struct scene_ctl {
    u32 magic;                          /* 'SCTL' */
    u32 req, slot, status, err;         /* PC: slot then req; tick: status SS_BUSY..SS_OK/ERR, req back to SR_IDLE */
    u32 done;                           /* counts finished requests */
    u32 loaded;                         /* slot last recalled (0 = none since boot), any source */
    u32 loads;                          /* counts recalls (box menu or PC): the PC re-reads when it moves */
    char names[SC_NREC][16];            /* SR_NAMES fills these (16 x NUL = empty slot) */
    struct scene_rec rec;               /* SR_READ result / SR_WRITE_RAW source / name for SR_STORE */
};
#define SCTL_MAGIC 0x4c544353u          /* 'SCTL' */

u32 scene_addr(void);
void scene_boot(void);                  /* power-up: TEMP back into the strip */
void scene_tick(void);                  /* UI tick: serve the PC's request */
int scene_close_hook(int fd);           /* Roland's power-off save, in place of its close(): TEMP goes in the same write */
int scene_recall(int slot);             /* box menu */
int scene_store(int slot, const char *name);
int scene_names(void);
const char *scene_name(int slot);
int scene_loaded(void);
#endif
