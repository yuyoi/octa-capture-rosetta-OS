// Rosetta Dual - one OCTA-CAPTURE through WASAPI exclusive mode (see wasapi_unit.h).
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include "win/wasapi_unit.h"

#include <avrt.h>
#include <functiondiscoverykeys_devpkey.h>
#include <ksmedia.h>

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace rd {

static const CLSID kEnum = __uuidof(MMDeviceEnumerator);

template <class T> static void release(T *&p) {
    if (p) p->Release();
    p = nullptr;
}

double qpc_seconds() {
    static double inv = [] {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        return 1.0 / (double)f.QuadPart;
    }();
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart * inv;
}

// "1-2 (OCTA-CAPTURE)" -> unit 0 pair 0; "9-10 (3- OCTA-CAPTURE)" -> unit 1 pair 4; "MAIN (...)" -> skipped
static bool parse_name(const std::wstring &n, int &unit, int &pair) {
    static const wchar_t *pairs[] = {L"1-2 (", L"3-4 (", L"5-6 (", L"7-8 (", L"9-10 ("};
    if (n.size() < 14 || n.compare(n.size() - 13, 13, L"OCTA-CAPTURE)") != 0) return false;
    for (int p = 0; p < 5; p++) {
        const size_t L = wcslen(pairs[p]);
        if (n.compare(0, L, pairs[p]) == 0) {
            pair = p;
            const wchar_t c = n[L];
            unit = (c >= L'0' && c <= L'9') ? 1 : 0;      // "(3- OCTA-CAPTURE)": Windows' number for the second box
            return true;
        }
    }
    return false;
}

std::vector<Endpoint> find_octa_endpoints() {
    std::vector<Endpoint> out;
    IMMDeviceEnumerator *en = nullptr;
    if (FAILED(CoCreateInstance(kEnum, nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void **)&en))) return out;
    for (int dir = 0; dir < 2; dir++) {
        IMMDeviceCollection *col = nullptr;
        if (FAILED(en->EnumAudioEndpoints(dir == 0 ? eCapture : eRender, DEVICE_STATE_ACTIVE, &col))) continue;
        UINT n = 0;
        col->GetCount(&n);
        for (UINT i = 0; i < n; i++) {
            IMMDevice *d = nullptr;
            IPropertyStore *ps = nullptr;
            LPWSTR id = nullptr;
            PROPVARIANT v;
            PropVariantInit(&v);
            if (SUCCEEDED(col->Item(i, &d)) && SUCCEEDED(d->GetId(&id)) && SUCCEEDED(d->OpenPropertyStore(STGM_READ, &ps)) &&
                SUCCEEDED(ps->GetValue(PKEY_Device_FriendlyName, &v)) && v.vt == VT_LPWSTR) {
                Endpoint e;
                e.name = v.pwszVal;
                e.id = id;
                e.capture = dir == 0;
                if (parse_name(e.name, e.unit, e.pair)) out.push_back(e);
            }
            PropVariantClear(&v);
            if (id) CoTaskMemFree(id);
            release(ps);
            release(d);
        }
        release(col);
    }
    release(en);
    return out;
}

