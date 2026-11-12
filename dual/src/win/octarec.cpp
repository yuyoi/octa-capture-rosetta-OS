// octarec - the recording engine behind Rosetta Octa Control's REC button. Streams every OCTA-CAPTURE on WinUSB (no
// Roland driver), reports input meters, and records inputs 1-10 of each box to 24-bit mono WAVs. When a take stops,
// box 2's kept tracks are re-timed onto box 1's timeline, using each box's exact sample count per USB tick and each USB
// controller's clock against the PC clock.
//
// stdin:  rec <folder>   start a take (files: b1_in01.wav ... b2_in10.wav)
//         stop           finish it (silent tracks are deleted)
//         quit
//         sx <box> <hex>  send one SysEx message to a box (lab / Roland parameter messages)
//         mon 1|0|t      the other box's inputs out of OUT 1-2 of the box on libusbK / off / -50 dBFS test tone
//         feed <name>|off  send the inputs set by fgain to the playback device whose name contains <name> (feed.h)
//         fgain <box> <g1..g10>  per-input send gains, 0 = not sent
// stdout: READY <boxes> <rate> <serials...> | NOBOX | M <box> <10 x peak dBFS> | REC | DONE <seconds> <dropouts> <ratio_ppm> <kept...>
//         S <box> <hex>   SysEx from a box
//         ERR <text>
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <windows.h>
#include <initguid.h>
#include <setupapi.h>
#include <winusb.h>
#include <usb.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "core/drift.h"
#include "feed.h"
#include "kbox.h"

#pragma comment(lib, "winusb.lib")
#pragma comment(lib, "setupapi.lib")

static std::mutex g_out;
static void say(const char *fmt, ...) {
    std::lock_guard<std::mutex> lk(g_out);
    va_list a;
    va_start(a, fmt);
    vprintf(fmt, a);
    va_end(a);
    fflush(stdout);
}

struct Dev {
    std::wstring instance, path;
    bool libusbk = false;
};

static std::vector<Dev> find_octas() {
    std::vector<Dev> out;
    HDEVINFO all = SetupDiGetClassDevsW(nullptr, L"USB", nullptr, DIGCF_ALLCLASSES | DIGCF_PRESENT);
    SP_DEVINFO_DATA di = {sizeof di};
    for (DWORD i = 0; SetupDiEnumDeviceInfo(all, i, &di); i++) {
        wchar_t hw[512] = {}, svc[64] = {}, inst[256] = {};
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_HARDWAREID, nullptr, (BYTE *)hw, sizeof hw, nullptr);
        if (!wcsstr(hw, L"VID_0582&PID_0120")) continue;
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_SERVICE, nullptr, (BYTE *)svc, sizeof svc, nullptr);
        const bool isk = !_wcsicmp(svc, L"libusbK");
        if (_wcsicmp(svc, L"WinUSB") && !isk) continue;
        SetupDiGetDeviceInstanceIdW(all, &di, inst, 256, nullptr);
        if (isk) {                                       // libusbK finds its devices by instance id
            out.push_back({inst, L"", true});
            continue;
        }
        HKEY k = SetupDiOpenDevRegKey(all, &di, DICS_FLAG_GLOBAL, 0, DIREG_DEV, KEY_READ);
        if (k == INVALID_HANDLE_VALUE) continue;
        wchar_t guids[512] = {};
        DWORD sz = sizeof guids, type = 0;
        LONG r = RegQueryValueExW(k, L"DeviceInterfaceGUIDs", nullptr, &type, (BYTE *)guids, &sz);
        if (r) {
            sz = sizeof guids;
            r = RegQueryValueExW(k, L"DeviceInterfaceGUID", nullptr, &type, (BYTE *)guids, &sz);
        }
        RegCloseKey(k);
        GUID g;
        if (r || CLSIDFromString(guids, &g) != NOERROR) continue;
        HDEVINFO h = SetupDiGetClassDevsW(&g, nullptr, nullptr, DIGCF_DEVICEINTERFACE | DIGCF_PRESENT);
        SP_DEVICE_INTERFACE_DATA id = {sizeof id};
        for (DWORD j = 0; SetupDiEnumDeviceInterfaces(h, nullptr, &g, j, &id); j++) {
            DWORD need = 0;
            SetupDiGetDeviceInterfaceDetailW(h, &id, nullptr, 0, &need, nullptr);
            std::vector<BYTE> buf(need);
            auto *d = (SP_DEVICE_INTERFACE_DETAIL_DATA_W *)buf.data();
            d->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W);
            SP_DEVINFO_DATA dd = {sizeof dd};
            if (SetupDiGetDeviceInterfaceDetailW(h, &id, d, need, nullptr, &dd)) {
                wchar_t inst2[256] = {};
                SetupDiGetDeviceInstanceIdW(h, &dd, inst2, 256, nullptr);
                if (!_wcsicmp(inst2, inst)) out.push_back({inst, d->DevicePath, false});
            }
        }
        SetupDiDestroyDeviceInfoList(h);
    }
    SetupDiDestroyDeviceInfoList(all);
    std::sort(out.begin(), out.end(), [](const Dev &a, const Dev &b) { return a.instance < b.instance; });   // by serial
    return out;
}

