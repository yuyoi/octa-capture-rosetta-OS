// Rosetta Dual - portable drift-corrected resampler. There is no OS code here; the Windows, Linux and macOS
// front ends share it.
//
// A Follower reads a multichannel source on its own clock (unit 2) and produces output on the master clock (unit 1).
// It is a delay-locked loop in the style of JACK/PipeWire:
//   - Both clocks are tracked by a smoothed linear fit of (time, frames). The master's points come from the block
//     calls; the source's come from the ring's write stamps. The base ratio = source rate / master rate.
//   - From the source fit, the follower knows where the source "is" at any instant, not just at packet arrivals.
//     So the buffer fill is measured without the packet sawtooth, and a gentle proportional correction holds it on
//     target. That gives a constant delay and no pitch wobble.
// Interpolation: Kaiser-windowed sinc, 32 taps, 512 phases, blended linearly between phases. Group delay is TAPS/2.
//
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <vector>

#include "ring.h"

namespace rd {

class SincTable {
public:
    static constexpr int TAPS = 32, PHASES = 512;
    explicit SincTable(double cutoff = 0.92, double beta = 9.0) : t_((size_t)(PHASES + 1) * TAPS) {
        const double PI = 3.14159265358979323846;
        auto bessel0 = [](double x) {
            double s = 1, t = 1;
            for (int k = 1; k < 40; k++) {
                t *= (x / (2 * k)) * (x / (2 * k));
                s += t;
            }
            return s;
        };
        const double ib = 1.0 / bessel0(beta);
        for (int p = 0; p <= PHASES; p++) {
            const double frac = (double)p / PHASES;              // wanted point: frac past tap TAPS/2-1
            double sum = 0;
            for (int k = 0; k < TAPS; k++) {
                const double x = (k - (TAPS / 2 - 1)) - frac;
                const double s = std::fabs(x) < 1e-12 ? cutoff : std::sin(PI * cutoff * x) / (PI * x);
                const double wp = x / (TAPS / 2.0);
                const double w = std::fabs(wp) >= 1 ? 0 : bessel0(beta * std::sqrt(1 - wp * wp)) * ib;
                t_[(size_t)p * TAPS + k] = (float)(s * w);
                sum += s * w;
            }
            for (int k = 0; k < TAPS; k++) t_[(size_t)p * TAPS + k] = (float)(t_[(size_t)p * TAPS + k] / sum);
        }
    }
    const float *phase(int p) const { return &t_[(size_t)p * TAPS]; }

private:
    std::vector<float> t_;
};

// Exponentially weighted least-squares line frames = a + rate * t (forgets with time constant tau seconds).
class RateFit {
public:
    explicit RateFit(double tau = 20.0) : tau_(tau) {}
    void reset() { n_ = 0; }
    void add(double t, double f) {
        if (n_ == 0) {
            t0_ = t; f0_ = f; tl_ = t;
            sw_ = sx_ = sy_ = sxx_ = sxy_ = 0;
        }
        const double w = std::exp(-(t - tl_) / tau_);
        tl_ = t;
        const double x = t - t0_, y = f - f0_;
        sw_ = sw_ * w + 1; sx_ = sx_ * w + x; sy_ = sy_ * w + y; sxx_ = sxx_ * w + x * x; sxy_ = sxy_ * w + x * y;
        n_++;
        span_ = t - t0_;
    }
    bool ok() const { return n_ > 50 && span_ > 5.0; }
    double rate() const {
        const double d = sw_ * sxx_ - sx_ * sx_;
        return (sw_ * sxy_ - sx_ * sy_) / d;
    }
    double at(double t) const {                          // fitted frame count at time t
        const double r = rate();
        const double a = (sy_ - r * sx_) / sw_;
        return f0_ + a + r * (t - t0_);
    }
    double time_of(double f) const {                     // fitted time of frame f (inverse of at)
        const double r = rate();
        const double a = (sy_ - r * sx_) / sw_;
        return t0_ + (f - f0_ - a) / r;
    }

private:
    double tau_, t0_ = 0, f0_ = 0, tl_ = 0, span_ = 0;
    double sw_ = 0, sx_ = 0, sy_ = 0, sxx_ = 0, sxy_ = 0;
    uint64_t n_ = 0;
};

struct FollowConfig {
    double target_fill = 512;    // frames buffered ahead of the read point (this path's extra latency)
    double tau_fit = 60.0;       // seconds of clock history in the rate fits
    double tau_hold = 8.0;       // seconds: how fast a fill error is pulled back
    double max_ppm = 300;        // sanity clamp around the measured ratio
    double tau_glide = 2.0;      // seconds: smoothing of the read speed itself
};

class Follower {
public:
    void init(int channels, double fs, Ring *src, FollowConfig cfg = {}) {
        ch_ = channels;
        fs_ = fs;
        src_ = src;
        cfg_ = cfg;
        cap_ = (size_t)SincTable::TAPS + 4096;
        hist_.assign(cap_ * ch_, 0.0f);
        mfit_ = RateFit(cfg.tau_fit);
        sfit_ = RateFit(cfg.tau_fit);
        restart();
    }
    void restart() {
        head_ = 0;
        pos_ = 0;
        ratio_ = 1;
        primed_ = false;
        pulled_ = 0;
        out_total_ = 0;
        last_sf_ = 0;
        maxpkt_ = 0;
        target_ = cfg_.target_fill;
        mfit_.reset();
        sfit_.reset();
    }
    double ratio() const { return ratio_; }
    double fill() const { return fill_; }
    bool locked() const { return mfit_.ok() && sfit_.ok(); }
    size_t underruns() const { return underruns_; }
    double delay() const { return target_ + SincTable::TAPS / 2; }     // frames this path adds
    double target() const { return target_; }
    double wanted() const { return std::max(want_, target_); }   // what this follower would pick if it started now
    void reprime() { primed_ = false; }                          // jump to a new target (one block of silence)
    bool primed_now() const { return primed_; }
    // several followers that must line up share one target: the engine raises every floor to the largest target
    void set_floor(double f) { floor_ = f; }