// ---------------- PairStream ----------------
bool PairStream::open(const std::wstring &id, bool capture, double fs, std::string &err) {
    close();
    capture_ = capture;
    fs_ = fs;
    IMMDeviceEnumerator *en = nullptr;
    IMMDevice *dev = nullptr;
    HRESULT hr = CoCreateInstance(kEnum, nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void **)&en);
    if (SUCCEEDED(hr)) hr = en->GetDevice(id.c_str(), &dev);
    release(en);
    if (FAILED(hr)) {
        err = "endpoint not found";
        return false;
    }
    auto activate = [&] { return dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, (void **)&client_); };
    if (FAILED(activate())) {
        release(dev);
        err = "cannot activate the endpoint";
        return false;
    }
    // first format the device takes in exclusive mode
    struct Try { int bits, valid; bool flt; } tries_int[] = {{32, 24, false}, {24, 24, false}, {32, 32, false}, {32, 32, true}, {16, 16, false}},
        tries_flt[] = {{32, 32, true}, {32, 24, false}, {24, 24, false}, {16, 16, false}, {32, 32, false}},
        tries_16[] = {{16, 16, false}, {32, 32, true}, {32, 24, false}, {24, 24, false}, {32, 32, false}};
    const char *fm = getenv("RD_FMT");
    const Try *tries = fm && !strcmp(fm, "float") ? tries_flt : fm && !strcmp(fm, "16") ? tries_16 : tries_int;
    WAVEFORMATEXTENSIBLE wf = {};
    bool found = false;
    for (int ti = 0; ti < 5; ti++) {
        const Try &t = tries[ti];
        wf = {};
        wf.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
        wf.Format.nChannels = 2;
        wf.Format.nSamplesPerSec = (DWORD)fs;
        wf.Format.wBitsPerSample = (WORD)t.bits;
        wf.Format.nBlockAlign = (WORD)(2 * t.bits / 8);
        wf.Format.nAvgBytesPerSec = wf.Format.nSamplesPerSec * wf.Format.nBlockAlign;
        wf.Format.cbSize = sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX);
        wf.Samples.wValidBitsPerSample = (WORD)t.valid;
        wf.dwChannelMask = SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT;
        wf.SubFormat = t.flt ? KSDATAFORMAT_SUBTYPE_IEEE_FLOAT : KSDATAFORMAT_SUBTYPE_PCM;
        if (client_->IsFormatSupported(AUDCLNT_SHAREMODE_EXCLUSIVE, (WAVEFORMATEX *)&wf, nullptr) == S_OK) {
            bits_ = t.bits;
            valid_ = t.valid;
            float_ = t.flt;
            bytes_ = t.bits / 8;
            found = true;
            break;
        }
    }
    if (!found) {
        release(client_);
        release(dev);
        err = "no exclusive-mode format at this sample rate";
        return false;
    }
    REFERENCE_TIME def = 0, mn = 0;
    client_->GetDevicePeriod(&def, &mn);
    REFERENCE_TIME per = getenv("RD_MINPERIOD") ? mn : def;   // default period (~10 ms): robust with many streams
    hr = client_->Initialize(AUDCLNT_SHAREMODE_EXCLUSIVE, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, per, per,
                             (WAVEFORMATEX *)&wf, nullptr);
    if (hr == AUDCLNT_E_BUFFER_SIZE_NOT_ALIGNED) {
        UINT32 n = 0;
        client_->GetBufferSize(&n);
        release(client_);
        activate();
        per = (REFERENCE_TIME)(10000000.0 * n / fs + 0.5);
        hr = client_->Initialize(AUDCLNT_SHAREMODE_EXCLUSIVE, AUDCLNT_STREAMFLAGS_EVENTCALLBACK, per, per,
                                 (WAVEFORMATEX *)&wf, nullptr);
    }
    release(dev);
    if (FAILED(hr)) {
        char b[96];
        snprintf(b, sizeof b, "exclusive open failed (0x%08lx): is another program using the OCTA?", (unsigned long)hr);
        err = b;
        release(client_);
        return false;
    }
    ev_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    client_->SetEventHandle(ev_);
    client_->GetBufferSize(&buf_frames_);
    if (capture_)
        hr = client_->GetService(__uuidof(IAudioCaptureClient), (void **)&cap_);
    else
        hr = client_->GetService(__uuidof(IAudioRenderClient), (void **)&ren_);
    if (FAILED(hr)) {
        err = "GetService failed";
        close();
        return false;
    }
    tmp_.assign((size_t)buf_frames_ * 4 * 2, 0.0f);
    first_t_ = 0;
    return true;
}

void PairStream::close() {
    if (client_) client_->Stop();
    release(cap_);
    release(ren_);
    release(client_);
    if (ev_) CloseHandle(ev_);
    ev_ = nullptr;
}

