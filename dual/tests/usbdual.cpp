// Rosetta Dual - both OCTA-CAPTUREs on WinUSB at once: stream both recording endpoints, starting on an agreed USB
// frame, and compare the two boxes' sample counts tick by tick (USB microframes, 8000/s).
//   usbdual [seconds]
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <windows.h>
#include <initguid.h>
#include <setupapi.h>
#include <winusb.h>
#include <usb.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>
#include <thread>
#include <vector>

#pragma comment(lib, "winusb.lib")
#pragma comment(lib, "setupapi.lib")

struct Dev {
    std::wstring instance, path, location;
};

static std::vector<Dev> find_octas() {
    std::vector<Dev> out;
    HDEVINFO all = SetupDiGetClassDevsW(nullptr, L"USB", nullptr, DIGCF_ALLCLASSES | DIGCF_PRESENT);
    SP_DEVINFO_DATA di = {sizeof di};
    for (DWORD i = 0; SetupDiEnumDeviceInfo(all, i, &di); i++) {
        wchar_t hw[512] = {}, svc[64] = {}, inst[256] = {}, loc[512] = {};
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_HARDWAREID, nullptr, (BYTE *)hw, sizeof hw, nullptr);
        if (!wcsstr(hw, L"VID_0582&PID_0120")) continue;
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_SERVICE, nullptr, (BYTE *)svc, sizeof svc, nullptr);
        if (_wcsicmp(svc, L"WinUSB")) continue;
        SetupDiGetDeviceInstanceIdW(all, &di, inst, 256, nullptr);
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_LOCATION_PATHS, nullptr, (BYTE *)loc, sizeof loc, nullptr);
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
                if (!_wcsicmp(inst2, inst)) out.push_back({inst, d->DevicePath, loc});
            }
        }
        SetupDiDestroyDeviceInfoList(h);
    }
    SetupDiDestroyDeviceInfoList(all);
    return out;
}

static const int PK = 64, NX = 8, BPF = 36;              // 8 ms per transfer, 8 queued, 12 ch x 3 bytes

struct Cap {
    HANDLE file = INVALID_HANDLE_VALUE;
    WINUSB_INTERFACE_HANDLE h0 = nullptr, h1 = nullptr;
    WINUSB_ISOCH_BUFFER_HANDLE bh = nullptr;
    ULONG per = 0;
    std::vector<BYTE> buf;
    std::vector<OVERLAPPED> ov;
    std::vector<std::vector<USBD_ISO_PACKET_DESCRIPTOR>> pd;
    ULONG start_frame = 0;
    LARGE_INTEGER start_qpc = {};
    std::vector<int> counts;                             // samples per microframe, in order
    long long errors = 0, short_zero = 0;
    int tick = 0;
    std::vector<std::pair<double, double>> sof;          // (QPC ticks, USB frame number)
    bool open(const Dev &d) {
        file = CreateFileW(d.path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, nullptr);
        if (file == INVALID_HANDLE_VALUE || !WinUsb_Initialize(file, &h0)) return false;
        if (!WinUsb_GetAssociatedInterface(h0, 0, &h1)) return false;
        if (!WinUsb_SetCurrentAlternateSetting(h1, 1)) return false;
        WINUSB_PIPE_INFORMATION_EX pi = {};
        for (UCHAR k = 0; WinUsb_QueryPipeEx(h1, 1, k, &pi); k++)
            if (pi.PipeId == 0x85) break;
        per = pi.MaximumBytesPerInterval;
        buf.resize((size_t)per * PK * NX);
        if (!WinUsb_RegisterIsochBuffer(h1, 0x85, buf.data(), (ULONG)buf.size(), &bh)) return false;
        ov.resize(NX);
        pd.assign(NX, std::vector<USBD_ISO_PACKET_DESCRIPTOR>(PK));
        for (auto &o : ov) o.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        return true;
    }
    bool start(ULONG frame) {
        start_frame = frame;
        ULONG f = frame;
        for (int x = 0; x < NX; x++) {
            if (!WinUsb_ReadIsochPipe(bh, (ULONG)(x * per * PK), per * PK, &f, PK, pd[x].data(), &ov[x]) &&
                GetLastError() != ERROR_IO_PENDING) {
                printf("ReadIsochPipe failed (%lu) at transfer %d\n", GetLastError(), x);
                return false;
            }
        }
        return true;
    }
    void run(long long packets) {
        counts.reserve((size_t)packets + PK);
        ULONG next = start_frame + (ULONG)(NX * PK / 8);
        int x = 0;
        while ((long long)counts.size() < packets) {
            DWORD n = 0;
            if (!WinUsb_GetOverlappedResult(h1, &ov[x], &n, TRUE)) errors++;
            for (int k = 0; k < PK; k++) {
                if (pd[x][k].Status) errors++;
                counts.push_back((int)(pd[x][k].Length / BPF));
            }
            if (++tick % 125 == 0) {                     // ~1 s: where is this controller's USB clock vs the PC clock
                ULONG cf = 0;
                LARGE_INTEGER q;
                if (WinUsb_GetCurrentFrameNumber(h1, &cf, &q)) sof.push_back({(double)q.QuadPart, (double)cf});
            }
            ResetEvent(ov[x].hEvent);
            ULONG f = next;
            next += PK / 8;
            if (!WinUsb_ReadIsochPipe(bh, (ULONG)(x * per * PK), per * PK, &f, PK, pd[x].data(), &ov[x]) &&
                GetLastError() != ERROR_IO_PENDING)
                errors++;
            x = (x + 1) % NX;
        }
    }
};

