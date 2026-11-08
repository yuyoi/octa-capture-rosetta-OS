// Rosetta Dual - ASIO driver: two OCTA-CAPTUREs as one device, no coax cable.
// Unit 1 is the clock. Both units' inputs go through a drift Follower (core/drift.h): unit 2 is re-timed onto
// unit 1's clock, and unit 1 gets the same fixed delay so both stay lined up. Outputs go to unit 1.
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
// Built on the Steinberg ASIO SDK 2.3.4 under its GPLv3 option.
#include <windows.h>
#include <objbase.h>
#include <avrt.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "combase.h"
#include "core/drift.h"
#include "iasiodrv.h"
#include "win/wasapi_unit.h"

// {7E4A6B21-6F0C-4D8B-9A51-52D0A2C1E7B9}: Rosetta Dual's own CLSID
static const CLSID CLSID_RosettaDual = {0x7e4a6b21, 0x6f0c, 0x4d8b, {0x9a, 0x51, 0x52, 0xd0, 0xa2, 0xc1, 0xe7, 0xb9}};

static void dlog(const char *fmt, ...) {
    char b[512];
    va_list a;
    va_start(a, fmt);
    vsnprintf(b, sizeof b, fmt, a);
    va_end(a);
    OutputDebugStringA(b);
    char path[MAX_PATH];
    if (GetTempPathA(MAX_PATH, path)) {
        strcat_s(path, "RosettaDual.log");
        FILE *f = nullptr;
        if (fopen_s(&f, path, "a") == 0 && f) {
            fprintf(f, "%s\n", b);
            fclose(f);
        }
    }
}

class RosettaDual : public IASIO, public CUnknown {
public:
    RosettaDual(LPUNKNOWN pUnk, HRESULT *phr) : CUnknown((TCHAR *)"Rosetta Dual", pUnk, phr) {}
    ~RosettaDual() override {
        stop();
        disposeBuffers();
    }
    DECLARE_IUNKNOWN
    static CUnknown *CreateInstance(LPUNKNOWN pUnk, HRESULT *phr) { return new RosettaDual(pUnk, phr); }
    HRESULT STDMETHODCALLTYPE NonDelegatingQueryInterface(REFIID riid, void **ppv) override {
        if (riid == CLSID_RosettaDual) return GetInterface(this, ppv);
        return CUnknown::NonDelegatingQueryInterface(riid, ppv);
    }

    ASIOBool init(void *) override {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        eps_ = rd::find_octa_endpoints();
        units_ = 0;
        for (const rd::Endpoint &e : eps_) units_ = std::max(units_, e.unit + 1);
        bool have1 = false;
        for (const rd::Endpoint &e : eps_) have1 |= e.unit == 0 && e.capture;
        if (!have1) {
            err_ = "No OCTA-CAPTURE found (is VS EXPAND off on both units?)";
            dlog("init: %s", err_.c_str());
            return ASIOFalse;
        }
        fs_ = current_rate();
        dlog("init: %d unit(s), %zu endpoints, %.0f Hz", units_, eps_.size(), fs_);
        return ASIOTrue;
    }
    void getDriverName(char *name) override { strcpy_s(name, 32, "Rosetta Dual"); }
    long getDriverVersion() override { return 1; }
    void getErrorMessage(char *s) override { strcpy_s(s, 124, err_.c_str()); }