bool PairStream::start() {
    if (!client_) return false;
    if (!capture_) {                                     // pre-roll one buffer of silence
        BYTE *d = nullptr;
        if (SUCCEEDED(ren_->GetBuffer(buf_frames_, &d))) ren_->ReleaseBuffer(buf_frames_, AUDCLNT_BUFFERFLAGS_SILENT);
    }
    return SUCCEEDED(client_->Start());
}

void PairStream::to_float(const BYTE *s, float *d, UINT32 n) const {
    const UINT32 m = n * 2;
    if (float_) {
        memcpy(d, s, m * sizeof(float));
    } else if (bytes_ == 4) {
        const int32_t *p = (const int32_t *)s;
        for (UINT32 i = 0; i < m; i++) d[i] = (float)(p[i] * (1.0 / 2147483648.0));
    } else if (bytes_ == 3) {
        for (UINT32 i = 0; i < m; i++) {
            const int32_t v = (int32_t)((uint32_t)s[3 * i] << 8 | (uint32_t)s[3 * i + 1] << 16 | (uint32_t)s[3 * i + 2] << 24);
            d[i] = (float)(v * (1.0 / 2147483648.0));
        }
    } else {
        const int16_t *p = (const int16_t *)s;
        for (UINT32 i = 0; i < m; i++) d[i] = p[i] * (1.0f / 32768.0f);
    }
}

void PairStream::from_float(const float *s, BYTE *d, UINT32 n) const {
    const UINT32 m = n * 2;
    auto clip = [](double x) { return x > 0.9999999 ? 0.9999999 : x < -1.0 ? -1.0 : x; };
    if (float_) {
        memcpy(d, s, m * sizeof(float));
    } else if (bytes_ == 4) {
        int32_t *p = (int32_t *)d;
        for (UINT32 i = 0; i < m; i++) p[i] = (int32_t)std::lround(clip(s[i]) * 2147483647.0) & (valid_ == 24 ? ~0xff : ~0);
    } else if (bytes_ == 3) {
        for (UINT32 i = 0; i < m; i++) {
            const int32_t v = (int32_t)std::lround(clip(s[i]) * 8388607.0);
            d[3 * i] = (BYTE)v;
            d[3 * i + 1] = (BYTE)(v >> 8);
            d[3 * i + 2] = (BYTE)(v >> 16);
        }
    } else {
        int16_t *p = (int16_t *)d;
        for (UINT32 i = 0; i < m; i++) p[i] = (int16_t)std::lround(clip(s[i]) * 32767.0);
    }
}