static int read_rate(WINUSB_INTERFACE_HANDLE h) {
    BYTE d[4] = {};
    ULONG got = 0;
    WINUSB_SETUP_PACKET s = {0xC0, 3, 1, 0, 4};
    if (!WinUsb_ControlTransfer(h, s, d, 4, &got, nullptr) || got != 4) return -1;
    return d[0] | d[1] << 8 | d[2] << 16;
}

static bool set_rate(WINUSB_INTERFACE_HANDLE h, int r) {
    BYTE d[4] = {0x40, (BYTE)r, (BYTE)(r >> 8), (BYTE)(r >> 16)};
    ULONG got = 0;
    WINUSB_SETUP_PACKET s = {0x40, 3, 8, 0, 4};
    if (!WinUsb_ControlTransfer(h, s, d, 4, &got, nullptr)) return false;
    for (int i = 0; i < 40; i++) {
        if (read_rate(h) == r) return true;
        Sleep(25);
    }
    return false;
}

static void wav_header(FILE *f, int rate, unsigned long long frames) {
    const unsigned data = (unsigned)std::min<unsigned long long>(frames * 3, 0xFFFFFFD0u);
    const unsigned riff = 36 + data, sr = rate, br = rate * 3, fmt = 16;
    const unsigned short pcm = 1, ch = 1, align = 3, bits = 24;
    fseek(f, 0, SEEK_SET);
    fwrite("RIFF", 1, 4, f), fwrite(&riff, 4, 1, f), fwrite("WAVEfmt ", 1, 8, f), fwrite(&fmt, 4, 1, f);
    fwrite(&pcm, 2, 1, f), fwrite(&ch, 2, 1, f), fwrite(&sr, 4, 1, f), fwrite(&br, 4, 1, f);
    fwrite(&align, 2, 1, f), fwrite(&bits, 2, 1, f), fwrite("data", 1, 4, f), fwrite(&data, 4, 1, f);
}

static const int PK = 16, NX = 32, INPUTS = 10;            // 2 ms transfers, 64 ms queued
static const int XFERS_PER_S = 8000 / PK, OUT_CH = 10, OUT_BPF = OUT_CH * 3, OUT_PK = 8, NO = 32, OUT_LEAD = 6;   // OUT: 1 ms writes
static std::atomic<bool> g_quit{false}, g_rec{false}, g_mon{false}, g_tone{false};

static inline int s24(const BYTE *p) { return (int)((unsigned)p[0] << 8 | (unsigned)p[1] << 16 | (unsigned)p[2] << 24); }

// monitor path: box 2's stream thread writes, box 1's stream thread reads
struct MonRing {
    static const unsigned N = 1 << 14;
    std::atomic<unsigned> w{0}, r{0};
    int d[N];
    unsigned level() const { return w.load(std::memory_order_acquire) - r.load(std::memory_order_acquire); }
    void push(int v) {
        const unsigned p = w.load(std::memory_order_relaxed);
        d[p & (N - 1)] = v;
        w.store(p + 1, std::memory_order_release);
    }
    int peek() const { return d[r.load(std::memory_order_relaxed) & (N - 1)]; }
    void skip(unsigned n) { r.store(r.load(std::memory_order_relaxed) + n, std::memory_order_release); }
    void flush() { r.store(w.load(std::memory_order_acquire), std::memory_order_release); }
};
static MonRing g_ring;
static Feed g_feed;                                     // send to stream (feed.h)

struct Block {
    std::vector<BYTE> d[INPUTS];
};

struct Box {
    int n = 0, rate = 44100, ch = 12, bpf = 36;
    HANDLE file = INVALID_HANDLE_VALUE;
    WINUSB_INTERFACE_HANDLE h0 = nullptr, h1 = nullptr;
    WINUSB_ISOCH_BUFFER_HANDLE bh = nullptr;
    kb::Dev *kd = nullptr;                               // set = this box is on libusbK and is the one that plays
    std::vector<void *> ih;                              // libusbK: one iso handle per IN transfer
    bool is_out = false;
    ULONG per = 0, start_frame = 0;
    std::vector<BYTE> buf;
    std::vector<OVERLAPPED> ov;
    std::vector<std::vector<USBD_ISO_PACKET_DESCRIPTOR>> pd;
    std::thread th, wr;
    // clock + take bookkeeping (stream thread writes; read after it has been told to finish the take)
    std::vector<std::pair<double, double>> sof;          // (QPC seconds, USB frame number)
    std::mutex sof_mu;
    long long tick = 0, rec_tick0 = -1, rec_ticks = 0, rec_samples = 0, dropouts = 0;
    bool recording = false;
    std::atomic<bool> take_done{false};
    // writer
    std::mutex mu;
    std::condition_variable cv;
    std::deque<std::unique_ptr<Block>> q;
    bool wr_stop = false, busy = false;
    FILE *out[INPUTS] = {};
    std::string path[INPUTS];
    double peak_rec[INPUTS] = {};

    // MIDI (interface 2, bulk 06/86): the CTRL link that the lab and Roland's parameter messages use
    WINUSB_INTERFACE_HANDLE h2 = nullptr;
    std::thread mt;
    std::vector<BYTE> sx[16];

