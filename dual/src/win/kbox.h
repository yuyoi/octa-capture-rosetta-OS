// kbox - a thin layer over libusbK for octarec. libusbK's headers clash with winusb.h, so they stay in kbox.cpp.
// libusbK is used for the box that plays audio: unlike WinUSB it lets every isochronous OUT packet have its own size.
// ifc: 0 = audio out (interface 0), 1 = audio in (interface 1), 2 = MIDI (interface 2).
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#pragma once
#include <windows.h>

namespace kb {
struct Dev;
Dev *open(const wchar_t *instance);
void close(Dev *d);
int control(Dev *d, BYTE type, BYTE req, WORD value, WORD index, BYTE *data, int len);      // bytes moved, -1 on error
bool set_alt(Dev *d, int ifc, int alt);
unsigned mbpi(Dev *d, int ifc, int alt, int ep);                                            // 0 = no such endpoint
bool frame(Dev *d, int ifc, unsigned *f);
bool asap(Dev *d, int ifc, int ep);

void *iso_new(Dev *d, int ifc, int ep, int packets, BYTE *buf, unsigned size);
bool iso_offsets(void *iso, unsigned per);
bool iso_set(void *iso, int k, unsigned off, unsigned len);
bool iso_get(void *iso, int k, unsigned *off, unsigned *len, unsigned *status);
DWORD iso_read(void *iso, unsigned len, unsigned *frame, int packets, OVERLAPPED *o);       // GetLastError: 997 = started
DWORD iso_write(void *iso, unsigned len, unsigned *frame, int packets, OVERLAPPED *o);
void iso_free(void *iso);

bool result(Dev *d, int ifc, OVERLAPPED *o, DWORD *n, BOOL wait);
bool read_pipe(Dev *d, int ifc, int ep, BYTE *b, unsigned len, OVERLAPPED *o);              // true = done or started
bool write_pipe(Dev *d, int ifc, int ep, BYTE *b, unsigned len, OVERLAPPED *o);
bool abort(Dev *d, int ifc, int ep);
}  // namespace kb
