// Send to stream: the inputs ticked in the app, after their strips, mixed to mono and played into one Windows playback
// device. By default that is VB-Cable's "CABLE Input", whose other end Windows offers as a microphone, so Discord, OBS or
// anything else hears exactly those inputs. Nothing of any output, the monitor mix or PC sound goes in.
//
// Each box's stream thread adds its ticked inputs into that box's ring (Feed::capture). One render thread (WASAPI shared
// mode, event driven) reads both rings through the same Kaiser sinc as the monitor path, at box rate / device rate, and
// nudges that ratio by each ring's level, because the boxes' and the device's clocks never quite agree.
// The boxes' own monitoring is untouched: this only reads what USB already carries.
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#pragma once
#include <audioclient.h>
#include <propkeydef.h>
#include <functiondiscoverykeys_devpkey.h>
#include <mmdeviceapi.h>

#include <atomic>
#include <cmath>
#include <string>
#include <thread>

#include "core/drift.h"

#pragma comment(lib, "ole32.lib")

struct FeedRing {
    static const unsigned N = 1 << 15;
    std::atomic<unsigned> w{0}, r{0};
    float d[N];
    unsigned level() const { return w.load(std::memory_order_acquire) - r.load(std::memory_order_acquire); }
    void push(float v) {
        const unsigned p = w.load(std::memory_order_relaxed);
        d[p & (N - 1)] = v;
        w.store(p + 1, std::memory_order_release);
    }
    float pop() {
        const unsigned p = r.load(std::memory_order_relaxed);
        const float v = d[p & (N - 1)];
        r.store(p + 1, std::memory_order_release);
        return v;
    }
    void skip(unsigned n) { r.store(r.load(std::memory_order_relaxed) + n, std::memory_order_release); }
    void flush() { r.store(w.load(std::memory_order_acquire), std::memory_order_release); }
};

struct Feed {
    static const int BOXES = 2, CH = 10;
    std::atomic<float> gain[BOXES][CH];
    std::atomic<bool> any[BOXES];
    FeedRing ring[BOXES];
    std::atomic<bool> stop{true};
    std::atomic<unsigned> und{0}, fix{0}, written{0};
    std::atomic<float> peak{0};
    std::thread th;
    int box_rate = 44100;
    std::wstring want;

    Feed() {
        for (int b = 0; b < BOXES; b++) {
            any[b] = false;
            for (int c = 0; c < CH; c++) gain[b][c] = 0.0f;
        }
    }

    // stream thread of box b, once per USB microframe: m = first frame, fr frames, bpf bytes per frame, ch channels
    void capture(int b, const unsigned char *m, int fr, int bpf, int ch) {
        if (b < 0 || b >= BOXES || !any[b].load(std::memory_order_relaxed) || stop.load(std::memory_order_relaxed)) return;
        float g[CH];
        const int n = ch < CH ? ch : CH;
        for (int c = 0; c < n; c++) g[c] = gain[b][c].load(std::memory_order_relaxed);
        for (int i = 0; i < fr; i++, m += bpf) {
            float acc = 0;
            for (int c = 0; c < n; c++)
                if (g[c] != 0.0f) {
                    const int v = (int)((unsigned)m[c * 3] << 8 | (unsigned)m[c * 3 + 1] << 16 | (unsigned)m[c * 3 + 2] << 24);
                    acc += g[c] * (float)(v / 2147483648.0);
                }
            ring[b].push(acc);
        }
    }

    void set_gains(int b, const float *g) {
        if (b < 0 || b >= BOXES) return;
        bool on = false;
        for (int c = 0; c < CH; c++) gain[b][c] = g[c], on |= g[c] != 0.0f;
        if (on && !any[b]) ring[b].flush();
        any[b] = on;
    }

    template <class Say> void start(const std::wstring &device, int rate, Say say) {
        end();
        want = device;
        box_rate = rate;
        stop = false;
        th = std::thread([this, say] { run(say); });
    }

    void end() {
        stop = true;
        if (th.joinable()) th.join();
    }