size_t PairStream::drain(Ring &ring) {
    size_t total = 0;
    UINT32 next = 0;
    while (cap_ && SUCCEEDED(cap_->GetNextPacketSize(&next)) && next > 0) {
        BYTE *data = nullptr;
        UINT32 frames = 0;
        DWORD flags = 0;
        UINT64 devpos = 0, qpc = 0;
        if (FAILED(cap_->GetBuffer(&data, &frames, &flags, &devpos, &qpc))) break;
        if (tmp_.size() < (size_t)frames * 2) tmp_.resize((size_t)frames * 2);
        if (flags & AUDCLNT_BUFFERFLAGS_SILENT)
            std::fill(tmp_.begin(), tmp_.begin() + (size_t)frames * 2, 0.0f);
        else
            to_float(data, tmp_.data(), frames);
        if (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) glitches_++;   // we were late: the device dropped data
        if (getenv("RD_TRACE_PKT")) {                   // log glitches and odd timing with the packets around them
            const double tq = qpc * 1e-7, exp_t = next_t_, dt = next_t_ > 0 ? tq - exp_t : 0;
            if (flags || std::fabs(dt) > 0.25 * buf_frames_ / fs_ || trace_n_ > 0) {
                printf("  pkt %p: frames %u flags %lx devpos %llu (expected %llu) qpc-late %+.3f ms\n", (void *)this, frames,
                       flags, (unsigned long long)devpos, (unsigned long long)next_pos_, dt * 1e3);
                trace_n_ = trace_n_ > 0 ? trace_n_ - 1 : 4;
            }
        }
        // Time of the packet's first frame. Roland's driver gives a QPC position, but it is not a real measurement
        // of the unit's clock (on the second unit it runs at exactly the nominal rate; on the first it disagrees
        // with a plain arrival count by ~70 ppm). Arrival time is noisier but honest; the clock fit averages it.
        // RD_DEVICE_QPC=1 switches back to the device's QPC positions for comparison.
        (void)qpc;
        const double t_first = (getenv("RD_DEVICE_QPC") && qpc) ? qpc * 1e-7 : qpc_seconds() - frames / fs_;
        if (first_t_ == 0) {
            first_t_ = t_first;
            next_pos_ = devpos;
        }
        // Keep sound and time on one line (the clock loop depends on it):
        //  - data lost (device position jumped, or the packet is stamped more than half a period late): put the
        //    missing frames in as silence;
        //  - data repeated (after a DATA_DISCONTINUITY the device hands over one stale packet, so the next packet is
        //    stamped a whole period "early"): drop that many frames.
        UINT64 gap = devpos > next_pos_ ? devpos - next_pos_ : 0;
        size_t drop = 0;
        bool early = false;
        if (next_t_ > 0 && gap == 0) {
            const double late = t_first - next_t_;
            // a late packet only means lost audio when the device says so (the second unit's packets are paced by
            // Roland's driver and can arrive unevenly without anything missing)
            if ((flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) && late > 0.5 * buf_frames_ / fs_)
                gap = (UINT64)std::llround(late * fs_);
            early = late < -0.5 * buf_frames_ / fs_;
            // An "early" packet is Roland's driver catching up on the second unit (it paces that unit's packets on
            // its own timer, then hands over an extra one): the audio is continuous, so keep every frame.
            if (getenv("RD_DROP_EARLY") && late < -0.5 * buf_frames_ / fs_) drop = (size_t)std::llround(-late * fs_);
        }
        if (gap > 0 && gap < (UINT64)(fs_ * 2)) {
            gap_frames_ += gap;
            std::vector<float> z((size_t)std::min<UINT64>(gap, 4096) * 2, 0.0f);
            while (gap) {
                const size_t k = (size_t)std::min<UINT64>(gap, 4096);
                ring.write(z.data(), k, t_first - (double)gap / fs_ + (k - 1) / fs_);
                gap -= k;
            }
        }
        next_pos_ = devpos + frames;
        if (drop >= frames) {                            // the whole packet is a repeat: keep the timeline, skip it
            dropped_frames_ += frames;
            cap_->ReleaseBuffer(frames);
            continue;                                    // next_t_ stays: this packet's time is already covered
        }
        if (drop) {
            dropped_frames_ += drop;
            ring.write(tmp_.data() + drop * 2, frames - drop, t_first + (frames - 1) / fs_);
        } else {
            ring.write(tmp_.data(), frames, t_first + (frames - 1) / fs_);
        }
        next_t_ = t_first + frames / fs_;
        if (early && getenv("RD_LOG_EVENTS")) {
            FILE *lf = nullptr;
            char lp[MAX_PATH];
            GetTempPathA(MAX_PATH, lp);
            strcat_s(lp, "RosettaDual_events.log");
            if (fopen_s(&lf, lp, "a") == 0 && lf) {
                fprintf(lf, "%p %.6f %llu %.3f\n", (void *)this, next_t_, (unsigned long long)ring.stamp().frames,
                        (t_first - (next_t_ - frames / fs_)) * 1e3);
                fclose(lf);
            }
        }
        if (early) {                                     // catch-up: remember when, and how many frames we had then
            std::lock_guard<std::mutex> lk(ev_mu_);
            if (events_.size() < 64) events_.push_back({next_t_, ring.stamp().frames});
        }
        cap_->ReleaseBuffer(frames);
        total += frames;
    }
    return total;
}

