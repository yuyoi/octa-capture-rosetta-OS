/* Host stand-in for Roland's "extmem" flash driver: 1 MB array, every write range logged so the test can prove where
 * scene.c writes. Sector 18 starts out like the real units (serial ID, Roland block, 0xDEAD filler). */
#include <string.h>

typedef unsigned int u32;
unsigned char host_flash[0x100000];
static u32 pos;
static int open_fd = -1, open_mode;
u32 wlog_lo[256], wlog_hi[256];
int wlog_n, opens, closes, bad_name;

void host_flash_reset(void)
{
    for (u32 a = 0; a < sizeof host_flash; a += 2) {
        host_flash[a] = 0xde;
        host_flash[a + 1] = 0xad;
    }
    memcpy(host_flash + 0xF0000, "ISRL00TEST00", 12);
    memset(host_flash + 0xF0040, 0x55, 0xF1A78 - 0xF0040);
    wlog_n = opens = closes = bad_name = 0;
}

int host_open(const char *n, int m)
{
    if (strcmp(n, "extmem")) {
        bad_name++;
        return -1;
    }
    if (open_fd != -1)
        return -1;
    opens++;
    open_mode = m;
    open_fd = 13;
    return open_fd;
}

int host_seek(int fd, u32 off, int w)
{
    if (fd != open_fd || w != 0 || off >= sizeof host_flash)
        return -1;
    pos = off;
    return (int)off;
}

int host_write(int fd, const void *b, u32 n)
{
    if (fd != open_fd || open_mode != 2 || pos + n > sizeof host_flash)
        return -1;
    memcpy(host_flash + pos, b, n);
    if (wlog_n < 256) {
        wlog_lo[wlog_n] = pos;
        wlog_hi[wlog_n++] = pos + n;
    }
    pos += n;
    return (int)n;
}

int host_read(int fd, void *b, u32 n)
{
    if (fd != open_fd || pos + n > sizeof host_flash)
        return -1;
    memcpy(b, host_flash + pos, n);
    pos += n;
    return (int)n;
}

int host_close(int fd)
{
    if (fd != open_fd)
        return -1;
    open_fd = -1;
    closes++;
    return 0;
}