    // playback (interface 0, iso OUT 0x05, 10 channels), libusbK only: WinUSB always fills packets to the endpoint's
    // maximum and the box crackles. Implicit feedback: every OUT microframe carries exactly as many frames as the
    // matching IN microframe delivered, so the box's own clock paces it, the way Roland's driver does.
    int alt = 0;
    std::atomic<bool> out_ready{false};
    std::vector<BYTE> obuf;
    std::vector<void *> oh;
    std::vector<OVERLAPPED> oov;
    std::vector<char> osub;
    ULONG oslot = 0, mbpi = 0;
    int oi = 0;
    bool oprimed = false, orun = false;
    double oavg = 0, ophase = 0, peak_mix = 0, ofrac = 0, orat = 1.0;
    float ohist[32] = {};                                // rd::SincTable::TAPS
    long long o_err = 0, o_und = 0, o_fix = 0, o_busy = 0;
    int o_last = 0, odd = 0;

    bool setup_out() {
        if (out_ready) return true;
        if (!kd) return false;
        if (!kb::set_alt(kd, 0, alt)) return false;
        mbpi = kb::mbpi(kd, 0, alt, 0x05);
        if (!mbpi) return false;
        say("P setup mbpi %lu\n", mbpi);
        kb::asap(kd, 0, 0x05);
        oslot = mbpi * OUT_PK;
        obuf.assign((size_t)oslot * NO, 0);
        oov.resize(NO);
        oh.assign(NO, nullptr);
        osub.assign(NO, 0);
        for (int i = 0; i < NO; i++) {
            oov[i].hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            oh[i] = kb::iso_new(kd, 0, 0x05, OUT_PK, obuf.data() + (size_t)i * oslot, oslot);
            if (!oh[i]) return false;
        }
        out_ready = true;
        return true;
    }

    // one OUT transfer (1 ms): fr[0..7] = frames for each microframe. Called from this box's stream thread.
    void out_send(const int *fr, bool silent) {
        OVERLAPPED &o = oov[oi];
        if (osub[oi]) {
            DWORD done = 0;
            if (!kb::result(kd, 0, &o, &done, FALSE)) {
                if (GetLastError() == ERROR_IO_INCOMPLETE) {     // this slot is still on the wire: skip, never overwrite it
                    o_busy++;
                    return;
                }
                if (o_err++ < 4) {
                    const DWORD e = GetLastError();
                    unsigned a = 0, l = 0, st[3] = {};
                    for (int j = 0; j < 3; j++) kb::iso_get(oh[oi], j, &a, &l, &st[j]);
                    say("P out failed: error %lu, packet status %x %x %x, last len %u\n", e, st[0], st[1], st[2], l);
                }
            }
        }
        BYTE *const base = obuf.data() + (size_t)oi * oslot;
        unsigned off = 0;
        int frames = 0;
        for (int j = 0; j < OUT_PK; j++) {
            const int f = std::min(fr[j], (int)(mbpi / OUT_BPF));
            kb::iso_set(oh[oi], j, off, f * OUT_BPF);
            off += f * OUT_BPF;
            frames += f;
        }
        memset(base, 0, off);
        const unsigned target = (unsigned)(2.5 * PK * rate / 8000);
        const bool tone = g_tone.load(), mon = g_mon.load() && !silent && !tone;
        unsigned lvl = mon ? g_ring.level() : 0;
        if (!mon) orun = false;
        if (mon && !orun && lvl >= target) {
            orun = true, oavg = lvl, ofrac = 0, orat = 1.0;
            memset(ohist, 0, sizeof ohist);
        }
        if (mon && orun) {                               // the two USB controllers differ by ~120 ppm: follow the ring's level
            oavg += ((double)lvl - oavg) * 0.01;
            if (lvl > MonRing::N / 2) g_ring.skip(lvl - target), lvl = target, oavg = lvl, o_fix++;
            orat = 1.0 + std::max(-5e-4, std::min(5e-4, (oavg - target) * 6e-6));
        }
        static const rd::SincTable table;
        const int T = rd::SincTable::TAPS;
        const double step = 2 * 3.14159265358979 * 997.0 / rate;
        BYTE *p = base;
        for (int i = 0; i < frames; i++, p += OUT_BPF) {
            int v = 0;
            if (tone && !silent) {
                v = (int)(std::sin(ophase) * 6790000.0);        // -50 dBFS
                ophase += step;
                if (ophase > 6.283185307179586) ophase -= 6.283185307179586;
            } else if (mon && orun) {
                ofrac += orat;
                while (ofrac >= 1.0 && orun) {
                    if (!lvl) {
                        orun = false;
                        o_und++;
                        break;
                    }
                    memmove(ohist, ohist + 1, (T - 1) * sizeof(float));
                    ohist[T - 1] = (float)(g_ring.peek() / 2147483648.0);
                    g_ring.skip(1), lvl--;
                    ofrac -= 1.0;
                }
                if (orun) {
                    const double ph = ofrac * rd::SincTable::PHASES;
                    const int p0 = (int)ph;
                    const float a = (float)(ph - p0);
                    const float *c0 = table.phase(p0), *c1 = table.phase(p0 + 1);
                    float s0 = 0, s1 = 0;
                    for (int k = 0; k < T; k++) s0 += c0[k] * ohist[k], s1 += c1[k] * ohist[k];
                    const double y = s0 + a * (s1 - s0);
                    v = (int)(std::max(-1.0, std::min(0.9999999, y)) * 2147483647.0);
                }
            }
            for (int c = 0; c < 2; c++) p[c * 3] = (BYTE)(v >> 8), p[c * 3 + 1] = (BYTE)(v >> 16), p[c * 3 + 2] = (BYTE)(v >> 24);
        }
        ResetEvent(o.hEvent);
        osub[oi] = 1;
        o_last = frames;
        const DWORD e = kb::iso_write(oh[oi], off, nullptr, OUT_PK, &o);     // no frame number: the pipe's ASAP policy applies
        if (e != ERROR_IO_PENDING) {
            if (o_err++ < 5) say("P write error %lu frames %d\n", e, frames);
            osub[oi] = 0;
        }
        oi = (oi + 1) % NO;
    }