int main(int argc, char **argv) {
    const double secs = argc > 1 ? atof(argv[1]) : 30;
    auto devs = find_octas();
    for (auto &d : devs) wprintf(L"%s  at  %s\n", d.instance.c_str(), d.location.c_str());
    if (devs.size() < 2) {
        printf("need two OCTAs on WinUSB\n");
        return 1;
    }
    Cap c[2];
    for (int u = 0; u < 2; u++)
        if (!c[u].open(devs[u])) {
            printf("unit %d: open failed (%lu)\n", u + 1, GetLastError());
            return 1;
        }
    ULONG cur[2];
    LARGE_INTEGER qpc[2];
    for (int u = 0; u < 2; u++) WinUsb_GetCurrentFrameNumber(c[u].h1, &cur[u], &qpc[u]);
    LARGE_INTEGER freq;
    QueryPerformanceFrequency(&freq);
    printf("USB frame now: %lu / %lu (QPC apart %.1f us)\n", cur[0], cur[1],
           (qpc[1].QuadPart - qpc[0].QuadPart) * 1e6 / freq.QuadPart);
    // start both 64 ms from now; if they are on one controller the frame numbers are the same clock
    const long long packets = (long long)(secs * 8000);
    for (int u = 0; u < 2; u++)
        if (!c[u].start(cur[u] + 64)) return 1;
    std::thread t0([&] { c[0].run(packets); }), t1([&] { c[1].run(packets); });
    t0.join();
    t1.join();
    for (int u = 0; u < 2; u++) WinUsb_AbortPipe(c[u].h1, 0x85);
    long long tot[2] = {};
    for (int u = 0; u < 2; u++) {
        std::map<int, long long> h;
        for (int v : c[u].counts) tot[u] += v, h[v]++;
        const double r = tot[u] / (c[u].counts.size() / 8000.0);
        printf("unit %d: %zu ticks, %lld samples -> %.4f Hz in USB time, errors %lld;", u + 1, c[u].counts.size(), tot[u], r,
               c[u].errors);
        for (auto &kv : h) printf("  %d x%lld", kv.first, kv.second);
        printf("\n");
    }
    // each controller's USB clock against the PC clock, and the boxes' rates in PC time
    double pc_rate[2] = {};
    for (int u = 0; u < 2; u++) {
        double mx = 0, my = 0, xx = 0, xy = 0, rr = 0;
        const size_t m = c[u].sof.size();
        const double x0 = c[u].sof[0].first, y0 = c[u].sof[0].second;
        for (auto &p : c[u].sof) mx += (p.first - x0) / freq.QuadPart, my += p.second - y0;
        mx /= m, my /= m;
        for (auto &p : c[u].sof) {
            const double x = (p.first - x0) / freq.QuadPart - mx, y = p.second - y0 - my;
            xx += x * x, xy += x * y;
        }
        const double frames_per_s = xy / xx;
        for (auto &p : c[u].sof) {
            const double x = (p.first - x0) / freq.QuadPart - mx, y = p.second - y0 - my;
            rr += (y - frames_per_s * x) * (y - frames_per_s * x);
        }
        const double r = tot[u] / (c[u].counts.size() / 8000.0);
        pc_rate[u] = r * frames_per_s / 1000.0;
        printf("unit %d controller: USB frames %.5f /s (%+.1f ppm vs PC clock), fit residual %.3f ms -> box runs at %.3f Hz in PC time\n",
               u + 1, frames_per_s, (frames_per_s / 1000 - 1) * 1e6, std::sqrt(rr / m), pc_rate[u]);
    }
    printf("boxes in PC time: unit 2 / unit 1 = %+.2f ppm\n", (pc_rate[1] / pc_rate[0] - 1) * 1e6);
    // sample-count difference, tick by tick
    const size_t n = std::min(c[0].counts.size(), c[1].counts.size());
    long long a = 0, b = 0;
    std::vector<double> dif;
    dif.reserve(n);
    for (size_t i = 0; i < n; i++) {
        a += c[0].counts[i];
        b += c[1].counts[i];
        dif.push_back((double)(b - a));
    }
    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (size_t i = 0; i < n; i++) {
        const double x = i / 8000.0;
        sx += x, sy += dif[i], sxx += x * x, sxy += x * dif[i];
    }
    const double slope = (n * sxy - sx * sy) / (n * sxx - sx * sx), icpt = (sy - slope * sx) / n;
    double rms = 0, lo = 1e9, hi = -1e9;
    for (size_t i = 0; i < n; i++) {
        const double e = dif[i] - (icpt + slope * (i / 8000.0));
        rms += e * e;
        lo = std::min(lo, e), hi = std::max(hi, e);
    }
    printf("unit 2 - unit 1: %+.4f samples/s (%+.2f ppm), residual %.3f samples rms, %.2f .. %.2f p-p around the line\n", slope,
           slope / 44100 * 1e6, std::sqrt(rms / n), lo, hi);
    return 0;
}
