// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE. libusbK itself is by Travis Lee Robinson (BSD/GPL/LGPL).
#include "kbox.h"

#include <libusbk.h>

#include <cstring>

// libusbK's static library was built against the old DLL C runtime and imports strstr by address
static const char *__cdecl shim_strstr(const char *h, const char *n) { return strstr(h, n); }
extern "C" void *__imp_strstr = (void *)shim_strstr;

namespace kb {
struct Dev {
    KLST_HANDLE list = nullptr;
    KUSB_HANDLE h[3] = {};
};

Dev *open(const wchar_t *instance) {
    char want[KLST_STRING_MAX_LEN] = {};
    WideCharToMultiByte(CP_ACP, 0, instance, -1, want, sizeof want, nullptr, nullptr);
    auto *d = new Dev;
    if (!LstK_Init(&d->list, KLST_FLAG_NONE)) {
        delete d;
        return nullptr;
    }
    KLST_DEVINFO_HANDLE info = nullptr;
    LstK_MoveReset(d->list);
    bool found = false;
    while (LstK_MoveNext(d->list, &info))
        if (!_stricmp(info->DeviceID, want)) {
            found = true;
            break;
        }
    if (!found || !UsbK_Init(&d->h[0], info) || !UsbK_GetAssociatedInterface(d->h[0], 0, &d->h[1]) ||
        !UsbK_GetAssociatedInterface(d->h[0], 1, &d->h[2])) {
        const DWORD e = GetLastError();
        close(d);
        SetLastError(found ? e : ERROR_FILE_NOT_FOUND);
        return nullptr;
    }
    return d;
}

void close(Dev *d) {
    if (!d) return;
    for (int i = 2; i >= 0; i--)
        if (d->h[i]) UsbK_Free(d->h[i]);
    if (d->list) LstK_Free(d->list);
    delete d;
}

int control(Dev *d, BYTE type, BYTE req, WORD value, WORD index, BYTE *data, int len) {
    WINUSB_SETUP_PACKET s = {};
    s.RequestType = type, s.Request = req, s.Value = value, s.Index = index, s.Length = (USHORT)len;
    UINT got = 0;
    return UsbK_ControlTransfer(d->h[0], s, data, (UINT)len, &got, nullptr) ? (int)got : -1;
}

bool set_alt(Dev *d, int ifc, int alt) { return UsbK_SetCurrentAlternateSetting(d->h[ifc], (UCHAR)alt) != FALSE; }

unsigned mbpi(Dev *d, int ifc, int alt, int ep) {
    WINUSB_PIPE_INFORMATION_EX pi = {};
    for (UCHAR k = 0; UsbK_QueryPipeEx(d->h[ifc], (UCHAR)alt, k, &pi); k++)
        if (pi.PipeId == ep) return pi.MaximumBytesPerInterval;
    return 0;
}

bool frame(Dev *d, int ifc, unsigned *f) { return UsbK_GetCurrentFrameNumber(d->h[ifc], (PUINT)f) != FALSE; }

bool asap(Dev *d, int ifc, int ep) {
    UCHAR on = 1;
    return UsbK_SetPipePolicy(d->h[ifc], (UCHAR)ep, ISO_ALWAYS_START_ASAP, 1, &on) != FALSE;
}

void *iso_new(Dev *d, int ifc, int ep, int packets, BYTE *buf, unsigned size) {
    KISOCH_HANDLE h = nullptr;
    return IsochK_Init(&h, d->h[ifc], (UCHAR)ep, (UINT)packets, buf, size) ? h : nullptr;
}
bool iso_offsets(void *iso, unsigned per) { return IsochK_SetPacketOffsets((KISOCH_HANDLE)iso, per) != FALSE; }
bool iso_set(void *iso, int k, unsigned off, unsigned len) { return IsochK_SetPacket((KISOCH_HANDLE)iso, (UINT)k, off, len, 0) != FALSE; }
bool iso_get(void *iso, int k, unsigned *off, unsigned *len, unsigned *status) {
    return IsochK_GetPacket((KISOCH_HANDLE)iso, (UINT)k, (PUINT)off, (PUINT)len, (PUINT)status) != FALSE;
}
DWORD iso_read(void *iso, unsigned len, unsigned *frame, int packets, OVERLAPPED *o) {
    UsbK_IsochReadPipe((KISOCH_HANDLE)iso, len, (PUINT)frame, (UINT)packets, o);
    return GetLastError();
}
DWORD iso_write(void *iso, unsigned len, unsigned *frame, int packets, OVERLAPPED *o) {
    UsbK_IsochWritePipe((KISOCH_HANDLE)iso, len, (PUINT)frame, (UINT)packets, o);
    return GetLastError();
}
void iso_free(void *iso) { IsochK_Free((KISOCH_HANDLE)iso); }

bool result(Dev *d, int ifc, OVERLAPPED *o, DWORD *n, BOOL wait) {
    UINT got = 0;
    const BOOL r = UsbK_GetOverlappedResult(d->h[ifc], o, &got, wait);
    *n = got;
    return r != FALSE;
}

bool read_pipe(Dev *d, int ifc, int ep, BYTE *b, unsigned len, OVERLAPPED *o) {
    return UsbK_ReadPipe(d->h[ifc], (UCHAR)ep, b, len, nullptr, o) || GetLastError() == ERROR_IO_PENDING;
}
bool write_pipe(Dev *d, int ifc, int ep, BYTE *b, unsigned len, OVERLAPPED *o) {
    return UsbK_WritePipe(d->h[ifc], (UCHAR)ep, b, len, nullptr, o) || GetLastError() == ERROR_IO_PENDING;
}
bool abort(Dev *d, int ifc, int ep) { return UsbK_AbortPipe(d->h[ifc], (UCHAR)ep) != FALSE; }
}  // namespace kb