    bool open(const Dev &d, int alt_) {
        alt = alt_;
        const int alt = alt_;
        if (d.libusbk) {
            kd = kb::open(d.instance.c_str());
            if (!kd) return false;
            return setup(alt);
        }
        file = CreateFileW(d.path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, nullptr);
        if (file == INVALID_HANDLE_VALUE || !WinUsb_Initialize(file, &h0)) return false;
        if (!WinUsb_GetAssociatedInterface(h0, 0, &h1) || !WinUsb_GetAssociatedInterface(h0, 1, &h2)) return false;
        return setup(alt);
    }

    void midi_rx() {                                     // USB-MIDI packets in; complete SysEx messages go to stdout
        BYTE b[512];
        OVERLAPPED o = {};
        o.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        while (!g_quit) {
            ResetEvent(o.hEvent);
            if (kd ? !kb::read_pipe(kd, 2, 0x86, b, sizeof b, &o)
                   : !WinUsb_ReadPipe(h2, 0x86, b, sizeof b, nullptr, &o) && GetLastError() != ERROR_IO_PENDING) {
                Sleep(10);
                continue;
            }
            DWORD n = 0;
            if (kd ? !kb::result(kd, 2, &o, &n, TRUE) : !WinUsb_GetOverlappedResult(h2, &o, &n, TRUE)) {
                if (!g_quit) Sleep(5);
                continue;
            }
            for (DWORD i = 0; i + 4 <= n; i += 4) {
                const int cable = b[i] >> 4, cin = b[i] & 15;
                const int len = cin == 4 || cin == 7 ? 3 : cin == 6 ? 2 : cin == 5 ? 1 : 0;
                if (!len) continue;
                sx[cable].insert(sx[cable].end(), b + i + 1, b + i + 1 + len);
                if (cin == 4) continue;
                if (sx[cable][0] == 0xF0) {
                    std::string h;
                    char t[4];
                    for (BYTE v : sx[cable]) snprintf(t, sizeof t, "%02x", v), h += t;
                    say("S %d %s\n", this->n, h.c_str());
                }
                sx[cable].clear();
            }
        }
        CloseHandle(o.hEvent);
    }

    void midi_send(const std::vector<BYTE> &m) {         // one SysEx message, out on cable 2 (as Roland's driver does)
        std::vector<BYTE> pk;
        for (size_t i = 0; i < m.size();) {
            const size_t left = m.size() - i;
            const int cin = left > 3 ? 4 : left == 3 ? 7 : left == 2 ? 6 : 5, take = left > 3 ? 3 : (int)left;
            pk.push_back((BYTE)(2 << 4 | cin));
            for (int k = 0; k < 3; k++) pk.push_back(k < take ? m[i + k] : 0);
            i += take;
        }
        OVERLAPPED o = {};
        o.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        DWORD n = 0;
        if (kd) {
            if (kb::write_pipe(kd, 2, 0x06, pk.data(), (unsigned)pk.size(), &o)) kb::result(kd, 2, &o, &n, TRUE);
        } else if (WinUsb_WritePipe(h2, 0x06, pk.data(), (ULONG)pk.size(), nullptr, &o) || GetLastError() == ERROR_IO_PENDING) {
            WinUsb_GetOverlappedResult(h2, &o, &n, TRUE);
        }
        CloseHandle(o.hEvent);
    }
    bool setup(int alt) {
        if (kd) {
            if (!kb::set_alt(kd, 1, alt)) return false;
            per = kb::mbpi(kd, 1, alt, 0x85);
            if (!per) return false;
            buf.resize((size_t)per * PK * NX);
            ov.resize(NX);
            ih.assign(NX, nullptr);
            pd.assign(NX, std::vector<USBD_ISO_PACKET_DESCRIPTOR>(PK));
            for (int x = 0; x < NX; x++) {
                ov[x].hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
                ih[x] = kb::iso_new(kd, 1, 0x85, PK, buf.data() + (size_t)x * per * PK, per * PK);
                if (!ih[x] || !kb::iso_offsets(ih[x], per)) return false;
            }
            return true;
        }
        if (!WinUsb_SetCurrentAlternateSetting(h1, (UCHAR)alt)) return false;
        WINUSB_PIPE_INFORMATION_EX pi = {};
        for (UCHAR k = 0; WinUsb_QueryPipeEx(h1, (UCHAR)alt, k, &pi); k++)
            if (pi.PipeId == 0x85) break;
        per = pi.MaximumBytesPerInterval;
        buf.resize((size_t)per * PK * NX);
        if (!WinUsb_RegisterIsochBuffer(h1, 0x85, buf.data(), (ULONG)buf.size(), &bh)) return false;
        ov.resize(NX);
        pd.assign(NX, std::vector<USBD_ISO_PACKET_DESCRIPTOR>(PK));
        for (auto &o : ov) o.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        return true;
    }
    ULONG now_frame(LARGE_INTEGER *q) {                  // USB frame number + the PC clock at that moment
        if (!kd) {
            ULONG cf = 0;
            WinUsb_GetCurrentFrameNumber(h1, &cf, q);
            return cf;
        }
        LARGE_INTEGER a, b;
        unsigned cf = 0;
        QueryPerformanceCounter(&a);
        kb::frame(kd, 1, &cf);
        QueryPerformanceCounter(&b);
        q->QuadPart = (a.QuadPart + b.QuadPart) / 2;
        return cf;
    }
    bool submit_in(int x, ULONG frame) {
        if (kd) {
            unsigned f = frame;
            return kb::iso_read(ih[x], per * PK, &f, PK, &ov[x]) == ERROR_IO_PENDING;
        }
        ULONG f = frame;
        return WinUsb_ReadIsochPipe(bh, (ULONG)((size_t)x * per * PK), per * PK, &f, PK, pd[x].data(), &ov[x]) ||
               GetLastError() == ERROR_IO_PENDING;
    }
    bool start() {
        LARGE_INTEGER q;
        start_frame = now_frame(&q) + 64;
        for (int x = 0; x < NX; x++)
            if (!submit_in(x, start_frame + x * PK / 8)) return false;
        th = std::thread([this] { stream(); });
        wr = std::thread([this] { writer(); });
        mt = std::thread([this] { midi_rx(); });
        return true;
    }

