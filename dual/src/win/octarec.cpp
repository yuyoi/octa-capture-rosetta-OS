// octarec - the recording engine behind Rosetta Octa Control's REC button. Streams every OCTA-CAPTURE on WinUSB (no
// Roland driver), reports input meters, and records inputs 1-10 of each box to 24-bit mono WAVs. When a take stops,
// box 2's kept tracks are re-timed onto box 1's timeline, using each box's exact sample count per USB tick and each USB
// controller's clock against the PC clock.
//
// stdin:  rec <folder>   start a take (files: b1_in01.wav ... b2_in10.wav)
//         stop           finish it (silent tracks are deleted)
//         quit
//         sx <box> <hex>  send one SysEx message to a box (lab / Roland parameter messages)
// stdout: READY <boxes> <rate> | NOBOX | M <box> <10 x peak dBFS> | REC | DONE <seconds> <dropouts> <ratio_ppm> <kept...>
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
        if (_wcsicmp(svc, L"WinUSB")) continue;
        SetupDiGetDeviceInstanceIdW(all, &di, inst, 256, nullptr);
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
                if (!_wcsicmp(inst2, inst)) out.push_back({inst, d->DevicePath});
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

static const int PK = 64, NX = 8, INPUTS = 10;
static std::atomic<bool> g_quit{false}, g_rec{false};

struct Block {
    std::vector<BYTE> d[INPUTS];
};

struct Box {
    int n = 0, rate = 44100, ch = 12, bpf = 36;
    HANDLE file = INVALID_HANDLE_VALUE;
    WINUSB_INTERFACE_HANDLE h0 = nullptr, h1 = nullptr;
    WINUSB_ISOCH_BUFFER_HANDLE bh = nullptr;
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

    bool open(const Dev &d, int alt) {
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
            if (!WinUsb_ReadPipe(h2, 0x86, b, sizeof b, nullptr, &o) && GetLastError() != ERROR_IO_PENDING) {
                Sleep(10);
                continue;
            }
            DWORD n = 0;
            if (!WinUsb_GetOverlappedResult(h2, &o, &n, TRUE)) {
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
        if (WinUsb_WritePipe(h2, 0x06, pk.data(), (ULONG)pk.size(), nullptr, &o) || GetLastError() == ERROR_IO_PENDING)
            WinUsb_GetOverlappedResult(h2, &o, &n, TRUE);
        CloseHandle(o.hEvent);
    }
    bool setup(int alt) {
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
    bool start() {
        ULONG cur = 0;
        LARGE_INTEGER q;
        WinUsb_GetCurrentFrameNumber(h1, &cur, &q);
        start_frame = cur + 64;
        ULONG f = start_frame;
        for (int x = 0; x < NX; x++)
            if (!WinUsb_ReadIsochPipe(bh, (ULONG)(x * per * PK), per * PK, &f, PK, pd[x].data(), &ov[x]) &&
                GetLastError() != ERROR_IO_PENDING)
                return false;
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
            if (!WinUsb_GetOverlappedResult(h1, &ov[x], &got, TRUE)) {
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
            for (int k = 0; k < PK; k++) {
                const BYTE *s = buf.data() + (size_t)x * per * PK + pd[x][k].Offset;
                int fr = (int)(pd[x][k].Length / bpf);
                if (pd[x][k].Status) dropouts++;
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
            if (++xfers % 125 == 0) {                    // ~1 s: this controller's USB clock vs the PC clock
                ULONG cf = 0;
                LARGE_INTEGER q2;
                if (WinUsb_GetCurrentFrameNumber(h1, &cf, &q2)) {
                    std::lock_guard<std::mutex> lk(sof_mu);
                    sof.push_back({(double)q2.QuadPart / freq.QuadPart, (double)cf});
                }
            }
            if (xfers % 8 == 0) {                        // ~15 meter updates per second
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
            if (!WinUsb_ReadIsochPipe(bh, (ULONG)((size_t)x * per * PK), per * PK, &f, PK, pd[x].data(), &ov[x]) &&
                GetLastError() != ERROR_IO_PENDING)
                dropouts++;
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
    for (auto &b : boxes)
        if (!b->start()) {
            say("ERR box %d start failed (error %lu)\n", b->n + 1, GetLastError());
            return 1;
        }
    say("READY %zu %d\n", boxes.size(), rate);
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
        } else if (!strcmp(line, "stop")) {
            if (g_rec) end_take(boxes);
        } else if (!strcmp(line, "quit")) {
            break;
        }
    }
    if (g_rec) end_take(boxes);
    g_quit = true;
    for (auto &b : boxes) {
        WinUsb_AbortPipe(b->h1, 0x85);
        WinUsb_AbortPipe(b->h2, 0x86);
        { std::lock_guard<std::mutex> lk(b->mu); b->wr_stop = true; }
        b->cv.notify_all();
        if (b->th.joinable()) b->th.join();
        if (b->mt.joinable()) b->mt.join();
        if (b->wr.joinable()) b->wr.join();
        WinUsb_UnregisterIsochBuffer(b->bh);
        WinUsb_SetCurrentAlternateSetting(b->h1, 0);
    }
    return 0;
}
