// Rosetta Dual test host: loads the driver like a DAW would, runs it N seconds, reports callbacks and levels.
//   dualhost [seconds] [buffer]
//   RD_DLL=path\RosettaDual.dll  load the DLL straight from disk (no regsvr32 needed)
//   RD_ALIGN=1                   alignment test: the same quiet noise on outputs 3-8 (cable one of them into unit 2),
//                                record the loudest input of each unit, report how they line up (1 s windows)
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <windows.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "asiodrivers.h"
#include "asio.h"
#include "iasiodrv.h"

extern AsioDrivers *asioDrivers;
bool loadAsioDriver(char *name);
extern IASIO *theAsioDriver;

static bool load_direct(const char *path) {
    static const CLSID clsid = {0x7e4a6b21, 0x6f0c, 0x4d8b, {0x9a, 0x51, 0x52, 0xd0, 0xa2, 0xc1, 0xe7, 0xb9}};
    HMODULE m = LoadLibraryA(path);
    if (!m) return false;
    typedef HRESULT(__stdcall * GetCO)(REFCLSID, REFIID, void **);
    GetCO get = (GetCO)GetProcAddress(m, "DllGetClassObject");
    IClassFactory *cf = nullptr;
    if (!get || FAILED(get(clsid, IID_IClassFactory, (void **)&cf))) return false;
    HRESULT hr = cf->CreateInstance(nullptr, clsid, (void **)&theAsioDriver);
    cf->Release();
    return SUCCEEDED(hr);
}

static ASIOBufferInfo bi[40];
static long nin = 0, nout = 0, frames = 256, calls = 0;
static double peak[40], rms[40], srate = 44100;
static bool align = false;
static long chA = -1, chB = -1;
static std::vector<float> recA, recB;
static unsigned lcg = 1;

static void bufferSwitch(long idx, ASIOBool) {
    calls++;
    for (long c = 0; c < nin; c++) {
        const int *p = (const int *)bi[c].buffers[idx];
        for (long i = 0; i < frames; i++) peak[c] = std::max(peak[c], std::fabs(p[i] / 2147483648.0));
    }
    for (long c = 0; c < nout; c++) memset(bi[nin + c].buffers[idx], 0, frames * 4);
    if (!align) return;
    for (long i = 0; i < frames; i++) {                  // noise, about -20 dBFS, same sample on outputs 3..8
        lcg = lcg * 1664525u + 1013904223u;
        const int v = ((int)(lcg >> 1) - 0x40000000) / 5;
        for (long c = 2; c < 8 && c < nout; c++) ((int *)bi[nin + c].buffers[idx])[i] = v;
    }
    const double t = calls * (double)frames / srate;
    if (t > 7 && t < 9) {
        for (long c = 0; c < nin; c++) {
            const int *p = (const int *)bi[c].buffers[idx];
            for (long i = 0; i < frames; i++) rms[c] += (p[i] / 2147483648.0) * (p[i] / 2147483648.0);
        }
    } else if (t >= 9 && chA < 0) {
        chA = 0;
        chB = 10;
        for (long c = 0; c < 10; c++)
            if (rms[c] > rms[chA]) chA = c;
        for (long c = 10; c < nin; c++)
            if (rms[c] > rms[chB]) chB = c;
        printf("align: unit 1 input %ld (%.1f dB), unit 2 input %ld (%.1f dB)\n", chA + 1,
               10 * std::log10(rms[chA] / (2 * srate) + 1e-20), chB - 9, 10 * std::log10(rms[chB] / (2 * srate) + 1e-20));
    }
    if (chA >= 0) {
        const int *a = (const int *)bi[chA].buffers[idx], *b = (const int *)bi[chB].buffers[idx];
        for (long i = 0; i < frames; i++) {
            recA.push_back(a[i] / 2147483648.0f);
            recB.push_back(b[i] / 2147483648.0f);
        }
    }
}
static ASIOTime *bufferSwitchTimeInfo(ASIOTime *, long idx, ASIOBool d) {
    bufferSwitch(idx, d);
    return nullptr;
}
static void sampleRateChanged(ASIOSampleRate) {}
static long asioMessage(long sel, long v, void *, double *) {
    if (sel == kAsioSelectorSupported) return v == kAsioSupportsTimeInfo || v == kAsioResetRequest;
    if (sel == kAsioSupportsTimeInfo) return 1;
    return 0;
}

static double xcorr(const float *a, const float *b, long n, long lag) {   // sum a[i] * b[i + lag]
    double s = 0;
    for (long i = 0; i < n; i++) s += (double)a[i] * b[i + lag];
    return s;
}