    void writer() {
        for (;;) {
            std::unique_ptr<Block> b;
            {
                std::unique_lock<std::mutex> lk(mu);
                cv.wait(lk, [&] { return wr_stop || !q.empty(); });
                if (q.empty()) return;
                b = std::move(q.front());
                q.pop_front();
                busy = true;
            }
            for (int c = 0; c < INPUTS; c++)
                if (out[c] && !b->d[c].empty()) fwrite(b->d[c].data(), 1, b->d[c].size(), out[c]);
            std::lock_guard<std::mutex> lk(mu);
            busy = false;
        }
    }
    bool drained() {
        std::lock_guard<std::mutex> lk(mu);
        return q.empty() && !busy;
    }

    void stream() {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
        LARGE_INTEGER freq;
        QueryPerformanceFrequency(&freq);
        double peak[INPUTS] = {};
        ULONG next = start_frame + NX * PK / 8;
        int x = 0, xfers = 0;
        const int inputs = std::min(INPUTS, ch);
        while (!g_quit) {
            DWORD got = 0;
            if (kd ? !kb::result(kd, 1, &ov[x], &got, TRUE) : !WinUsb_GetOverlappedResult(h1, &ov[x], &got, TRUE)) {
                dropouts++;
                if (GetLastError() == ERROR_GEN_FAILURE || GetLastError() == ERROR_DEVICE_NOT_CONNECTED) {
                    say("ERR box %d disconnected\n", n + 1);
                    return;
                }
            }
            const bool want = g_rec.load();
            if (want && !recording) {                    // take begins on this transfer boundary
                recording = true;
                rec_tick0 = tick;
                rec_ticks = rec_samples = 0;
                memset(peak_rec, 0, sizeof peak_rec);
            }
            auto block = recording && want ? std::make_unique<Block>() : nullptr;
            int frames_uf[PK] = {};                      // what the box delivered per microframe: what it wants back
            for (int k = 0; k < PK; k++) {
                unsigned off = 0, len = 0, st = 0;
                if (kd) kb::iso_get(ih[x], k, &off, &len, &st);
                else off = pd[x][k].Offset, len = pd[x][k].Length, st = pd[x][k].Status;
                const BYTE *s = buf.data() + (size_t)x * per * PK + off;
                int fr = (int)(len / bpf);
                if (st) dropouts++;
                frames_uf[k] = fr;
                if (!is_out && g_mon) {                   // box 2's analog inputs, summed, on the way to box 1's OUT 1-2
                    const BYTE *m = s;
                    for (int i = 0; i < fr; i++, m += bpf) {
                        long long sum = 0;
                        for (int c = 0; c < std::min(8, ch); c++) sum += s24(m + c * 3);
                        g_ring.push((int)std::max<long long>(-2147483647LL, std::min<long long>(2147483647LL, sum)));
                    }
                }
                g_feed.capture(n, s, fr, bpf, ch);       // inputs ticked for the stream (after their strips), mixed
                if (ch >= 12) {                          // this box's main mix (capture 11-12): shows what its outputs carry
                    const BYTE *m = s;
                    for (int i = 0; i < fr; i++, m += bpf) {
                        const double a = std::fabs(s24(m + 30) / 2147483648.0);
                        if (a > peak_mix) peak_mix = a;
                        static FILE *const dump = getenv("RD_MIXDUMP") ? fopen(getenv("RD_MIXDUMP"), "wb") : nullptr;
                        if (dump && is_out && out_ready) fwrite(m + 30, 1, 3, dump);     // bench test only (tests/mixcheck.py)
                    }
                }
                for (int c = 0; c < inputs; c++) {
                    const BYTE *p = s + c * 3;
                    for (int i = 0; i < fr; i++, p += bpf) {
                        const int v = (int)((unsigned)p[0] << 8 | (unsigned)p[1] << 16 | (unsigned)p[2] << 24);
                        const double a = std::fabs(v / 2147483648.0);
                        if (a > peak[c]) peak[c] = a;
                        if (block && a > peak_rec[c]) peak_rec[c] = a;
                        if (block) block->d[c].insert(block->d[c].end(), p, p + 3);
                    }
                }
                if (block) rec_samples += fr, rec_ticks++;
                tick++;
            }
            if (block) {
                std::lock_guard<std::mutex> lk(mu);
                q.push_back(std::move(block));
                cv.notify_one();
            } else if (recording && !want) {             // take ended on this boundary
                recording = false;
                take_done = true;
            }
            {
                int sum = 0, zero = 0;
                for (int v : frames_uf) sum += v, zero += !v;
                const int nom = rate * PK / 8000;
                if ((sum < nom - 3 || sum > nom + 3) && odd++ < 40)
                    say("P odd box %d xfer %d frames %d (expected %d) empty packets %d\n", n, xfers, sum, nom, zero);
            }
            if (is_out && out_ready) {
                if (!oprimed) {                          // a few silent transfers set how far ahead of the wire we stay
                    oprimed = true;
                    for (int i = 0; i < OUT_LEAD; i++) out_send(frames_uf, true);
                }
                for (int j = 0; j + OUT_PK <= PK; j += OUT_PK) out_send(frames_uf + j, false);
            }
            if ((xfers + 1) % XFERS_PER_S == 0 && (is_out ? out_ready.load() : g_mon.load())) {
                say("P box %d frames %d level %u avg %.0f und %lld fix %lld busy %lld oerr %lld in_drop %lld mix %.1f\n", n, o_last,
                    g_ring.level(), oavg, o_und, o_fix, o_busy, o_err, dropouts, peak_mix > 1e-6 ? 20 * std::log10(peak_mix) : -120.0);
                peak_mix = 0;
            }
            if (++xfers % XFERS_PER_S == 0) {            // ~1 s: this controller's USB clock vs the PC clock
                LARGE_INTEGER q2;
                const ULONG cf = now_frame(&q2);
                {
                    std::lock_guard<std::mutex> lk(sof_mu);
                    sof.push_back({(double)q2.QuadPart / freq.QuadPart, (double)cf});
                }
            }
            if (xfers % (XFERS_PER_S / 15) == 0) {       // ~15 meter updates per second
                char line[256];
                int o = snprintf(line, sizeof line, "M %d", n);
                for (int c = 0; c < INPUTS; c++) {
                    o += snprintf(line + o, sizeof line - o, " %.1f", peak[c] > 1e-6 ? 20 * std::log10(peak[c]) : -120.0);
                    peak[c] = 0;
                }
                say("%s\n", line);
            }
            ResetEvent(ov[x].hEvent);
            ULONG f = next;
            next += PK / 8;
            if (!submit_in(x, f)) dropouts++;
            x = (x + 1) % NX;
        }
    }