    ASIOError getChannels(long *in, long *out) override {
        *in = 10 * units_;
        *out = 10;
        return ASE_OK;
    }
    ASIOError getLatencies(long *in, long *out) override {
        *in = (long)(block_ + delay_);
        *out = (long)(block_ + period1_ * 2);
        return ASE_OK;
    }
    ASIOError getBufferSize(long *mn, long *mx, long *pref, long *gran) override {
        *mn = 64;
        *mx = 2048;
        *pref = 256;
        *gran = -1;                                      // powers of two
        return ASE_OK;
    }
    ASIOError canSampleRate(ASIOSampleRate r) override {
        return (r == 44100 || r == 48000 || r == 88200 || r == 96000) ? ASE_OK : ASE_NoClock;
    }
    ASIOError getSampleRate(ASIOSampleRate *r) override {
        *r = fs_;
        return ASE_OK;
    }
    ASIOError setSampleRate(ASIOSampleRate r) override {
        if (canSampleRate(r) != ASE_OK) return ASE_NoClock;
        if (r == fs_) return ASE_OK;
        const bool was = running_;
        stop();
        fs_ = r;
        if (cb_ && cb_->asioMessage && cb_->asioMessage(kAsioSelectorSupported, kAsioResetRequest, 0, 0))
            cb_->asioMessage(kAsioResetRequest, 0, 0, 0);
        else if (was)
            start();
        return ASE_OK;
    }
    ASIOError getClockSources(ASIOClockSource *c, long *n) override {
        c[0].index = 0;
        c[0].associatedChannel = -1;
        c[0].associatedGroup = -1;
        c[0].isCurrentSource = ASIOTrue;
        strcpy_s(c[0].name, 32, "Unit 1 (master)");
        *n = 1;
        return ASE_OK;
    }
    ASIOError setClockSource(long ref) override { return ref == 0 ? ASE_OK : ASE_InvalidMode; }
    ASIOError getSamplePosition(ASIOSamples *pos, ASIOTimeStamp *ts) override {
        const uint64_t p = sample_pos_.load();
        const double t = stamp_ns_.load();
        pos->hi = (unsigned long)(p >> 32);
        pos->lo = (unsigned long)(p & 0xffffffff);
        const uint64_t tn = (uint64_t)t;
        ts->hi = (unsigned long)(tn >> 32);
        ts->lo = (unsigned long)(tn & 0xffffffff);
        return ASE_OK;
    }
    ASIOError getChannelInfo(ASIOChannelInfo *info) override {
        const bool in = info->isInput == ASIOTrue;
        const long max = in ? 10 * units_ : 10;
        if (info->channel < 0 || info->channel >= max) return ASE_InvalidParameter;
        info->isActive = ASIOFalse;
        for (const Buf &b : bufs_)
            if (b.in == in && b.ch == info->channel) info->isActive = ASIOTrue;
        info->channelGroup = 0;
        info->type = ASIOSTInt32LSB;
        if (in)
            snprintf(info->name, 32, "U%ld In %ld", info->channel / 10 + 1, info->channel % 10 + 1);
        else
            snprintf(info->name, 32, "U1 Out %ld", info->channel + 1);
        return ASE_OK;
    }
    ASIOError createBuffers(ASIOBufferInfo *bi, long n, long size, ASIOCallbacks *cb) override {
        disposeBuffers();
        block_ = size;
        cb_ = cb;
        for (long i = 0; i < n; i++) {
            Buf b;
            b.in = bi[i].isInput == ASIOTrue;
            b.ch = bi[i].channelNum;
            if (b.ch < 0 || b.ch >= (b.in ? 10 * units_ : 10)) {
                disposeBuffers();
                return ASE_InvalidParameter;
            }
            b.mem[0].assign(size, 0);
            b.mem[1].assign(size, 0);
            bi[i].buffers[0] = b.mem[0].data();
            bi[i].buffers[1] = b.mem[1].data();
            bufs_.push_back(std::move(b));
            for (int k = 0; k < 2; k++) bi[i].buffers[k] = bufs_.back().mem[k].data();
        }
        time_info_ = cb->asioMessage && cb->asioMessage(kAsioSupportsTimeInfo, 0, 0, 0) == 1;
        dlog("createBuffers: %ld channels, %ld frames, timeinfo %d", n, size, (int)time_info_);
        return ASE_OK;
    }
    ASIOError disposeBuffers() override {
        stop();
        bufs_.clear();
        cb_ = nullptr;
        return ASE_OK;
    }
    ASIOError controlPanel() override {
        char b[900];
        snprintf(b, sizeof b,
                 "Rosetta Dual - two OCTA-CAPTUREs, no cable\n\n%d unit(s), %.0f Hz, buffer %ld\n"
                 "unit 2 ratio %.7f (%.1f ppm), fill %.1f / %.1f, underruns %zu\n"
                 "unit 1: %s\nunit 2: %s\n\n"
                 "Each unit needs its own USB controller, and VS EXPAND must be OFF on both units.\n"
                 "GPLv3, github.com/yuyoi/octa-capture-rosetta-OS",
                 units_, fs_, block_, f2_.ratio(), (f2_.ratio() - 1) * 1e6, f2_.fill(), f1_.fill(),
                 f1_.underruns() + f2_.underruns(), u1_.status().c_str(), units_ > 1 ? u2_.status().c_str() : "-");
        MessageBoxA(nullptr, b, "Rosetta Dual", MB_OK);
        return ASE_OK;
    }
    ASIOError future(long selector, void *) override {
        switch (selector) {
        case kAsioEnableTimeCodeRead:
        case kAsioDisableTimeCodeRead:
            return ASE_SUCCESS;
        default:
            return ASE_InvalidParameter;
        }
    }
    ASIOError outputReady() override { return ASE_NotPresent; }

