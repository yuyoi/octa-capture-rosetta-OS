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
    // paced = the source's packets are handed over on someone else's timer (Roland's driver does this for the
    // second unit), with an extra "catch-up" packet now and then. Packet times then say nothing about where the
    // audio really is - except at the catch-ups, which all happen at the same backlog. So the source position is
    // pinned to the catch-ups (events), and its rate is measured between them.
    bool paced = false;
    // level = ignore packet times completely: run at the prior ratio and nudge it very slowly by the averaged buffer
    // fill (tau_level seconds). The sturdy mode when a driver's packet timing can't be trusted (Roland, 2nd unit).
    // fixed = ratio exactly 1, no correction (the clock master itself).
    bool level = false, fixed = false;
    double tau_level = 300.0, tau_level_avg = 30.0, max_level_ppm = 50;
    double guess_frac = 0.5;     // before the first catch-up: assume the backlog is this fraction of a packet
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
    using Events = std::function<std::vector<std::pair<double, uint64_t>>()>;
    void set_events(Events e) { events_ = std::move(e); }
    void set_offset(double frames) { offset_ = frames; }        // calibration: shifts this path's delay
    void set_prior_ratio(double q) { prior_ = q; }               // source/master rate from last time (paced mode)
    // measured source/master rate (paced mode, after two catch-ups), 0 = not known yet
    double learned_ratio() const { return learned_; }
    size_t catchups() const { return evs_.size() + ev_dropped_; }

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
        if (!primed_ && (cfg_.fixed || cfg_.level)) {    // start at the target fill (plus the trim), nothing else
            const double want = target_ + offset_;
            const size_t av = src_->available();
            if (maxpkt_ == 0 || want < 0 || av < (size_t)want + T + n) {
                std::fill(out, out + n * ch_, 0.0f);
                return false;
            }
            src_->skip(av - ((size_t)want + T));
            head_ = 0;
            src_->read(&hist_[0], T);
            pulled_ = src_->read_total();
            pos_ = 0;
            lvl_init_ = false;
            primed_ = true;
        }
        if (!primed_) {
            // Start only once both clocks are measured, then jump straight to the exact target: the fill is
            // measured from the clock fit, so priming from "frames available" would leave an offset that the slow
            // loop then needs minutes to crawl away (and the two units would not line up until then).
            double s_est = 0, b_unused = 1;
            if (locked()) {
                if (cfg_.paced) source_paced(t_blk, st, s_est, b_unused);
                else s_est = sfit_.at(t_blk);
                s_est += offset_;
            }
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
        // where the source is now, minus where we read = the true fill
        double s_now, base;
        if (cfg_.fixed || cfg_.level) {
            const double av = (double)src_->available() + offset_;
            if (!lvl_init_) lvl_avg_ = av, lvl_init_ = true;
            const double a = 1.0 - std::exp(-(double)n / (fs_ * cfg_.tau_level_avg));
            lvl_avg_ += a * (av - lvl_avg_);
            fill_ = lvl_avg_;
            if (cfg_.fixed) {
                ratio_ = 1.0;
            } else {
                // proportional on the averaged fill: ratio = prior + (fill error) / (fs * tau_level), clamped
                const double lim = cfg_.max_level_ppm * 1e-6;
                const double corr = (lvl_avg_ - target_) / (fs_ * cfg_.tau_level);
                ratio_ = prior_ * (1 + std::max(-lim, std::min(lim, corr)));
            }
            return render(out, n, table);
        }
        if (cfg_.paced) {
            source_paced(t_blk, st, s_now, base);
        } else {
            s_now = sfit_.ok() ? sfit_.at(t_blk) : (double)st.frames;
            base = locked() ? sfit_.rate() / mfit_.rate() : 1.0;   // RateFit::ok() needs > 5 s of data
        }
        s_now += offset_;
        fill_ = s_now - ((double)pulled_ + pos_);
        const double corr = (fill_ - target_) / (fs_ * cfg_.tau_hold);
        const double lim = cfg_.max_ppm * 1e-6;
        const double want = base * (1 + std::max(-lim, std::min(lim, corr)));
        // the fits get one new (jittery) point per block; follow them through a slow one-pole so the read speed
        // never steps from block to block (that would be pitch noise), only glides
        const double a = 1.0 - std::exp(-(double)n / (fs_ * cfg_.tau_glide));
        ratio_ = (ratio_ == 0 || !locked()) ? want : ratio_ + a * (want - ratio_);

        return render(out, n, table);
    }

    bool render(float *out, size_t n, const SincTable &table) {
        const int T = SincTable::TAPS;
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
    // paced source: position + rate from the catch-up events
    void source_paced(double t, const Stamp &st, double &s_now, double &base) {
        if (events_) {
            for (const auto &e : events_()) {
                if (!evs_.empty() && e.first - evs_.back().first < 1.0) continue;   // one per catch-up
                evs_.push_back({e.first, (double)e.second});
                if (evs_.size() > 32) evs_.erase(evs_.begin()), ev_dropped_++;
            }
        }
        const double r1 = mfit_.ok() ? mfit_.rate() : fs_;
        double r2 = r1 * prior_;
        if (evs_.size() >= 2 && evs_.back().first - evs_.front().first > 20) {
            r2 = (evs_.back().second - evs_.front().second) / (evs_.back().first - evs_.front().first);
            learned_ = r2 / r1;
        }
        if (evs_.empty()) {
            s_now = (sfit_.ok() ? sfit_.at(t) : (double)st.frames) + cfg_.guess_frac * maxpkt_;
        } else {
            s_now = evs_.back().second + r2 * (t - evs_.back().first);
        }
        base = r2 / r1;
    }

    int ch_ = 2;
    double fs_ = 48000;
    Ring *src_ = nullptr;
    Events events_;
    std::vector<std::pair<double, double>> evs_;
    size_t ev_dropped_ = 0;
    double offset_ = 0, prior_ = 1.0, learned_ = 0, lvl_avg_ = 0;
    bool lvl_init_ = false;
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