void PairStream::fill(Ring &ring) {
    BYTE *d = nullptr;
    if (!ren_ || FAILED(ren_->GetBuffer(buf_frames_, &d))) return;
    if (tmp_.size() < (size_t)buf_frames_ * 2) tmp_.resize((size_t)buf_frames_ * 2);
    // start playing only once a lead of one extra buffer is queued, then hold it; count every shortfall
    if (!playing_ && ring.available() >= (size_t)buf_frames_ * 2) playing_ = true;
    const size_t have = playing_ ? std::min(ring.available(), (size_t)buf_frames_) : 0;
    if (playing_ && have < buf_frames_) underflows_++, playing_ = false;
    ring.read(tmp_.data(), have);
    std::fill(tmp_.begin() + have * 2, tmp_.begin() + (size_t)buf_frames_ * 2, 0.0f);
    from_float(tmp_.data(), d, buf_frames_);
    ren_->ReleaseBuffer(buf_frames_, 0);
}

// ---------------- Unit ----------------
bool Unit::open(const std::vector<Endpoint> &eps, int unit, double fs, bool want_out, std::string &err) {
    stop();
    fs_ = fs;
    for (int dir = 0; dir < (want_out ? 2 : 1); dir++) {
        for (int p = 0; p < 5; p++) {
            for (const Endpoint &e : eps) {
                if (e.unit != unit || e.pair != p || e.capture != (dir == 0)) continue;
                PairStream *s = new PairStream;
                if (!s->open(e.id, dir == 0, fs, err)) {
                    delete s;
                    err = "unit " + std::to_string(unit + 1) + " pair " + std::to_string(2 * p + 1) + ": " + err;
                    return false;
                }
                (dir == 0 ? caps_ : rens_).push_back(s);
            }
        }
    }
    if (caps_.empty()) {
        err = "unit " + std::to_string(unit + 1) + ": no inputs found";
        return false;
    }
    pin_ = std::vector<Ring>(caps_.size());
    for (Ring &r : pin_) r.init(2, 1 << 15);
    pout_ = std::vector<Ring>(rens_.size());
    for (Ring &r : pout_) r.init(2, 1 << 15);
    in.init(channels_in(), 1 << 15);
    out.init(rens_.empty() ? 2 : channels_out(), 1 << 15);
    aligned_ = false;
    merged_ = 0;
    return true;
}

bool Unit::start(HANDLE notify) {
    notify_ = notify;
    stop_ = false;
    for (PairStream *s : rens_)
        if (!s->start()) return false;
    for (PairStream *s : caps_)
        if (!s->start()) return false;
    th_ = std::thread([this] { run(); });
    if (!rens_.empty()) th_out_ = std::thread([this] { run_out(); });
    return true;
}

void Unit::stop() {
    stop_ = true;
    if (th_.joinable()) th_.join();
    if (th_out_.joinable()) th_out_.join();
    for (PairStream *s : caps_) delete s;
    for (PairStream *s : rens_) delete s;
    caps_.clear();
    rens_.clear();
}

void Unit::merge() {
    const size_t P = caps_.size();
    if (!aligned_) {                                     // line the pairs up on their first-sample times, once
        double latest = 0;
        for (PairStream *s : caps_) {
            if (!s->started_data()) return;
            latest = std::max(latest, s->first_time());
        }
        for (size_t i = 0; i < P; i++) {
            const size_t skip = (size_t)std::lround((latest - caps_[i]->first_time()) * fs_);
            if (pin_[i].available() < skip) return;      // not enough yet: try again on the next packet
        }
        for (size_t i = 0; i < P; i++) pin_[i].skip((size_t)std::lround((latest - caps_[i]->first_time()) * fs_));
        skip0_ = (uint64_t)std::lround((latest - caps_[0]->first_time()) * fs_);
        aligned_ = true;
    }
    size_t n = SIZE_MAX;
    for (Ring &r : pin_) n = std::min(n, r.available());
    if (n == 0 || n == SIZE_MAX) return;
    mix_.resize(n * 2 * P);
    pair_.resize(n * 2);
    for (size_t i = 0; i < P; i++) {
        pin_[i].read(pair_.data(), n);
        for (size_t f = 0; f < n; f++) {
            mix_[f * 2 * P + 2 * i] = pair_[2 * f];
            mix_[f * 2 * P + 2 * i + 1] = pair_[2 * f + 1];
        }
    }
    const Stamp st = pin_[0].stamp();                    // time of the newest merged frame, from pair 1-2's clock
    const double t = st.t - (double)(st.frames - pin_[0].read_total()) / fs_;
    in.write(mix_.data(), n, t);
    merged_ += n;
    // catch-up events of pair 1-2, in merged-frame numbers
    for (const auto &e : caps_[0]->take_events()) {
        if (e.second < skip0_) continue;
        std::lock_guard<std::mutex> lk(ev_mu_);
        if (events_.size() < 256) events_.push_back({e.first, e.second - skip0_});
    }
}