    ASIOError start() override {
        if (running_) return ASE_OK;
        if (bufs_.empty() || !cb_) return ASE_NotPresent;
        std::string err;
        if (!u1_.open(eps_, 0, fs_, true, err) || (units_ > 1 && !u2_.open(eps_, 1, fs_, false, err))) {
            err_ = err;
            dlog("start: %s", err.c_str());
            u1_.stop();
            u2_.stop();
            return ASE_HWMalfunction;
        }
        period1_ = u1_.period();
        rd::FollowConfig fc;
        fc.target_fill = 0;                              // grows to packet + block automatically
        f1_.init(u1_.channels_in(), fs_, &u1_.in, fc);
        if (units_ > 1) f2_.init(u2_.channels_in(), fs_, &u2_.in, fc);
        stop_ = false;
        idx_ = 0;
        sample_pos_ = 0;
        ev_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!u1_.start(ev_) || (units_ > 1 && !u2_.start(nullptr))) {
            err_ = "could not start the streams";
            stop();
            return ASE_HWMalfunction;
        }
        engine_ = std::thread([this] { run(); });
        running_ = true;
        dlog("start: %.0f Hz, block %ld, unit 1 period %u", fs_, block_, (unsigned)period1_);
        return ASE_OK;
    }
    ASIOError stop() override {
        if (!running_ && !engine_.joinable()) return ASE_OK;
        stop_ = true;
        if (ev_) SetEvent(ev_);
        if (engine_.joinable()) engine_.join();
        u1_.stop();
        u2_.stop();
        if (ev_) CloseHandle(ev_);
        ev_ = nullptr;
        running_ = false;
        return ASE_OK;
    }

