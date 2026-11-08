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
    struct Try { int bits, valid; bool flt; } tries[] = {{32, 24, false}, {24, 24, false}, {32, 32, false}, {32, 32, true}, {16, 16, false}};
    WAVEFORMATEXTENSIBLE wf = {};
    bool found = false;
    for (const Try &t : tries) {
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
    REFERENCE_TIME per = mn > 0 ? mn : def;
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
        const double t_first = qpc ? qpc * 1e-7 : qpc_seconds() - frames / fs_;   // qpc: 100 ns units
        if (first_t_ == 0) first_t_ = t_first;
        ring.write(tmp_.data(), frames, t_first + (frames - 1) / fs_);
        cap_->ReleaseBuffer(frames);
        total += frames;
    }
    return total;
}

void PairStream::fill(Ring &ring) {
    BYTE *d = nullptr;
    if (!ren_ || FAILED(ren_->GetBuffer(buf_frames_, &d))) return;
    if (tmp_.size() < (size_t)buf_frames_ * 2) tmp_.resize((size_t)buf_frames_ * 2);
    const size_t have = std::min(ring.available(), (size_t)buf_frames_);
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
    return true;
}

void Unit::stop() {
    stop_ = true;
    if (th_.joinable()) th_.join();
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
}

void Unit::run() {
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    DWORD idx = 0;
    HANDLE av = AvSetMmThreadCharacteristicsW(L"Pro Audio", &idx);
    std::vector<HANDLE> hs;
    for (PairStream *s : caps_) hs.push_back(s->event());
    for (PairStream *s : rens_) hs.push_back(s->event());
    std::vector<float> o, pr;
    while (!stop_) {
        const DWORD w = WaitForMultipleObjects((DWORD)hs.size(), hs.data(), FALSE, 200);
        if (w == WAIT_TIMEOUT) continue;
        const size_t k = w - WAIT_OBJECT_0;
        if (k < caps_.size()) {
            caps_[k]->drain(pin_[k]);
            merge();
            if (notify_) SetEvent(notify_);
        } else if (k - caps_.size() < rens_.size()) {
            // split newly played-in 10-channel output onto the pairs, then serve this pair's buffer
            const size_t n = out.available();
            if (n) {
                const size_t R = rens_.size();
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
            const size_t r = k - caps_.size();
            rens_[r]->fill(pout_[r]);
        }
    }
    if (av) AvRevertMmThreadCharacteristics(av);
    CoUninitialize();
}

std::string Unit::status() const {
    char b[160];
    snprintf(b, sizeof b, "%zu in pairs, %zu out pairs, period %u frames, in ring %zu frames, overruns %zu",
             caps_.size(), rens_.size(), (unsigned)period(), in.available(), in.overruns());
    return b;
}

}  // namespace rd