static void analyse() {
    const long W = (long)srate, M = 3000;
    if ((long)recA.size() < 4 * W + 2 * M) {
        printf("align: not enough data\n");
        return;
    }
    long best = 0;
    double bv = -1e30;
    for (long lag = -M; lag <= M; lag++) {               // coarse: full search in the first window
        const double v = xcorr(&recA[M], &recB[M], W, lag);
        if (v > bv) bv = v, best = lag;
    }
    printf("align: unit 2's input arrives %+ld samples vs unit 1's (coarse)\n", best);
    double lmin = 1e9, lmax = -1e9, cmin = 1;
    for (long w = 0; (w + 1) * W + 2 * M < (long)recA.size(); w++) {
        const float *a = &recA[M + w * W], *b = &recB[M + w * W];
        long l0 = best;
        double vb = -1e30;
        for (long lag = best - 8; lag <= best + 8; lag++) {
            const double x = xcorr(a, b, W, lag);
            if (x > vb) vb = x, l0 = lag;
        }
        double v[3];
        for (int k = 0; k < 3; k++) v[k] = xcorr(a, b, W, l0 - 1 + k);
        const double lag = l0 + 0.5 * (v[0] - v[2]) / (v[0] - 2 * v[1] + v[2]);   // parabolic peak
        double ea = 0, eb = 0;
        for (long i = 0; i < W; i++) {
            ea += (double)a[i] * a[i];
            eb += (double)b[i + l0] * b[i + l0];
        }
        const double corr = v[1] / std::sqrt(ea * eb + 1e-30);
        lmin = std::min(lmin, lag);
        lmax = std::max(lmax, lag);
        cmin = std::min(cmin, corr);
        if (w % 10 == 0) printf("  t %3ld s: lag %+9.3f samples, correlation %.5f\n", w + 9, lag, corr);
    }
    printf("align: lag %+.3f .. %+.3f samples (%.3f p-p), worst 1 s correlation %.5f\n", lmin, lmax, lmax - lmin, cmin);
}

static LONG WINAPI veh(EXCEPTION_POINTERS *e) {
    if (e->ExceptionRecord->ExceptionCode < 0x80000000) return EXCEPTION_CONTINUE_SEARCH;
    HMODULE m = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)e->ExceptionRecord->ExceptionAddress, &m);
    char name[MAX_PATH] = "?";
    if (m) GetModuleFileNameA(m, name, MAX_PATH);
    printf("EXCEPTION %08lx at %p = %s +0x%llx\n", e->ExceptionRecord->ExceptionCode, e->ExceptionRecord->ExceptionAddress,
           name, (unsigned long long)((char *)e->ExceptionRecord->ExceptionAddress - (char *)m));
    fflush(stdout);
    return EXCEPTION_CONTINUE_SEARCH;
}

int main(int argc, char **argv) {
    AddVectoredExceptionHandler(1, veh);
    const int secs = argc > 1 ? atoi(argv[1]) : 10;
    frames = argc > 2 ? atol(argv[2]) : 256;
    CoInitialize(nullptr);
    const char *direct = getenv("RD_DLL");
    align = getenv("RD_ALIGN") != nullptr;
    if (align) {
        recA.reserve((size_t)secs * 100000);
        recB.reserve((size_t)secs * 100000);
    }
    if (direct ? !load_direct(direct) : !loadAsioDriver((char *)"Rosetta Dual")) {
        printf("Rosetta Dual is not registered (regsvr32 build\\RosettaDual.dll)\n");
        return 1;
    }
    ASIODriverInfo di = {};
    di.asioVersion = 2;
    if (ASIOInit(&di) != ASE_OK) {
        printf("init failed: %s\n", di.errorMessage);
        return 1;
    }
    ASIOGetChannels(&nin, &nout);
    ASIOSampleRate sr = 0;
    ASIOGetSampleRate(&sr);
    srate = sr;
    printf("%s: %ld in, %ld out, %.0f Hz, buffer %ld\n", di.name, nin, nout, sr, frames);
    for (long c = 0; c < nin + nout; c++) {
        bi[c].isInput = c < nin ? ASIOTrue : ASIOFalse;
        bi[c].channelNum = c < nin ? c : c - nin;
    }
    ASIOCallbacks cb = {bufferSwitch, sampleRateChanged, asioMessage, bufferSwitchTimeInfo};
    if (ASIOCreateBuffers(bi, nin + nout, frames, &cb) != ASE_OK) {
        printf("createBuffers failed\n");
        return 1;
    }
    long il = 0, ol = 0;
    if (ASIOStart() != ASE_OK) {
        printf("start failed\n");
        return 1;
    }
    for (int s = 0; s < secs; s++) {
        Sleep(1000);
        if ((s + 1) % 10 == 0 || s < 3)
            printf("t %3d s: %ld callbacks (%.1f per s, expected %.1f)\n", s + 1, calls, calls / (s + 1.0), sr / frames);
    }
    ASIOStop();
    ASIOGetLatencies(&il, &ol);
    printf("latency in %ld out %ld frames\npeaks (dBFS):", il, ol);
    for (long c = 0; c < nin; c++) printf("%s %.1f", c % 10 ? "" : "\n ", peak[c] > 0 ? 20 * std::log10(peak[c]) : -999.0);
    printf("\n");
    if (align) analyse();
    if (align && getenv("RD_SAVE")) {                    // raw float32 pairs (A, B interleaved) for offline checks
        FILE *f = fopen(getenv("RD_SAVE"), "wb");
        if (f) {
            for (size_t i = 0; i < recA.size(); i++) {
                fwrite(&recA[i], 4, 1, f);
                fwrite(&recB[i], 4, 1, f);
            }
            fclose(f);
        }
    }
    ASIODisposeBuffers();
    if (direct) {
        theAsioDriver->Release();
        theAsioDriver = nullptr;
    } else {
        ASIOExit();
    }
    return 0;
}
