// asiocap - bench meter for any ASIO interface: records every input for a few seconds and prints, per channel, its name,
// peak and RMS in dBFS and the level of a 997 Hz tone (the level octarec's test tone would have).
//   asiocap "<driver name>" [seconds]
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <windows.h>

#include <cmath>
#include <cstdio>
#include <vector>

#include "asiodrivers.h"
#include "asio.h"
#include "iasiodrv.h"

extern AsioDrivers *asioDrivers;
bool loadAsioDriver(char *name);

static std::vector<ASIOBufferInfo> g_bi;
static std::vector<ASIOChannelInfo> g_ci;
static long g_frames = 0;
static double g_rate = 44100;
struct Ch {
    double peak = 0, sum = 0, re = 0, im = 0, phase = 0;
    long long n = 0;
};
static std::vector<Ch> g_ch;
static volatile LONG g_count = 0;

static double sample(const void *b, long i, ASIOSampleType t) {
    switch (t) {
        case ASIOSTInt32LSB: return ((const int *)b)[i] / 2147483648.0;
        case ASIOSTFloat32LSB: return ((const float *)b)[i];
        case ASIOSTFloat64LSB: return ((const double *)b)[i];
        case ASIOSTInt16LSB: return ((const short *)b)[i] / 32768.0;
        case ASIOSTInt24LSB: {
            const unsigned char *p = (const unsigned char *)b + i * 3;
            return (int)((unsigned)p[0] << 8 | (unsigned)p[1] << 16 | (unsigned)p[2] << 24) / 2147483648.0;
        }
        default: return 0;
    }
}

static void bufferSwitch(long idx, ASIOBool) {
    const double w = 2 * 3.14159265358979 * 997.0 / g_rate;
    for (size_t c = 0; c < g_bi.size(); c++) {
        Ch &s = g_ch[c];
        for (long i = 0; i < g_frames; i++) {
            const double x = sample(g_bi[c].buffers[idx], i, g_ci[c].type);
            const double a = std::fabs(x);
            if (a > s.peak) s.peak = a;
            s.sum += x * x;
            s.re += x * std::cos(s.phase), s.im += x * std::sin(s.phase);
            s.phase += w;
            if (s.phase > 6.283185307179586) s.phase -= 6.283185307179586;
            s.n++;
        }
    }
    InterlockedIncrement(&g_count);
}
static ASIOTime *bufferSwitchTimeInfo(ASIOTime *, long idx, ASIOBool d) {
    bufferSwitch(idx, d);
    return nullptr;
}
static void sampleRateChanged(ASIOSampleRate) {}
static long asioMessage(long sel, long val, void *, double *) {
    if (sel == kAsioSelectorSupported) return val == kAsioEngineVersion;
    if (sel == kAsioEngineVersion) return 2;
    return 0;
}

static double db(double v) { return v > 1e-9 ? 20 * std::log10(v) : -180; }

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    const double secs = argc > 2 ? atof(argv[2]) : 3;
    CoInitialize(nullptr);
    if (!loadAsioDriver(argv[1])) {
        printf("cannot load ASIO driver %s\n", argv[1]);
        return 1;
    }
    ASIODriverInfo di = {};
    di.asioVersion = 2;
    if (ASIOInit(&di) != ASE_OK) {
        printf("ASIOInit failed: %s\n", di.errorMessage);
        return 1;
    }
    long nin = 0, nout = 0, mn, mx, pref, gran;
    ASIOGetChannels(&nin, &nout);
    ASIOGetBufferSize(&mn, &mx, &pref, &gran);
    ASIOSampleRate sr = 0;
    ASIOGetSampleRate(&sr);
    g_rate = sr;
    g_frames = pref;
    printf("%s: %ld in, %ld out, %.0f Hz, buffer %ld\n", argv[1], nin, nout, (double)sr, pref);
    g_bi.resize(nin);
    g_ci.resize(nin);
    g_ch.resize(nin);
    for (long i = 0; i < nin; i++) {
        g_bi[i].isInput = ASIOTrue;
        g_bi[i].channelNum = i;
        g_ci[i].channel = i;
        g_ci[i].isInput = ASIOTrue;
        ASIOGetChannelInfo(&g_ci[i]);
    }
    ASIOCallbacks cb = {bufferSwitch, sampleRateChanged, asioMessage, bufferSwitchTimeInfo};
    if (ASIOCreateBuffers(g_bi.data(), nin, pref, &cb) != ASE_OK) {
        printf("ASIOCreateBuffers failed\n");
        return 1;
    }
    if (ASIOStart() != ASE_OK) {
        printf("ASIOStart failed\n");
        return 1;
    }
    Sleep((DWORD)(secs * 1000));
    ASIOStop();
    ASIODisposeBuffers();
    ASIOExit();
    printf("%ld buffers\n", (long)g_count);
    for (long i = 0; i < nin; i++) {
        const Ch &s = g_ch[i];
        const double n = s.n ? (double)s.n : 1;
        printf("%2ld %-26s peak %7.1f  rms %7.1f  997Hz %7.1f\n", i + 1, g_ci[i].name, db(s.peak), db(std::sqrt(s.sum / n)),
               db(2 * std::hypot(s.re, s.im) / n));
    }
    return 0;
}
