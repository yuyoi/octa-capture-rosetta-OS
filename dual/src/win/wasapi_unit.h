// Rosetta Dual - one OCTA-CAPTURE through WASAPI exclusive mode.
// Roland's WDM driver shows each unit as stereo endpoints ("1-2 (OCTA-CAPTURE)" ... "9-10 (...)"; the second unit is
// "1-2 (3- OCTA-CAPTURE)" or similar). A Unit opens all pairs of one box, event-driven. It lines the pairs up once at
// start (they share the unit's clock, so they stay lined up), and merges them into one 10-channel input ring. The
// ring is stamped with the device's own capture timestamps (QPC positions). Output works the other way: a 10-channel
// ring is split onto the pairs.
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#pragma once
#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>

#include <atomic>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "core/ring.h"

namespace rd {

struct Endpoint {
    std::wstring id, name;
    int unit = 0;          // 0 = first box "(OCTA-CAPTURE)", 1 = second "(N- OCTA-CAPTURE)"
    int pair = 0;          // 0..4 = 1-2 .. 9-10
    bool capture = true;
};
std::vector<Endpoint> find_octa_endpoints();

double qpc_seconds();      // monotonic clock used for every stamp

class PairStream {
public:
    ~PairStream() { close(); }
    bool open(const std::wstring &id, bool capture, double fs, std::string &err);
    void close();
    bool start();
    HANDLE event() const { return ev_; }
    // capture: move everything that is ready into ring (stereo). Returns frames moved.
    size_t drain(Ring &ring);
    // render: fill one device buffer from ring (stereo), silence when it runs dry
    void fill(Ring &ring);
    UINT32 period_frames() const { return buf_frames_; }
    double first_time() const { return first_t_; }        // capture: time of the first frame received (s)
    bool started_data() const { return first_t_ > 0; }
    size_t glitches() const { return glitches_; }

private:
    void to_float(const BYTE *src, float *dst, UINT32 n) const;
    void from_float(const float *src, BYTE *dst, UINT32 n) const;
    IAudioClient *client_ = nullptr;
    IAudioCaptureClient *cap_ = nullptr;
    IAudioRenderClient *ren_ = nullptr;
    HANDLE ev_ = nullptr;
    bool capture_ = true;
    UINT32 buf_frames_ = 0;
    int bits_ = 32, valid_ = 24, bytes_ = 4;
    bool float_ = false;
    double fs_ = 48000, first_t_ = 0, qpc_scale_ = 1e-7;
    std::vector<float> tmp_;
    size_t glitches_ = 0;
    UINT64 next_pos_ = 0, gap_frames_ = 0;
    double next_t_ = 0;
    UINT64 dropped_frames_ = 0;
    bool playing_ = false;
    std::mutex ev_mu_;
    std::vector<std::pair<double, uint64_t>> events_;
    size_t underflows_ = 0;
    int trace_n_ = 0;

public:
    UINT64 gap_frames() const { return gap_frames_; }
    UINT64 dropped_frames() const { return dropped_frames_; }
    size_t underflows() const { return underflows_; }
    // catch-up packets (Roland's driver handing over an extra packet early): (arrival time, frames written after)
    std::vector<std::pair<double, uint64_t>> take_events();
};

class Unit {
public:
    ~Unit() { stop(); }
    // open all capture pairs (+ render pairs when want_out), at fs
    bool open(const std::vector<Endpoint> &eps, int unit, double fs, bool want_out, std::string &err);
    bool start(HANDLE notify);         // notify is signalled after new merged input
    void stop();
    Ring in;                           // 10 channels, merged + aligned, stamped
    Ring out;                          // 10 channels to play
    int channels_in() const { return 2 * (int)caps_.size(); }
    int channels_out() const { return 2 * (int)rens_.size(); }
    UINT32 period() const { return caps_.empty() ? 0 : caps_[0]->period_frames(); }
    std::string status() const;
    // catch-up events of this unit in merged-frame numbers (see PairStream::take_events)
    std::vector<std::pair<double, uint64_t>> take_events();

private:
    void run();
    void run_out();
    void merge();
    std::vector<PairStream *> caps_, rens_;
    std::vector<Ring> pin_, pout_;     // per-pair stereo rings
    std::thread th_, th_out_;
    std::atomic<bool> stop_{false};
    HANDLE notify_ = nullptr;
    double fs_ = 48000;
    bool aligned_ = false;
    uint64_t merged_ = 0, skip0_ = 0;
    std::mutex ev_mu_;
    std::vector<std::pair<double, uint64_t>> events_;
    std::vector<float> mix_, pair_;
};

}  // namespace rd