    // this controller's USB clock over the take: QPC seconds = a + b * frame
    void fit(double &a, double &b) {
        std::vector<std::pair<double, double>> pts;
        {
            std::lock_guard<std::mutex> lk(sof_mu);
            pts = sof;
        }
        if (pts.size() < 2) {                            // too short to measure: assume the nominal USB clock
            b = 0.001;
            a = pts.empty() ? 0 : pts[0].first - b * pts[0].second;
            return;
        }
        double mx = 0, my = 0, xx = 0, xy = 0;
        const size_t m = pts.size();
        const double x0 = pts[0].second, y0 = pts[0].first;
        for (auto &p : pts) mx += p.second - x0, my += p.first - y0;
        mx /= m, my /= m;
        for (auto &p : pts) {
            const double u = p.second - x0 - mx, v = p.first - y0 - my;
            xx += u * u, xy += u * v;
        }
        b = xy / xx;
        a = y0 + my - b * (x0 + mx);
    }
    double frame_of_tick(long long t) const { return start_frame + t / 8.0; }
};

static bool begin_take(std::vector<std::unique_ptr<Box>> &boxes, const std::string &dir) {
    for (auto &b : boxes) {
        const int inputs = std::min(INPUTS, b->ch);
        for (int c = 0; c < inputs; c++) {
            char name[32];
            snprintf(name, sizeof name, "\\b%d_in%02d.wav", b->n + 1, c + 1);
            b->path[c] = dir + name;
            b->out[c] = fopen(b->path[c].c_str(), "wb");
            if (!b->out[c]) return false;
            setvbuf(b->out[c], nullptr, _IOFBF, 1 << 20);
            wav_header(b->out[c], b->rate, 0);
        }
        b->take_done = false;
    }
    g_rec = true;
    return true;
}

