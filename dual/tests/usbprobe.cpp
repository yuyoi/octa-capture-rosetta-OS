// Rosetta Dual - first contact with an OCTA-CAPTURE bound to WinUSB (no Roland driver).
//   usbprobe                 list OCTAs on WinUSB, dump descriptors, read the sample rate
//   usbprobe rate 48000      set the sample rate (vendor request 3, as in Linux sound/usb/quirks.c)
//   usbprobe capture [secs]  stream the recording endpoint (iface 1, ep 0x85) and measure the box's exact clock
//                            in USB time: frames per 125 us microframe, counted over the whole run
// Protocol from Linux sound/usb/quirks-table.h (0582:0120): iface 0 = playback ep 0x05, iface 1 = capture ep 0x85,
// alt 1/2/3/4 = 44.1/48/96/192 kHz, S24_3LE, 10 out / 12 in (4/4 at 192k); iface 2 = MIDI.
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <windows.h>
#include <initguid.h>
#include <setupapi.h>
#include <winusb.h>
#include <usb.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#pragma comment(lib, "winusb.lib")
#pragma comment(lib, "setupapi.lib")

struct Dev {
    std::wstring instance, path;
};

// every OCTA-CAPTURE (VID 0582 PID 0120) whose driver is WinUSB, with its WinUSB device path
static std::vector<Dev> find_winusb_octas() {
    std::vector<Dev> out;
    HDEVINFO all = SetupDiGetClassDevsW(nullptr, L"USB", nullptr, DIGCF_ALLCLASSES | DIGCF_PRESENT);
    if (all == INVALID_HANDLE_VALUE) return out;
    SP_DEVINFO_DATA di = {sizeof di};
    for (DWORD i = 0; SetupDiEnumDeviceInfo(all, i, &di); i++) {
        wchar_t hw[512] = {}, svc[64] = {}, inst[256] = {};
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_HARDWAREID, nullptr, (BYTE *)hw, sizeof hw, nullptr);
        if (!wcsstr(hw, L"VID_0582&PID_0120")) continue;
        SetupDiGetDeviceRegistryPropertyW(all, &di, SPDRP_SERVICE, nullptr, (BYTE *)svc, sizeof svc, nullptr);
        SetupDiGetDeviceInstanceIdW(all, &di, inst, 256, nullptr);
        wprintf(L"found %s  driver: %s\n", inst, svc[0] ? svc : L"(none)");
        if (_wcsicmp(svc, L"WinUSB")) continue;
        HKEY k = SetupDiOpenDevRegKey(all, &di, DICS_FLAG_GLOBAL, 0, DIREG_DEV, KEY_READ);
        if (k == INVALID_HANDLE_VALUE) continue;
        wchar_t guids[512] = {};
        DWORD sz = sizeof guids, type = 0;
        if (RegQueryValueExW(k, L"DeviceInterfaceGUIDs", nullptr, &type, (BYTE *)guids, &sz) &&
            RegQueryValueExW(k, L"DeviceInterfaceGUID", nullptr, &type, (BYTE *)guids, &(sz = sizeof guids))) {
            RegCloseKey(k);
            continue;
        }
        RegCloseKey(k);
        GUID g;
        if (CLSIDFromString(guids, &g) != NOERROR) continue;
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
    return out;
}

static void dump_config(const BYTE *c, int n) {
    for (int p = 0; p + 2 <= n && c[p] >= 2; p += c[p]) {
        const BYTE L = c[p], T = c[p + 1];
        if (T == 2)
            printf("config: %d interfaces, total %d bytes, attributes %02x, %d mA\n", c[p + 4], c[p + 2] | c[p + 3] << 8,
                   c[p + 7], c[p + 8] * 2);
        else if (T == 4)
            printf("  interface %d alt %d: %d endpoints, class %02x/%02x/%02x\n", c[p + 2], c[p + 3], c[p + 4], c[p + 5],
                   c[p + 6], c[p + 7]);
        else if (T == 5) {
            const int mps = c[p + 4] | c[p + 5] << 8;
            const char *tt[] = {"control", "iso", "bulk", "interrupt"}, *st[] = {"none", "async", "adaptive", "sync"},
                       *ut[] = {"data", "feedback", "implicit-fb data", "?"};
            printf("    endpoint %02x %s sync=%s usage=%s maxpacket %d x%d interval %d\n", c[p + 2], tt[c[p + 3] & 3],
                   st[(c[p + 3] >> 2) & 3], ut[(c[p + 3] >> 4) & 3], mps & 0x7ff, ((mps >> 11) & 3) + 1, c[p + 6]);
        } else {
            printf("    descriptor type %02x, %d bytes:", T, L);
            for (int i = 2; i < L && i < 18; i++) printf(" %02x", c[p + i]);
            printf("\n");
        }
    }
}