private:
    struct Buf {
        bool in = true;
        long ch = 0;
        std::vector<int32_t> mem[2];
    };

    double current_rate() {
        for (const rd::Endpoint &e : eps_) {
            if (e.unit != 0 || !e.capture) continue;
            IMMDeviceEnumerator *en = nullptr;
            IMMDevice *d = nullptr;
            IAudioClient *c = nullptr;
            WAVEFORMATEX *wf = nullptr;
            double r = 48000;
            if (SUCCEEDED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                           (void **)&en)) &&
                SUCCEEDED(en->GetDevice(e.id.c_str(), &d)) &&
                SUCCEEDED(d->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, (void **)&c)) && SUCCEEDED(c->GetMixFormat(&wf)))
                r = wf->nSamplesPerSec;
            if (wf) CoTaskMemFree(wf);
            if (c) c->Release();
            if (d) d->Release();
            if (en) en->Release();
            return r;
        }
        return 48000;
    }

    // engine: one ASIO block each time unit 1 has delivered enough audio
    void run() {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        DWORD tidx = 0;
        HANDLE av = AvSetMmThreadCharacteristicsW(L"Pro Audio", &tidx);
        const int C1 = u1_.channels_in(), C2 = units_ > 1 ? u2_.channels_in() : 0;
        std::vector<float> a((size_t)block_ * C1), b((size_t)block_ * std::max(C2, 1)), o((size_t)block_ * 10);
        // pace by unit 1's own clock: one block for every block_ frames unit 1 has delivered since we started
        uint64_t base = UINT64_MAX, produced = 0;
        while (!stop_) {
            WaitForSingleObject(ev_, 100);
            const rd::Stamp st = u1_.in.stamp();
            if (base == UINT64_MAX) {
                if (st.frames == 0) continue;
                base = st.frames;
            }
            while (!stop_ && st.frames - base >= produced + (uint64_t)block_) {
                const double now = rd::qpc_seconds();     // jittery by ~1 ms; the follower's clock fit smooths it
                produced += block_;
                // one delay for both units: both start from the larger wish; if one ever re-primes larger (after
                // a dropout), the other jumps to match so the units stay lined up
                const double tgt = std::max(f1_.wanted(), C2 ? f2_.wanted() : 0.0);
                f1_.set_floor(tgt);
                if (C2) {
                    f2_.set_floor(tgt);
                    if (f1_.primed_now() && f2_.primed_now() && std::fabs(f1_.target() - f2_.target()) > 0.5)
                        (f1_.target() < f2_.target() ? f1_ : f2_).reprime();
                }
                f1_.process(a.data(), block_, now);
                if (C2) f2_.process(b.data(), block_, now);
                const long ix = idx_;
                for (Buf &k : bufs_) {
                    if (!k.in) continue;
                    const float *src = k.ch < 10 ? a.data() : b.data();
                    const int C = k.ch < 10 ? C1 : C2, c = k.ch % 10;
                    int32_t *dst = k.mem[ix].data();
                    if (c >= C) {
                        memset(dst, 0, sizeof(int32_t) * block_);
                        continue;
                    }
                    for (long i = 0; i < block_; i++) {
                        double v = src[(size_t)i * C + c];
                        v = v > 0.9999999 ? 0.9999999 : v < -1.0 ? -1.0 : v;
                        dst[i] = (int32_t)std::lround(v * 2147483647.0);
                    }
                }
                sample_pos_ += block_;
                stamp_ns_ = now * 1e9;
                if (time_info_ && cb_->bufferSwitchTimeInfo) {
                    ASIOTime t = {};
                    t.timeInfo.samplePosition.hi = (unsigned long)(sample_pos_.load() >> 32);
                    t.timeInfo.samplePosition.lo = (unsigned long)(sample_pos_.load() & 0xffffffff);
                    const uint64_t ns = (uint64_t)(now * 1e9);
                    t.timeInfo.systemTime.hi = (unsigned long)(ns >> 32);
                    t.timeInfo.systemTime.lo = (unsigned long)(ns & 0xffffffff);
                    t.timeInfo.sampleRate = fs_;
                    t.timeInfo.flags = kSystemTimeValid | kSamplePositionValid | kSampleRateValid;
                    cb_->bufferSwitchTimeInfo(&t, ix, ASIOTrue);
                } else {
                    cb_->bufferSwitch(ix, ASIOTrue);
                }
                // outputs -> unit 1 (10 channels interleaved)
                std::fill(o.begin(), o.end(), 0.0f);
                for (Buf &k : bufs_) {
                    if (k.in) continue;
                    const int32_t *s = k.mem[ix].data();
                    for (long i = 0; i < block_; i++) o[(size_t)i * 10 + k.ch] = s[i] * (1.0f / 2147483648.0f);
                }
                if (u1_.channels_out() == 10) u1_.out.write(o.data(), block_, now);
                idx_ = 1 - ix;
                delay_ = f1_.delay();
                if (++logn_ % (uint64_t)(2 * fs_ / block_) == 0)
                    dlog("t %.0f s: unit 2 ratio %.7f (%+.1f ppm) fill %.1f locked %d | unit 1 ratio %.7f fill %.1f | "
                         "underruns %zu/%zu, delay %.0f frames", produced / fs_, f2_.ratio(), (f2_.ratio() - 1) * 1e6,
                         f2_.fill(), (int)f2_.locked(), f1_.ratio(), f1_.fill(), f1_.underruns(), f2_.underruns(), delay_);
            }
        }
        if (av) AvRevertMmThreadCharacteristics(av);
        CoUninitialize();
    }

    std::vector<rd::Endpoint> eps_;
    int units_ = 0;
    double fs_ = 48000;
    long block_ = 256;
    UINT32 period1_ = 0;
    double delay_ = 0;
    std::string err_;
    std::vector<Buf> bufs_;
    ASIOCallbacks *cb_ = nullptr;
    bool time_info_ = false, running_ = false;
    rd::Unit u1_, u2_;
    rd::Follower f1_, f2_;
    std::thread engine_;
    std::atomic<bool> stop_{false};
    std::atomic<uint64_t> sample_pos_{0};
    std::atomic<double> stamp_ns_{0};
    HANDLE ev_ = nullptr;
    long idx_ = 0;
    uint64_t logn_ = 0;
};

// ---------------- COM / registration (ASIO SDK combase) ----------------
CFactoryTemplate g_Templates[1] = {{L"Rosetta Dual", &CLSID_RosettaDual, RosettaDual::CreateInstance}};
int g_cTemplates = 1;

extern LONG RegisterAsioDriver(CLSID, char *, char *, char *, char *);
extern LONG UnregisterAsioDriver(CLSID, char *, char *);

HRESULT _stdcall DllRegisterServer() {
    LONG rc = RegisterAsioDriver(CLSID_RosettaDual, (char *)"RosettaDual.dll", (char *)"Rosetta Dual",
                                 (char *)"Rosetta Dual (2x OCTA-CAPTURE)", (char *)"Apartment");
    return rc ? E_FAIL : S_OK;
}
HRESULT _stdcall DllUnregisterServer() {
    LONG rc = UnregisterAsioDriver(CLSID_RosettaDual, (char *)"RosettaDual.dll", (char *)"Rosetta Dual");
    return rc ? E_FAIL : S_OK;
}

BOOL WINAPI DllEntryPoint(HINSTANCE, ULONG, LPVOID);
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) { return DllEntryPoint(h, reason, r); }