    // Produce n output frames (interleaved). t_now = master time (seconds) of this block. False = silence written.
    bool process(float *out, size_t n, double t_now) {
        static const SincTable table;
        const int T = SincTable::TAPS;
        const Stamp st = src_->stamp();
        if (st.frames != last_sf_) {                     // new source data: one more point of the source clock
            if (last_sf_) maxpkt_ = std::max(maxpkt_, (double)(st.frames - last_sf_));
            sfit_.add(st.t, (double)st.frames);
            last_sf_ = st.frames;
        }
        // The fill must cover the biggest source packet seen (twice, for scheduling hiccups) + one block + the sinc
        // window. It is chosen before starting and then frozen: changing it while running would mean gliding the
        // pitch for many seconds (or a dropout) to reach the new delay.
        want_ = std::max(cfg_.target_fill, 2 * maxpkt_ + (double)n + T + 64);
        if (!primed_) target_ = std::max(want_, floor_);
        mfit_.add(t_now, (double)out_total_);
        // smooth master time of this block: the master fit's idea of when frame out_total_ happened. Using the raw
        // (jittery) callback time here would turn timestamp jitter straight into pitch wobble.
        const double t_blk = mfit_.ok() ? mfit_.time_of((double)out_total_) : t_now;
        out_total_ += n;
        if (!primed_) {
            // Start only once both clocks are measured, then jump straight to the exact target: the fill is
            // measured from the clock fit, so priming from "frames available" would leave an offset that the slow
            // loop then needs minutes to crawl away (and the two units would not line up until then).
            const double s_est = locked() ? sfit_.at(t_blk) : 0.0;
            const double first = s_est - target_ - T;            // frame index the window must start at
            const uint64_t rt = src_->read_total();
            if (!locked() || first < (double)rt || (double)src_->available() < first - (double)rt + T) {
                if (src_->available() > (size_t)(target_ + T + n) * 4) src_->skip(src_->available() - (size_t)(target_ + T + n) * 2);
                std::fill(out, out + n * ch_, 0.0f);
                return false;
            }
            src_->skip((size_t)(first - (double)rt));
            head_ = 0;
            src_->read(&hist_[0], T);
            pulled_ = src_->read_total();
            pos_ = 0;
            primed_ = true;
        }
        // where the source is now (from its clock fit), minus where we read = the true fill
        const double s_now = sfit_.ok() ? sfit_.at(t_blk) : (double)st.frames;
        fill_ = s_now - ((double)pulled_ + pos_);
        const double base = locked() ? sfit_.rate() / mfit_.rate() : 1.0;   // RateFit::ok() needs > 5 s of data
        const double corr = (fill_ - target_) / (fs_ * cfg_.tau_hold);
        const double lim = cfg_.max_ppm * 1e-6;
        const double want = base * (1 + std::max(-lim, std::min(lim, corr)));
        // the fits get one new (jittery) point per block; follow them through a slow one-pole so the read speed
        // never steps from block to block (that would be pitch noise), only glides
        const double a = 1.0 - std::exp(-(double)n / (fs_ * cfg_.tau_glide));
        ratio_ = (ratio_ == 0 || !locked()) ? want : ratio_ + a * (want - ratio_);

        for (size_t i = 0; i < n; i++) {
            while (pos_ >= 1.0) {                        // slide the window one frame
                if (src_->available() < 1) {
                    std::fill(out + i * ch_, out + n * ch_, 0.0f);
                    underruns_++;
                    primed_ = false;
                    return false;
                }
                if (head_ + T >= cap_) {
                    std::memmove(&hist_[0], &hist_[(head_ + 1) * ch_], sizeof(float) * (size_t)(T - 1) * ch_);
                    head_ = 0;
                    src_->read(&hist_[(size_t)(T - 1) * ch_], 1);
                } else {
                    src_->read(&hist_[(head_ + T) * ch_], 1);
                    head_++;
                }
                pulled_++;
                pos_ -= 1.0;
            }
            const double ph = pos_ * SincTable::PHASES;
            const int p0 = (int)ph;
            const float a = (float)(ph - p0);
            const float *c0 = table.phase(p0), *c1 = table.phase(p0 + 1);
            const float *w = &hist_[head_ * ch_];
            for (int c = 0; c < ch_; c++) {
                float s0 = 0, s1 = 0;
                for (int k = 0; k < T; k++) {
                    const float x = w[(size_t)k * ch_ + c];
                    s0 += c0[k] * x;
                    s1 += c1[k] * x;
                }
                out[i * ch_ + c] = s0 + a * (s1 - s0);
            }
            pos_ += ratio_;
        }
        return true;
    }

private:
    int ch_ = 2;
    double fs_ = 48000;
    Ring *src_ = nullptr;
    FollowConfig cfg_;
    std::vector<float> hist_;
    size_t cap_ = 0, head_ = 0;
    double pos_ = 0, ratio_ = 1, fill_ = 0, target_ = 0, maxpkt_ = 0, floor_ = 0, want_ = 0;
    bool primed_ = false;
    uint64_t pulled_ = 0, out_total_ = 0, last_sf_ = 0;
    RateFit mfit_, sfit_;
    size_t underruns_ = 0;
};

}  // namespace rd
