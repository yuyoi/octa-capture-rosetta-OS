// Rosetta Dual: how good are the OCTA's capture timestamps? Opens pair 1-2 of each unit (exclusive), logs
// (frames, QPC time) per packet for N seconds, and fits a line per unit. Prints each unit's rate, the timestamp
// jitter (residual) and the ratio between the units.
// stamps [seconds]
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "core/ring.h"
#include "win/wasapi_unit.h"

struct Fit {
    double rate = 0, a = 0, rms = 0, pp = 0;
};
static Fit fit(const std::vector<double> &t, const std::vector<double> &f) {
    const size_t n = t.size();
    double mt = 0, mf = 0;
    for (size_t i = 0; i < n; i++) mt += t[i], mf += f[i];
    mt /= n;
    mf /= n;
    double sxx = 0, sxy = 0;
    for (size_t i = 0; i < n; i++) sxx += (t[i] - mt) * (t[i] - mt), sxy += (t[i] - mt) * (f[i] - mf);
    Fit r;
    r.rate = sxy / sxx;
    r.a = mf - r.rate * mt;
    double lo = 1e9, hi = -1e9;
    for (size_t i = 0; i < n; i++) {
        const double e = (f[i] - (r.a + r.rate * t[i])) / r.rate * 1e6;   // microseconds
        r.rms += e * e;
        lo = std::min(lo, e);
        hi = std::max(hi, e);
    }
    r.rms = std::sqrt(r.rms / n);
    r.pp = hi - lo;
    return r;
}

int main(int argc, char **argv) {
    const double secs = argc > 1 ? atof(argv[1]) : 30;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    auto eps = rd::find_octa_endpoints();
    rd::PairStream s[2];
    rd::Ring r[2];
    for (int u = 0; u < 2; u++) {
        for (auto &e : eps) {
            if (e.unit == u && e.pair == 0 && e.capture) {
                std::string err;
                if (!s[u].open(e.id, true, 44100, err)) {
                    printf("unit %d: %s\n", u + 1, err.c_str());
                    return 1;
                }
            }
        }
        r[u].init(2, 1 << 16);
    }
    for (int u = 0; u < 2; u++) s[u].start();
    std::vector<double> t[2], f[2];
    const double t0 = rd::qpc_seconds();
    HANDLE hs[2] = {s[0].event(), s[1].event()};
    while (rd::qpc_seconds() - t0 < secs) {
        DWORD w = WaitForMultipleObjects(2, hs, FALSE, 200);
        if (w > WAIT_OBJECT_0 + 1) continue;
        const int u = (int)(w - WAIT_OBJECT_0);
        if (s[u].drain(r[u])) {
            const rd::Stamp st = r[u].stamp();
            t[u].push_back(st.t);
            f[u].push_back((double)st.frames);
            r[u].skip(r[u].available());
        }
    }
    if (getenv("RD_DUMP")) {                             // all packets: unit, arrival time, frames total
        FILE *fp = fopen(getenv("RD_DUMP"), "w");
        for (int u = 0; fp && u < 2; u++)
            for (size_t i = 0; i < t[u].size(); i++) fprintf(fp, "%d %.7f %.0f\n", u, t[u][i], f[u][i]);
        if (fp) fclose(fp);
    }
    Fit F[2];
    for (int u = 0; u < 2; u++) {
        // drop the first second (start-up)
        std::vector<double> tt, ff;
        for (size_t i = 0; i < t[u].size(); i++)
            if (t[u][i] - t[u][0] > 1) tt.push_back(t[u][i]), ff.push_back(f[u][i]);
        F[u] = fit(tt, ff);
        printf("unit %d: %zu packets, rate %.3f Hz (%+.1f ppm vs PC clock), timestamp jitter %.1f us rms, %.1f us p-p, "
               "glitches %zu\n",
               u + 1, tt.size(), F[u].rate, (F[u].rate / 44100 - 1) * 1e6, F[u].rms, F[u].pp, s[u].glitches(), (unsigned long long)s[u].gap_frames());
    }
    printf("unit 2 / unit 1 = %.8f (%+.2f ppm)\n", F[1].rate / F[0].rate, (F[1].rate / F[0].rate - 1) * 1e6);
    return 0;
}
