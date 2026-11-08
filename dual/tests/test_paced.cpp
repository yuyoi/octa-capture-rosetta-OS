// Rosetta Dual core test, paced source: the content runs on the unit's own clock (+2.4 ppm here). Packets are handed
// over on the PC's nominal timer, and an extra catch-up packet comes whenever the backlog passes one packet - which
// is what Roland's driver does with the second OCTA. The follower must keep the content at a fixed delay.
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <cmath>
#include <cstdio>
#include <vector>

#include "../src/core/drift.h"

static const double PI = 3.14159265358979323846;

int run(double ppm, double start_backlog, double secs, double prior = 1.0) {
    const double fs = 44100, fs2 = fs * (1 + ppm * 1e-6), freq = 997;
    const size_t P = 441, B = 256;
    rd::Ring ring;
    ring.init(2, 1 << 16);
    rd::FollowConfig fc;
    fc.paced = true;
    rd::Follower f;
    f.init(2, fs, &ring, fc);
    f.set_prior_ratio(prior);                  // the ratio remembered from the last session
    std::vector<std::pair<double, uint64_t>> pending;
    f.set_events([&] { auto v = pending; pending.clear(); return v; });
    double backlog = start_backlog;             // frames the "driver" holds back
    uint64_t made = 0, handed = 0;              // content frames produced / handed over
    std::vector<float> pk(P * 2), out(B * 2);
    double t_pk = 0, t_blk = 0;
    size_t j = 0;
    std::vector<double> got, when;
    auto content = [&](uint64_t k, float *d) {
        const double t = k / fs2;
        d[0] = (float)(0.5 * std::sin(2 * PI * freq * t));
        d[1] = (float)(0.5 * std::cos(2 * PI * freq * t));
    };
    while (t_blk < secs) {
        if (t_pk <= t_blk) {
            // content produced up to now = fs2 * t; the driver hands over one packet, plus one more if it is behind
            const uint64_t avail = (uint64_t)(fs2 * t_pk + 1e-9) + (uint64_t)start_backlog;
            int n = avail >= handed + P ? 1 : 0;
            if (n && avail - (handed + P) >= P + (uint64_t)P / 2) n = 2;           // catch-up
            for (int k = 0; k < n; k++) {
                for (size_t i = 0; i < P; i++) content(handed + i, &pk[i * 2]);
                handed += P;
                ring.write(pk.data(), P, t_pk);
                if (k == 1) pending.push_back({t_pk, ring.stamp().frames});
            }
            t_pk += P / fs;                    // the PC's nominal timer
        } else {
            const bool ok = f.process(out.data(), B, j / fs);
            for (size_t i = 0; ok && i < B; i++) got.push_back(out[i * 2]), when.push_back((j + i) / fs);
            j += B;
            t_blk = j / fs;
        }
    }
    auto delay_at = [&](double t0, double t1) {
        double ss = 0, sc = 0, cc = 0, ys = 0, yc = 0;
        for (size_t i = 0; i < got.size(); i++) {
            if (when[i] < t0 || when[i] >= t1) continue;
            const double s = std::sin(2 * PI * freq * when[i]), c = std::cos(2 * PI * freq * when[i]);
            ss += s * s, sc += s * c, cc += c * c, ys += got[i] * s, yc += got[i] * c;
        }
        const double det = ss * cc - sc * sc, a = (ys * cc - yc * sc) / det, b = (yc * ss - ys * sc) / det;
        return -std::atan2(b, a) / (2 * PI * freq) * fs;   // samples (mod one period of the tone)
    };
    double lo = 1e9, hi = -1e9;
    for (double t = secs - 120; t < secs - 1; t += 1) {
        const double d = delay_at(t, t + 1);
        lo = std::min(lo, d), hi = std::max(hi, d);
    }
    printf("%+5.1f ppm, prior %+.1f ppm, start backlog %3.0f: catch-ups %zu, learned ratio %.7f (want %.7f), delay wander %.3f samples "
           "p-p over the last 120 s, underruns %zu\n",
           ppm, (prior - 1) * 1e6, start_backlog, f.catchups(), f.learned_ratio(), 1 + ppm * 1e-6, hi - lo, f.underruns());
    const bool learned_ok = f.learned_ratio() == 0 || std::fabs(f.learned_ratio() - (1 + ppm * 1e-6)) < 3e-7;
    return (hi - lo < 1.0 && learned_ok && f.underruns() == 0) ? 0 : 1;
}

int main() {
    int bad = 0;
    bad += run(+124, 50, 400);     // the measured situation: driver timer ~124 ppm slower than unit 2's content
    bad += run(+124, 400, 400);
    bad += run(+30, 200, 600, 1 + 30e-6);   // rare catch-ups: works from the remembered ratio
    run(+30, 200, 600);                     // ...and without a memory: drifts until two catch-ups (shown, not judged)
    printf(bad ? "FAIL (%d)\n" : "all ok\n", bad);
    return bad;
}