// box 2's track, re-timed onto box 1's timeline: position in box 2 = offset + n * step
static void retime(const std::string &path, double offset, double step, unsigned long long out_frames, int rate) {
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) return;
    _fseeki64(f, 0, SEEK_END);
    const long long bytes = _ftelli64(f) - 44;
    _fseeki64(f, 44, SEEK_SET);
    const long long n = bytes / 3;
    std::vector<BYTE> raw((size_t)bytes);
    const size_t rd = fread(raw.data(), 1, raw.size(), f);
    fclose(f);
    if ((long long)rd != bytes) return;
    std::vector<float> x((size_t)n);
    for (long long i = 0; i < n; i++) {
        const int v = (int)((unsigned)raw[3 * i] << 8 | (unsigned)raw[3 * i + 1] << 16 | (unsigned)raw[3 * i + 2] << 24);
        x[i] = (float)(v / 2147483648.0);
    }
    static const rd::SincTable table;
    const int T = rd::SincTable::TAPS;
    f = fopen(path.c_str(), "wb");
    if (!f) return;
    setvbuf(f, nullptr, _IOFBF, 1 << 20);
    wav_header(f, rate, out_frames);
    _fseeki64(f, 44, SEEK_SET);
    for (unsigned long long i = 0; i < out_frames; i++) {
        const double pos = offset + (double)i * step;
        const double fl = std::floor(pos);
        const long long i0 = (long long)fl;
        double y = 0;
        if (i0 - (T / 2 - 1) >= 0 && i0 + T / 2 < n) {
            const double ph = (pos - fl) * rd::SincTable::PHASES;
            const int p0 = (int)ph;
            const float a = (float)(ph - p0);
            const float *c0 = table.phase(p0), *c1 = table.phase(p0 + 1);
            const float *w = &x[(size_t)(i0 - (T / 2 - 1))];
            float s0 = 0, s1 = 0;
            for (int k = 0; k < T; k++) s0 += c0[k] * w[k], s1 += c1[k] * w[k];
            y = s0 + a * (s1 - s0);
        }
        y = y > 0.9999999 ? 0.9999999 : y < -1.0 ? -1.0 : y;
        const int v = (int)std::lround(y * 8388607.0);
        const BYTE b[3] = {(BYTE)v, (BYTE)(v >> 8), (BYTE)(v >> 16)};
        fwrite(b, 1, 3, f);
    }
    fclose(f);
}

static void end_take(std::vector<std::unique_ptr<Box>> &boxes) {
    g_rec = false;
    for (auto &b : boxes)
        while (!b->take_done && !g_quit) Sleep(5);
    for (auto &b : boxes) {                              // let each writer drain, then close the files
        while (!b->drained()) Sleep(2);
        for (int c = 0; c < INPUTS; c++) {
            if (!b->out[c]) continue;
            wav_header(b->out[c], b->rate, (unsigned long long)b->rec_samples);
            fclose(b->out[c]);
            b->out[c] = nullptr;
        }
    }
    Box &m = *boxes[0];
    double ma = 0, mb = 1;
    m.fit(ma, mb);
    const double m_t0 = ma + mb * m.frame_of_tick(m.rec_tick0);
    const double m_dur = mb * (m.rec_ticks / 8.0);
    const double m_rate = m.rec_samples / m_dur;
    double ppm = 0;
    long long dropouts = m.dropouts;
    std::string kept;
    for (auto &b : boxes) {
        double a = ma, bb = mb;
        if (b->n) b->fit(a, bb);
        const double t0 = a + bb * b->frame_of_tick(b->rec_tick0);
        const double rate = b->rec_samples / (bb * (b->rec_ticks / 8.0));
        const double offset = (m_t0 - t0) * rate, step = rate / m_rate;
        if (b->n) {
            ppm = (step - 1) * 1e6;
            dropouts += b->dropouts;
        }
        for (int c = 0; c < std::min(INPUTS, b->ch); c++) {
            if (b->path[c].empty()) continue;
            if (b->peak_rec[c] < 0.001 && !getenv("RD_KEEP")) {                // never above -60 dBFS: nothing was plugged in
                DeleteFileA(b->path[c].c_str());
                continue;
            }
            if (b->n) retime(b->path[c], offset, step, (unsigned long long)m.rec_samples, m.rate);
            char t[16];
            snprintf(t, sizeof t, " %d:%d", b->n + 1, c + 1);
            kept += t;
        }
    }
    say("DONE %.2f %lld %.2f%s\n", m.rec_samples / (double)m.rate, dropouts, ppm, kept.c_str());
}

