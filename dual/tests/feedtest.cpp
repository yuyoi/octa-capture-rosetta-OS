// feed.h without boxes: a fake "box" pushes a 1 kHz tone at 44.1 kHz (+100 ppm, like a real box's clock) in 2 ms bursts,
// Feed plays it into a playback device, and a loopback capture of that same device checks what came out: frequency, level,
// and gaps or clicks. usage: feedtest "<part of a playback device name>" [seconds]
#include <windows.h>
#include <initguid.h>

#include <cmath>
#include <cstdio>
#include <vector>

#include "feed.h"

static void say(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    vprintf(fmt, a);
    va_end(a);
    fflush(stdout);
}

int wmain(int argc, wchar_t **argv) {
    if (argc < 2) return 2;
    const double secs = argc > 2 ? _wtof(argv[2]) : 20.0;
    static Feed feed;
    float g[Feed::CH] = {1.0f};
    feed.set_gains(0, g);
    feed.start(argv[1], 44100, [](const char *fmt, auto... a) { say(fmt, a...); });

    std::atomic<bool> done{false};
    std::thread box([&] {                                   // the fake box: 88.2 frames per 2 ms, slightly fast
        LARGE_INTEGER f, t0, t;
        QueryPerformanceFrequency(&f);
        QueryPerformanceCounter(&t0);
        const double rate = 44100.0 * (1 + 100e-6);
        double sent = 0, ph = 0;
        std::vector<unsigned char> frame(36);
        while (!done) {
            Sleep(2);
            QueryPerformanceCounter(&t);
            const double due = (double)(t.QuadPart - t0.QuadPart) / f.QuadPart * rate;
            while (sent < due) {
                const int v = (int)(std::sin(ph) * 0.25 * 2147483647.0);       // -12 dBFS
                ph += 2 * 3.14159265358979 * 1000.0 / 44100.0;
                if (ph > 6.283185307179586) ph -= 6.283185307179586;
                frame[0] = (unsigned char)(v >> 8), frame[1] = (unsigned char)(v >> 16), frame[2] = (unsigned char)(v >> 24);
                feed.capture(0, frame.data(), 1, 36, 12);
                sent += 1;
            }
        }
    });

    // loopback capture of the same device
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    Sleep(800);
    IMMDeviceEnumerator *en = nullptr;
    IMMDeviceCollection *all = nullptr;
    IMMDevice *dev = nullptr;
    CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void **)&en);
    en->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &all);
    UINT n = 0;
    all->GetCount(&n);
    std::wstring want = argv[1];
    for (auto &c : want) c = (wchar_t)towlower(c);
    for (UINT i = 0; i < n && !dev; i++) {
        IMMDevice *d = nullptr;
        IPropertyStore *ps = nullptr;
        PROPVARIANT pv;
        PropVariantInit(&pv);
        all->Item(i, &d);
        d->OpenPropertyStore(STGM_READ, &ps);
        ps->GetValue(PKEY_Device_FriendlyName, &pv);
        std::wstring fn = pv.vt == VT_LPWSTR ? pv.pwszVal : L"";
        for (auto &c : fn) c = (wchar_t)towlower(c);
        if (fn.find(want) != std::wstring::npos) dev = d, d = nullptr;
        PropVariantClear(&pv);
        ps->Release();
        if (d) d->Release();
    }
    if (!dev) { say("no such device\n"); done = true; box.join(); feed.end(); return 1; }
    IAudioClient *ac = nullptr;
    dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, (void **)&ac);
    WAVEFORMATEX *wf = nullptr;
    ac->GetMixFormat(&wf);
    ac->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 2000000, 0, wf, nullptr);
    IAudioCaptureClient *cc = nullptr;
    ac->GetService(__uuidof(IAudioCaptureClient), (void **)&cc);
    const int chans = wf->nChannels, rate = (int)wf->nSamplesPerSec;
    std::vector<float> got;
    ac->Start();
    const DWORD end = GetTickCount() + (DWORD)(secs * 1000);
    unsigned gaps = 0;
    while (GetTickCount() < end) {
        Sleep(10);
        UINT32 pk = 0;
        while (SUCCEEDED(cc->GetNextPacketSize(&pk)) && pk) {
            BYTE *d = nullptr;
            UINT32 fr = 0;
            DWORD fl = 0;
            cc->GetBuffer(&d, &fr, &fl, nullptr, nullptr);
            if (fl & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) gaps++;
            for (UINT32 i = 0; i < fr; i++) got.push_back((fl & AUDCLNT_BUFFERFLAGS_SILENT) ? 0.f : ((float *)d)[i * chans]);
            cc->ReleaseBuffer(fr);
        }
    }
    ac->Stop();
    done = true;
    box.join();
    feed.end();
    // skip the first second (priming), then: level, frequency from zero crossings, and the worst sample-to-sample jump
    const size_t s0 = (size_t)rate;
    if (got.size() < s0 * 3) { say("too little captured (%zu)\n", got.size()); return 1; }
    double sq = 0;
    size_t zc = 0, zfirst = 0, zlast = 0;
    float worst = 0;
    for (size_t i = s0 + 1; i < got.size(); i++) {
        sq += got[i] * got[i];
        if (got[i - 1] < 0 && got[i] >= 0) {
            if (!zc) zfirst = i;
            zlast = i, zc++;
        }
        worst = std::max(worst, std::fabs(got[i] - got[i - 1]));
    }
    const double rms = std::sqrt(sq / (got.size() - s0 - 1));
    const double hz = zc > 1 ? (zc - 1) * (double)rate / (zlast - zfirst) : 0;
    // a clean 1 kHz sine at 0.25 peak moves at most 0.25 * 2 pi * 1000 / rate per sample
    const double max_step = 0.25 * 2 * 3.14159265358979 * 1000.0 / rate;
    say("RESULT device rate %d, captured %.1f s, level %.2f dBFS (want -15.05), frequency %.3f Hz (want 1000.1), "
        "biggest step %.4f (clean <= %.4f), discontinuity flags %u, underruns %u, level fixes %u\n",
        rate, got.size() / (double)rate, 20 * std::log10(rms + 1e-12), hz, worst, max_step, gaps, feed.und.load(), feed.fix.load());
    return 0;
}