    template <class Say> void run(Say say) {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        IMMDeviceEnumerator *en = nullptr;
        IMMDeviceCollection *all = nullptr;
        IMMDevice *dev = nullptr;
        IAudioClient *ac = nullptr;
        IAudioRenderClient *rc = nullptr;
        WAVEFORMATEX *wf = nullptr;
        HANDLE ev = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        std::string name;
        auto fail = [&](const char *what, HRESULT hr) { say("FEED error %s %08lx\n", what, (unsigned long)hr); };
        do {
            HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void **)&en);
            if (FAILED(hr)) { fail("enumerator", hr); break; }
            if (FAILED(hr = en->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &all))) { fail("endpoints", hr); break; }
            UINT n = 0;
            all->GetCount(&n);
            std::wstring lw = want;
            for (auto &ch : lw) ch = (wchar_t)towlower(ch);
            for (UINT i = 0; i < n && !dev; i++) {          // first playback device whose name contains the wanted text
                IMMDevice *d = nullptr;
                IPropertyStore *ps = nullptr;
                PROPVARIANT pv;
                PropVariantInit(&pv);
                if (SUCCEEDED(all->Item(i, &d)) && SUCCEEDED(d->OpenPropertyStore(STGM_READ, &ps)) &&
                    SUCCEEDED(ps->GetValue(PKEY_Device_FriendlyName, &pv)) && pv.vt == VT_LPWSTR) {
                    std::wstring fn = pv.pwszVal;
                    std::wstring lf = fn;
                    for (auto &ch : lf) ch = (wchar_t)towlower(ch);
                    if (lf.find(lw) != std::wstring::npos) {
                        dev = d, d = nullptr;
                        for (wchar_t ch : fn) name += (ch > 31 && ch < 127) ? (char)ch : '?';
                    }
                }
                PropVariantClear(&pv);
                if (ps) ps->Release();
                if (d) d->Release();
            }
            if (!dev) { say("FEED nodevice\n"); break; }
            if (FAILED(hr = dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, (void **)&ac))) { fail("activate", hr); break; }
            if (FAILED(hr = ac->GetMixFormat(&wf))) { fail("format", hr); break; }
            bool is_float = wf->wFormatTag == WAVE_FORMAT_IEEE_FLOAT;
            if (wf->wFormatTag == WAVE_FORMAT_EXTENSIBLE)
                is_float = ((WAVEFORMATEXTENSIBLE *)wf)->SubFormat == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
            const int bits = wf->wBitsPerSample, chans = wf->nChannels, dev_rate = (int)wf->nSamplesPerSec;
            if (!(is_float && bits == 32) && !(!is_float && (bits == 16 || bits == 24 || bits == 32))) { fail("sample type", 0); break; }
            if (FAILED(hr = ac->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, 400000, 0, wf, nullptr))) {
                fail("init", hr);
                break;
            }
            ac->SetEventHandle(ev);
            UINT32 cap = 0;
            ac->GetBufferSize(&cap);
            if (FAILED(hr = ac->GetService(__uuidof(IAudioRenderClient), (void **)&rc))) { fail("render", hr); break; }
            say("FEED on %d %d %s\n", dev_rate, chans, name.c_str());
            static const rd::SincTable table;
            const int T = rd::SincTable::TAPS;
            const double base = (double)box_rate / dev_rate;
            const double target = box_rate * 0.030;                 // 30 ms in each ring: plenty for USB's 2 ms bursts
            float hist[BOXES][32] = {};
            double frac[BOXES] = {}, avg[BOXES] = {}, ratio[BOXES] = {base, base}, integ[BOXES] = {};
            const UINT32 keep = (UINT32)(dev_rate / 50);             // keep ~20 ms queued in the device, no more
            bool primed[BOXES] = {};
            ac->Start();
            unsigned long long tick = 0;
            while (!stop) {
                WaitForSingleObject(ev, 200);
                UINT32 pad = 0;
                if (FAILED(hr = ac->GetCurrentPadding(&pad))) { fail("lost device", hr); break; }
                if (pad >= keep) continue;
                const UINT32 nf = std::min<UINT32>(cap - pad, keep - pad);
                BYTE *buf = nullptr;
                if (FAILED(hr = rc->GetBuffer(nf, &buf))) { fail("buffer", hr); break; }
                unsigned lvl[BOXES];
                for (int b = 0; b < BOXES; b++) {
                    lvl[b] = ring[b].level();
                    if (!any[b]) { primed[b] = false; continue; }
                    if (!primed[b] && lvl[b] >= target) {    // start (or restart) with exactly the target in the ring
                        ring[b].skip(lvl[b] - (unsigned)target);
                        lvl[b] = (unsigned)target;
                        primed[b] = true, avg[b] = target, frac[b] = 0;
                    }
                    if (lvl[b] > 4 * target) {
                        say("FEED fix box %d level %u at callback %llu\n", b, lvl[b], tick);
                        ring[b].skip(lvl[b] - (unsigned)target), lvl[b] = (unsigned)target, avg[b] = target, fix++;
                    }
                    avg[b] += (lvl[b] - avg[b]) * 0.02;
                    if (primed[b]) integ[b] = std::max(-1.5e-3, std::min(1.5e-3, integ[b] + (avg[b] - target) * 2e-9));
                    ratio[b] = base * (1.0 + std::max(-2e-3, std::min(2e-3, (avg[b] - target) * 2e-6 + integ[b])));
                }
                float pk = peak.load();
                for (UINT32 i = 0; i < nf; i++) {
                    double y = 0;
                    for (int b = 0; b < BOXES; b++) {
                        if (!primed[b]) continue;
                        frac[b] += ratio[b];
                        while (frac[b] >= 1.0) {
                            if (!lvl[b]) {
                                primed[b] = false, und++;
                                say("FEED underrun box %d at callback %llu, frame %u of %u\n", b, tick, i, nf);
                                break;
                            }
                            memmove(hist[b], hist[b] + 1, (T - 1) * sizeof(float));
                            hist[b][T - 1] = ring[b].pop();
                            lvl[b]--;
                            frac[b] -= 1.0;
                        }
                        if (!primed[b]) continue;
                        const double ph = frac[b] * rd::SincTable::PHASES;
                        const int p0 = (int)ph;
                        const float a = (float)(ph - p0);
                        const float *c0 = table.phase(p0), *c1 = table.phase(p0 + 1);
                        float s0 = 0, s1 = 0;
                        for (int k = 0; k < T; k++) s0 += c0[k] * hist[b][k], s1 += c1[k] * hist[b][k];
                        y += s0 + a * (s1 - s0);
                    }
                    y = y > 0.9999999 ? 0.9999999 : y < -1.0 ? -1.0 : y;
                    if (std::fabs(y) > pk) pk = (float)std::fabs(y);
                    for (int c = 0; c < chans; c++) {
                        BYTE *p = buf + ((size_t)i * chans + c) * (bits / 8);
                        if (is_float) {
                            const float f = (float)y;
                            memcpy(p, &f, 4);
                        } else if (bits == 16) {
                            const short s = (short)std::lround(y * 32767.0);
                            memcpy(p, &s, 2);
                        } else if (bits == 24) {
                            const int s = (int)std::lround(y * 8388607.0);
                            p[0] = (BYTE)s, p[1] = (BYTE)(s >> 8), p[2] = (BYTE)(s >> 16);
                        } else {
                            const int s = (int)std::lround(y * 2147483647.0);
                            memcpy(p, &s, 4);
                        }
                    }
                }
                peak = pk;
                rc->ReleaseBuffer(nf, 0);
                written += nf;
                if (++tick % 500 == 0) {                        // about every 5 s: levels, underruns, fixes, peak dBFS
                    say("F %u %u %u %u %.1f\n", (unsigned)avg[0], (unsigned)avg[1], und.load(), fix.load(), pk > 1e-7 ? 20 * std::log10(pk) : -140.0);
                    peak = 0;
                }
            }
            ac->Stop();
        } while (false);
        if (rc) rc->Release();
        if (ac) ac->Release();
        if (wf) CoTaskMemFree(wf);
        if (dev) dev->Release();
        if (all) all->Release();
        if (en) en->Release();
        CloseHandle(ev);
        CoUninitialize();
        if (!stop) say("FEED off\n");
        stop = true;
    }
};