int main() {
    auto devs = find_octas();
    if (devs.empty()) {
        say("NOBOX\n");
        return 0;
    }
    std::vector<std::unique_ptr<Box>> boxes;
    int rate = 0;
    for (size_t i = 0; i < devs.size() && i < 2; i++) {
        auto b = std::make_unique<Box>();
        b->n = (int)i;
        if (devs[i].libusbk) {
            kb::Dev *k = kb::open(devs[i].instance.c_str());
            BYTE d[4] = {};
            if (!k || kb::control(k, 0xC0, 3, 1, 0, d, 4) != 4) {
                say("ERR cannot open box %zu on libusbK (error %lu)\n", i + 1, GetLastError());
                return 1;
            }
            const int r = d[0] | d[1] << 8 | d[2] << 16;
            if (i == 0) rate = r;
            else if (r != rate) {
                BYTE w[4] = {0x40, (BYTE)rate, (BYTE)(rate >> 8), (BYTE)(rate >> 16)};
                kb::control(k, 0x40, 3, 8, 0, w, 4);
                Sleep(500);
            }
            kb::close(k);
        } else {
            HANDLE f = CreateFileW(devs[i].path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                                   OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, nullptr);
            WINUSB_INTERFACE_HANDLE h = nullptr;
            if (f == INVALID_HANDLE_VALUE || !WinUsb_Initialize(f, &h)) {
                say("ERR cannot open box %zu (error %lu)\n", i + 1, GetLastError());
                return 1;
            }
            const int r = read_rate(h);
            if (i == 0) rate = r;
            else if (r != rate && !set_rate(h, rate)) {
                say("ERR box 2 will not change to %d Hz\n", rate);
                return 1;
            }
            WinUsb_Free(h);
            CloseHandle(f);
        }
        const int alt = rate == 44100 ? 1 : rate == 48000 ? 2 : rate == 96000 ? 3 : rate == 192000 ? 4 : 0;
        if (!alt) {
            say("ERR unsupported rate %d\n", rate);
            return 1;
        }
        b->rate = rate;
        b->ch = rate == 192000 ? 4 : 12;
        b->bpf = b->ch * 3;
        if (!b->open(devs[i], alt)) {
            say("ERR box %zu setup failed (error %lu)\n", i + 1, GetLastError());
            return 1;
        }
        boxes.push_back(std::move(b));
    }
    {                                                    // the first box on libusbK is the one you listen on (RD_PLAYER: bench override)
        const int want = getenv("RD_PLAYER") ? atoi(getenv("RD_PLAYER")) : -1;
        for (auto &b : boxes)
            if (b->kd && (want < 0 || b->n == want)) {
                b->is_out = true;
                break;
            }
    }
    for (auto &b : boxes)
        if (!b->start()) {
            say("ERR box %d start failed (error %lu)\n", b->n + 1, GetLastError());
            return 1;
        }
    {                                                    // READY <boxes> <rate> <serial of box 1> [<serial of box 2>]
        std::string s = "READY " + std::to_string(boxes.size()) + " " + std::to_string(rate);
        for (size_t i = 0; i < boxes.size(); i++) {      // the app tells the boxes apart by these, not by their order
            const std::wstring &w = devs[i].instance;
            const size_t p = w.find_last_of(L'\\');
            s += ' ';
            for (wchar_t c : (p == std::wstring::npos ? w : w.substr(p + 1)))
                s += (c > 32 && c < 127) ? (char)c : '_';
        }
        say("%s\n", s.c_str());
    }
    char line[1024];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "rec ", 4)) {
            if (g_rec) continue;
            if (begin_take(boxes, line + 4))
                say("REC\n");
            else
                say("ERR cannot create files in %s\n", line + 4);
        } else if (!strncmp(line, "sx ", 3)) {           // sx <box> <hex of the whole SysEx message>
            int box = line[3] - '0';
            const char *h = line + 5;
            std::vector<BYTE> m;
            for (; h[0] && h[1]; h += 2) {
                unsigned v = 0;
                sscanf(h, "%2x", &v);
                m.push_back((BYTE)v);
            }
            if (box >= 0 && box < (int)boxes.size() && !m.empty()) boxes[box]->midi_send(m);
        } else if (!strncmp(line, "mon ", 4)) {          // mon 1 = box 2's inputs out of box 1, mon 0 = off, mon t = test tone
            const char m = line[4];
            Box *player = nullptr;
            for (auto &b : boxes)
                if (b->is_out) player = b.get();
            if (m != '0' && (!player || !player->setup_out())) {
                say(player ? "ERR playback setup failed (error %lu)\n" : "ERR no box is on libusbK (error %lu)\n", GetLastError());
                continue;
            }
            g_tone = m == 't';
            if (m == '1') g_ring.flush();
            g_mon = m != '0';
        } else if (!strncmp(line, "feed ", 5)) {         // feed <part of a playback device's name> | feed off
            if (!strcmp(line + 5, "off")) {
                g_feed.end();
                say("FEED off\n");
            } else {
                const int need = MultiByteToWideChar(CP_UTF8, 0, line + 5, -1, nullptr, 0);
                std::wstring w(need > 0 ? need - 1 : 0, L' ');
                if (need > 1) MultiByteToWideChar(CP_UTF8, 0, line + 5, -1, &w[0], need);
                g_feed.start(w, rate, [](const char *fmt, auto... a) { say(fmt, a...); });
            }
        } else if (!strncmp(line, "fgain ", 6)) {        // fgain <box> <10 gains, input 1-10>: 0 = not sent
            int box = -1;
            float g[Feed::CH] = {};
            const int got = sscanf(line + 6, "%d %f %f %f %f %f %f %f %f %f %f", &box, g, g + 1, g + 2, g + 3, g + 4, g + 5, g + 6,
                                   g + 7, g + 8, g + 9);
            if (got >= 2) g_feed.set_gains(box, g);
        } else if (!strcmp(line, "stop")) {
            if (g_rec) end_take(boxes);
        } else if (!strcmp(line, "quit")) {
            break;
        }
    }
    if (g_rec) end_take(boxes);
    g_feed.end();
    g_quit = true;
    for (auto &b : boxes) {
        if (b->kd) {
            kb::abort(b->kd, 1, 0x85);
            kb::abort(b->kd, 2, 0x86);
            if (b->out_ready) kb::abort(b->kd, 0, 0x05);
        } else {
            WinUsb_AbortPipe(b->h1, 0x85);
            WinUsb_AbortPipe(b->h2, 0x86);
        }
        { std::lock_guard<std::mutex> lk(b->mu); b->wr_stop = true; }
        b->cv.notify_all();
        if (b->th.joinable()) b->th.join();
        if (b->mt.joinable()) b->mt.join();
        if (b->wr.joinable()) b->wr.join();
        if (b->kd) {
            for (void *i : b->ih) kb::iso_free(i);
            for (void *i : b->oh) kb::iso_free(i);
            kb::set_alt(b->kd, 1, 0);
            if (b->out_ready) kb::set_alt(b->kd, 0, 0);
            kb::close(b->kd);
        } else {
            WinUsb_UnregisterIsochBuffer(b->bh);
            WinUsb_SetCurrentAlternateSetting(b->h1, 0);
        }
    }
    return 0;
}
