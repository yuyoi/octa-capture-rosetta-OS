// Rosetta Dual - lock-free single-producer / single-consumer ring of interleaved float frames, with a write stamp
// (time of the newest write + total frames written, read consistently through a seqlock).
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace rd {

struct Stamp {
    double t = 0;          // seconds (caller's monotonic clock) at which `frames` had been produced
    uint64_t frames = 0;   // total frames written so far
};

class Ring {
public:
    void init(int channels, size_t frames_pow2) {
        ch_ = channels;
        cap_ = frames_pow2;
        mask_ = frames_pow2 - 1;
        buf_.assign(cap_ * ch_, 0.0f);
        w_.store(0);
        r_.store(0);
        seq_.store(0);
        st_t_ = 0;
        st_f_ = 0;
    }
    int channels() const { return ch_; }
    size_t available() const { return (size_t)(w_.load(std::memory_order_acquire) - r_.load(std::memory_order_acquire)); }

    // producer: append n frames; t = time the LAST of them was produced (or of the packet end). Never blocks:
    // if the consumer stalls the oldest frames are overwritten.
    void write(const float *src, size_t n, double t) {
        uint64_t w = w_.load(std::memory_order_relaxed);
        for (size_t i = 0; i < n; i++) std::memcpy(&buf_[((w + i) & mask_) * ch_], src + i * ch_, sizeof(float) * ch_);
        w_.store(w + n, std::memory_order_release);
        uint64_t r = r_.load(std::memory_order_acquire);
        if (w + n - r > cap_) {
            r_.store(w + n - cap_, std::memory_order_release);
            overruns_++;
        }
        seq_.fetch_add(1, std::memory_order_acq_rel);          // odd = writing
        st_t_ = t;
        st_f_ = w + n;
        seq_.fetch_add(1, std::memory_order_acq_rel);
    }
    Stamp stamp() const {
        for (;;) {
            uint32_t s0 = seq_.load(std::memory_order_acquire);
            if (s0 & 1) continue;
            Stamp s{st_t_, st_f_};
            std::atomic_thread_fence(std::memory_order_acquire);
            if (seq_.load(std::memory_order_acquire) == s0) return s;
        }
    }
    // consumer
    void read(float *dst, size_t n) {
        uint64_t r = r_.load(std::memory_order_relaxed);
        for (size_t i = 0; i < n; i++) std::memcpy(dst + i * ch_, &buf_[((r + i) & mask_) * ch_], sizeof(float) * ch_);
        r_.store(r + n, std::memory_order_release);
    }
    void skip(size_t n) { r_.fetch_add(n, std::memory_order_acq_rel); }
    uint64_t read_total() const { return r_.load(std::memory_order_acquire); }
    size_t overruns() const { return overruns_; }

private:
    std::vector<float> buf_;
    int ch_ = 2;
    size_t cap_ = 0, mask_ = 0;
    std::atomic<uint64_t> w_{0}, r_{0};
    std::atomic<uint32_t> seq_{0};
    volatile double st_t_ = 0;
    volatile uint64_t st_f_ = 0;
    size_t overruns_ = 0;
};

}  // namespace rd