static bool ctrl(WINUSB_INTERFACE_HANDLE h, BYTE type, BYTE req, USHORT val, USHORT idx, BYTE *data, USHORT len,
                 ULONG *got) {
    WINUSB_SETUP_PACKET s = {type, req, val, idx, len};
    return WinUsb_ControlTransfer(h, s, data, len, got, nullptr) != FALSE;
}

static int read_rate(WINUSB_INTERFACE_HANDLE h, int *status) {
    BYTE d[4] = {};
    ULONG got = 0;
    if (!ctrl(h, 0xC0, 3, 1, 0, d, 4, &got) || got != 4) return -1;
    if (status) *status = d[3];
    return d[0] | d[1] << 8 | d[2] << 16;
}

int main(int argc, char **argv) {
    auto devs = find_winusb_octas();
    if (devs.empty()) {
        printf("no OCTA-CAPTURE on WinUSB yet (switch one with Zadig)\n");
        return 1;
    }
    const Dev &dv = devs[0];
    wprintf(L"using %s\n", dv.instance.c_str());
    HANDLE f = CreateFileW(dv.path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, nullptr);
    WINUSB_INTERFACE_HANDLE h0 = nullptr;
    if (f == INVALID_HANDLE_VALUE || !WinUsb_Initialize(f, &h0)) {
        printf("cannot open the device (error %lu)\n", GetLastError());
        return 1;
    }
    USB_DEVICE_DESCRIPTOR dd = {};
    ULONG got = 0;
    WinUsb_GetDescriptor(h0, USB_DEVICE_DESCRIPTOR_TYPE, 0, 0, (BYTE *)&dd, sizeof dd, &got);
    printf("device: USB %x.%02x, VID %04x PID %04x, release %04x, %d config(s)\n", dd.bcdUSB >> 8, dd.bcdUSB & 0xff,
           dd.idVendor, dd.idProduct, dd.bcdDevice, dd.bNumConfigurations);
    BYTE cfg[2048] = {};
    WinUsb_GetDescriptor(h0, USB_CONFIGURATION_DESCRIPTOR_TYPE, 0, 0, cfg, sizeof cfg, &got);
    dump_config(cfg, (int)got);
    int st = 0;
    printf("sample rate: %d Hz (status byte %02x)\n", read_rate(h0, &st), st);

    const std::string cmd = argc > 1 ? argv[1] : "";
    if (cmd == "rate" && argc > 2) {
        const int r = atoi(argv[2]);
        BYTE d[4] = {0x40, (BYTE)r, (BYTE)(r >> 8), (BYTE)(r >> 16)};
        ULONG g2 = 0;
        printf("set %d Hz: %s\n", r, ctrl(h0, 0x40, 3, 8, 0, d, 4, &g2) ? "sent" : "FAILED");
        for (int i = 0; i < 40; i++) {
            Sleep(25);
            const int now = read_rate(h0, &st);
            if (now == r) {
                printf("now %d Hz after %d ms\n", now, (i + 1) * 25);
                break;
            }
        }
    }
    if (cmd == "capture") {
        const double secs = argc > 2 ? atof(argv[2]) : 10;
        const int rate = read_rate(h0, nullptr);
        const int alt = rate == 48000 ? 2 : rate == 96000 ? 3 : rate == 192000 ? 4 : 1;
        const int ch = rate == 192000 ? 4 : 12, bpf = ch * 3;
        WINUSB_INTERFACE_HANDLE h1 = nullptr;
        if (!WinUsb_GetAssociatedInterface(h0, 0, &h1)) {
            printf("no interface 1 (error %lu)\n", GetLastError());
            return 1;
        }
        if (!WinUsb_SetCurrentAlternateSetting(h1, (UCHAR)alt)) {
            printf("cannot select alt %d (error %lu)\n", alt, GetLastError());
            return 1;
        }
        WINUSB_PIPE_INFORMATION_EX pi = {};
        UCHAR pipe = 0;
        for (UCHAR k = 0; WinUsb_QueryPipeEx(h1, (UCHAR)alt, k, &pi); k++)
            if (pi.PipeId == 0x85) pipe = pi.PipeId;
        if (!pipe) {
            printf("no endpoint 0x85 in alt %d\n", alt);
            return 1;
        }
        for (UCHAR k = 0; WinUsb_QueryPipeEx(h1, (UCHAR)alt, k, &pi); k++)
            if (pi.PipeId == 0x85) break;
        printf("capture: alt %d, %d ch, pipe %02x, max %lu bytes per interval, interval %d\n", alt, ch, pi.PipeId,
               pi.MaximumBytesPerInterval, pi.Interval);
        const ULONG per = pi.MaximumBytesPerInterval;
        const int PK = 64;                               // microframes per transfer (8 ms)
        const int NX = 8;                                // transfers in flight
        std::vector<BYTE> buf((size_t)per * PK * NX);
        WINUSB_ISOCH_BUFFER_HANDLE bh = nullptr;
        if (!WinUsb_RegisterIsochBuffer(h1, pipe, buf.data(), (ULONG)buf.size(), &bh)) {
            printf("RegisterIsochBuffer failed (%lu)\n", GetLastError());
            return 1;
        }
        std::vector<OVERLAPPED> ov(NX);
        std::vector<std::vector<USBD_ISO_PACKET_DESCRIPTOR>> pd(NX, std::vector<USBD_ISO_PACKET_DESCRIPTOR>(PK));
        for (int x = 0; x < NX; x++) {
            ov[x].hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            if (!WinUsb_ReadIsochPipeAsap(bh, (ULONG)(x * per * PK), per * PK, x > 0, PK, pd[x].data(), &ov[x]) &&
                GetLastError() != ERROR_IO_PENDING) {
                printf("ReadIsochPipeAsap failed (%lu)\n", GetLastError());
                return 1;
            }
        }
        std::map<int, long long> hist;
        long long frames = 0, packets = 0, errors = 0;
        double peak = 0;
        const long long need = (long long)(secs * 8000);
        int x = 0;
        while (packets < need) {
            DWORD n = 0;
            if (!WinUsb_GetOverlappedResult(h1, &ov[x], &n, TRUE)) errors++;
            for (int k = 0; k < PK; k++) {
                const ULONG len = pd[x][k].Length;
                if (pd[x][k].Status) errors++;
                const int fr = (int)(len / bpf);
                hist[fr]++;
                frames += fr;
                packets++;
                const BYTE *s = buf.data() + x * per * PK + pd[x][k].Offset;
                for (int i = 0; i < fr * ch; i++) {
                    const int v = (int)((unsigned)s[3 * i] << 8 | (unsigned)s[3 * i + 1] << 16 | (unsigned)s[3 * i + 2] << 24);
                    const double a = std::abs(v / 2147483648.0);
                    if (a > peak) peak = a;
                }
            }
            ResetEvent(ov[x].hEvent);
            if (!WinUsb_ReadIsochPipeAsap(bh, (ULONG)(x * per * PK), per * PK, TRUE, PK, pd[x].data(), &ov[x]) &&
                GetLastError() != ERROR_IO_PENDING)
                errors++;
            x = (x + 1) % NX;
        }
        WinUsb_AbortPipe(h1, pipe);
        const double usb_s = packets / 8000.0;
        printf("%lld microframes (%.3f s of USB time), %lld frames -> %.4f Hz in USB time (%+.1f ppm vs nominal), "
               "errors %lld, peak %.1f dBFS\nframes per microframe:",
               packets, usb_s, frames, frames / usb_s, (frames / usb_s / rate - 1) * 1e6, errors,
               peak > 0 ? 20 * log10(peak) : -999.0);
        for (auto &kv : hist) printf("  %d x%lld", kv.first, kv.second);
        printf("\n");
        WinUsb_UnregisterIsochBuffer(bh);
        WinUsb_SetCurrentAlternateSetting(h1, 0);
        WinUsb_Free(h1);
    }
    WinUsb_Free(h0);
    CloseHandle(f);
    return 0;
}
