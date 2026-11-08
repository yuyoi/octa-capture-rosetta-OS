// Rosetta Dual core test: a source on a drifting clock (+38 ppm, the measured OCTA pair), delivered in USB-sized
// packets, followed onto the master clock. Checks lock time, ratio, delay stability and how clean the audio is.
// Build: cl /O2 /EHsc /std:c++17 tests\test_drift.cpp   or   g++ -O2 -std=c++17 tests/test_drift.cpp
#include <cmath>
#include <cstdio>
#include <algorithm>
#include <cstdlib>
#include <vector>

#include "../src/core/drift.h"
#include "../src/core/ring.h"

static const double PI = 3.14159265358979323846;

static int run(double ppm, double fs, size_t block, size_t packet, double secs, double freq, double jitter_ms = 0) {
    const double fs2 = fs * (1 + ppm * 1e-6);
    const int CH = 2;
    rd::Ring ring;
    ring.init(CH, 1 << 15);
    rd::Follower fol;
    fol.init(CH, fs, &ring);
    unsigned rnd = 12345;
    auto jit = [&] { rnd = rnd * 1664525u + 1013904223u; return ((rnd >> 8) / 16777216.0 - 0.5) * 2 * jitter_ms * 1e-3; };
    double t_src = 0, t_dst = 0;                       // time of the next packet / block
    size_t k_src = 0, j = 0;
    std::vector<float> pk(packet * CH), out(block * CH);
    double rmin = 9, rmax = 0, fmin = 1e9, fmax = -1e9;
    std::vector<double> got, when;                     // output samples (ch 0) and their master time
    while (t_dst < secs) {
        if (t_src <= t_dst) {                          // source packet due
            for (size_t i = 0; i < packet; i++) {
                double t = (k_src + i) / fs2;
                pk[i * CH] = (float)(0.5 * std::sin(2 * PI * freq * t));
                pk[i * CH + 1] = (float)(0.5 * std::cos(2 * PI * freq * t));
            }
            k_src += packet;
            ring.write(pk.data(), packet, k_src / fs2 + jit());
            t_src = k_src / fs2;
        } else {                                       // master block due
            bool ok = fol.process(out.data(), block, j / fs + jit());
            if (j / fs > secs - 5) { double r = fol.ratio(); rmin = std::min(rmin, r); rmax = std::max(rmax, r); fmin = std::min(fmin, fol.fill()); fmax = std::max(fmax, fol.fill()); }
            if (getenv("RD_TRACE") && j % (size_t)fs < block)
                std::printf("  t %5.1f ratio %.7f fill %8.1f locked %d ok %d avail %zu\n", j / fs, fol.ratio(), fol.fill(),
                            fol.locked(), ok, ring.available());
            for (size_t i = 0; i < block; i++) {
                if (ok) {
                    got.push_back(out[i * CH]);
                    when.push_back((j + i) / fs);
                }
            }
            j += block;
            t_dst = j / fs;
        }
    }
    // fit the last 5 s and the 5 s before that: a*sin + b*cos at freq -> phase -> delay; residual -> SNR
    auto fit = [&](double t0, double t1, double &delay, double &snr) {
        double ss = 0, sc = 0, cc = 0, ys = 0, yc = 0;
        for (size_t i = 0; i < got.size(); i++) {
            if (when[i] < t0 || when[i] >= t1) continue;
            double s = std::sin(2 * PI * freq * when[i]), c = std::cos(2 * PI * freq * when[i]);
            ss += s * s; sc += s * c; cc += c * c; ys += got[i] * s; yc += got[i] * c;
        }
        double det = ss * cc - sc * sc, a = (ys * cc - yc * sc) / det, b = (yc * ss - ys * sc) / det;
        double ph = std::atan2(b, a);                  // got = A sin(w t + ph) = 0.5 sin(w (t - d))
        delay = -ph / (2 * PI * freq);
        // residual after also allowing a tiny pitch offset (basis s, c, dt*s, dt*c): a sub-ppm speed difference is
        // a slow phase ramp, not distortion. 4x4 normal equations, Gaussian elimination.
        const double tc = (t0 + t1) / 2;
        double M[4][5] = {};
        for (size_t i = 0; i < got.size(); i++) {
            if (when[i] < t0 || when[i] >= t1) continue;
            const double s = std::sin(2 * PI * freq * when[i]), c = std::cos(2 * PI * freq * when[i]), dt = when[i] - tc;
            const double v[4] = {s, c, dt * s, dt * c};
            for (int r = 0; r < 4; r++) {
                for (int k = 0; k < 4; k++) M[r][k] += v[r] * v[k];
                M[r][4] += v[r] * got[i];
            }
        }
        for (int r = 0; r < 4; r++)
            for (int q = r + 1; q < 4; q++) {
                const double f = M[q][r] / M[r][r];
                for (int k = r; k < 5; k++) M[q][k] -= f * M[r][k];
            }
        double x[4];
        for (int r = 3; r >= 0; r--) {
            double v = M[r][4];
            for (int k = r + 1; k < 4; k++) v -= M[r][k] * x[k];
            x[r] = v / M[r][r];
        }
        double e = 0, p = 0;
        for (size_t i = 0; i < got.size(); i++) {
            if (when[i] < t0 || when[i] >= t1) continue;
            const double s = std::sin(2 * PI * freq * when[i]), c = std::cos(2 * PI * freq * when[i]), dt = when[i] - tc;
            const double m = x[0] * s + x[1] * c + x[2] * dt * s + x[3] * dt * c;
            e += (got[i] - m) * (got[i] - m);
            p += m * m;
        }
        snr = 10 * std::log10(p / e);
    };
    // audible quality: tone fitted in short 0.2 s windows (slow timing wander is not distortion)
    double worst = 1e9, dmin = 1e9, dmax = -1e9;
    for (double t0 = secs - 30; t0 < secs - 0.2; t0 += 0.2) {
        double d, sn;
        fit(t0, t0 + 0.2, d, sn);
        worst = std::min(worst, sn);
        dmin = std::min(dmin, d * fs);
        dmax = std::max(dmax, d * fs);
    }
    double d1, s1, d2, s2;
    fit(secs - 10, secs - 5, d1, s1);
    fit(secs - 5, secs, d2, s2);
    double drift_samples = dmax - dmin;               // delay wander over the last 30 s (samples p-p)
    s2 = worst;
    const double want = 1 + ppm * 1e-6;
    std::printf("%+6.1f ppm, jitter %.2f ms, %5.0f Hz block %4zu: ratio %.7f (want %.7f) fill %.1f, delay wander %.3f samples p-p (30 s), "
                "worst SNR %.1f dB, underruns %zu\n",
                ppm, jitter_ms, fs, block, fol.ratio(), want, fol.fill(), drift_samples, s2, fol.underruns());
    std::printf("      last 5 s: ratio %.8f..%.8f (%.2f ppm p-p), fill %.2f..%.2f\n", rmin, rmax, (rmax - rmin) * 1e6, fmin, fmax);
    bool ok = std::fabs(fol.ratio() - want) < 5e-6 && std::fabs(drift_samples) < (jitter_ms > 0 ? 0.5 : 0.01) && s2 > (jitter_ms > 0 ? 80 : 90) && fol.underruns() == 0;
    return ok ? 0 : 1;
}

int main(int argc, char **argv) {
    int bad = 0;
    if (argc > 1) return run(+90, 96000, 512, 960, 20, 15000);
    bad += run(+38.4, 44100, 256, 441, 180, 997);     // the measured pair
    bad += run(-120, 48000, 128, 480, 180, 4000);
    bad += run(+90, 96000, 512, 960, 180, 15000);
    bad += run(0, 44100, 64, 441, 120, 1000);
    bad += run(+38.4, 44100, 256, 441, 180, 997, 0.2);   // with +-0.2 ms timestamp jitter (real USB timing)
    bad += run(+38.4, 48000, 256, 480, 180, 10000, 0.2);
    std::printf(bad ? "FAIL (%d)\n" : "all ok\n", bad);
    return bad;
}