std::vector<std::pair<double, uint64_t>> PairStream::take_events() {
    std::lock_guard<std::mutex> lk(ev_mu_);
    std::vector<std::pair<double, uint64_t>> v;
    v.swap(events_);
    return v;
}

std::vector<std::pair<double, uint64_t>> Unit::take_events() {
    std::lock_guard<std::mutex> lk(ev_mu_);
    std::vector<std::pair<double, uint64_t>> v;
    v.swap(events_);
    return v;
}

void Unit::run() {                                     // capture thread: drain, merge, wake the engine
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    DWORD idx = 0;
    HANDLE av = AvSetMmThreadCharacteristicsW(L"Pro Audio", &idx);
    std::vector<HANDLE> hs;
    for (PairStream *s : caps_) hs.push_back(s->event());
    while (!stop_) {
        const DWORD w = WaitForMultipleObjects((DWORD)hs.size(), hs.data(), FALSE, 200);
        if (w == WAIT_TIMEOUT) continue;
        const size_t k = w - WAIT_OBJECT_0;
        if (k < caps_.size()) {
            caps_[k]->drain(pin_[k]);
            merge();
            if (notify_) SetEvent(notify_);
        }
    }
    if (av) AvRevertMmThreadCharacteristics(av);
    CoUninitialize();
}

void Unit::run_out() {                                 // render thread: its own thread, so it is never starved
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    DWORD idx = 0;
    HANDLE av = AvSetMmThreadCharacteristicsW(L"Pro Audio", &idx);
    std::vector<HANDLE> hs;
    for (PairStream *s : rens_) hs.push_back(s->event());
    std::vector<float> o, pr;
    const size_t R = rens_.size();
    while (!stop_) {
        const DWORD w = WaitForMultipleObjects((DWORD)hs.size(), hs.data(), FALSE, 200);
        if (w == WAIT_TIMEOUT) continue;
        const size_t r = w - WAIT_OBJECT_0;
        if (r >= R) continue;
        const size_t n = out.available();               // split new 10-channel output onto the pairs
        if (n) {
            o.resize(n * 2 * R);
            pr.resize(n * 2);
            out.read(o.data(), n);
            for (size_t i = 0; i < R; i++) {
                for (size_t f = 0; f < n; f++) {
                    pr[2 * f] = o[f * 2 * R + 2 * i];
                    pr[2 * f + 1] = o[f * 2 * R + 2 * i + 1];
                }
                pout_[i].write(pr.data(), n, 0);
            }
        }
        rens_[r]->fill(pout_[r]);
    }
    if (av) AvRevertMmThreadCharacteristics(av);
    CoUninitialize();
}

std::string Unit::status() const {
    char b[300];
    size_t g = 0;
    size_t u = 0, d = 0, gp = 0;
    for (PairStream *s : caps_) g += s->glitches(), d += (size_t)s->dropped_frames(), gp += (size_t)s->gap_frames();
    for (PairStream *s : rens_) u += s->underflows();
    snprintf(b, sizeof b, "%zu in / %zu out pairs, period %u, in ring %zu, overruns %zu, capture glitches %zu "
             "(gap-filled %zu, dropped %zu frames), output underflows %zu",
             caps_.size(), rens_.size(), (unsigned)period(), in.available(), in.overruns(), g, gp, d, u);
    return b;
}

}  // namespace rd
