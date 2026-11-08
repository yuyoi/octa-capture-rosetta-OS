// Rosetta Dual test host: loads the driver like a DAW would, runs it N seconds, reports callbacks, levels, ratio.
// dualhost [seconds] [buffer]
// Copyright (C) 2026 JunkSmithWizard + Claude. GPLv3, see dual/LICENSE.
#include <windows.h>
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

// load build\RosettaDual.dll straight from disk (no registry: for testing before regsvr32)
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
static double peak[40];

static void bufferSwitch(long idx, ASIOBool) {
    calls++;
    for (long c = 0; c < nin; c++) {
        const int *p = (const int *)bi[c].buffers[idx];
        for (long i = 0; i < frames; i++) {
            const double v = std::fabs(p[i] / 2147483648.0);
            if (v > peak[c]) peak[c] = v;
        }
    }
    for (long c = 0; c < nout; c++) memset(bi[nin + c].buffers[idx], 0, frames * 4);
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

static LONG WINAPI veh(EXCEPTION_POINTERS *e) {
    if (e->ExceptionRecord->ExceptionCode < 0x80000000) return EXCEPTION_CONTINUE_SEARCH;
    HMODULE m = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)e->ExceptionRecord->ExceptionAddress, &m);
    char name[MAX_PATH] = "?";
    if (m) GetModuleFileNameA(m, name, MAX_PATH);
    printf("EXCEPTION %08lx at %p = %s +0x%llx (thread %lu)\n", e->ExceptionRecord->ExceptionCode,
           e->ExceptionRecord->ExceptionAddress, name,
           (unsigned long long)((char *)e->ExceptionRecord->ExceptionAddress - (char *)m), GetCurrentThreadId());
    fflush(stdout);
    return EXCEPTION_CONTINUE_SEARCH;
}

int main(int argc, char **argv) {
    AddVectoredExceptionHandler(1, veh);
    const int secs = argc > 1 ? atoi(argv[1]) : 10;
    frames = argc > 2 ? atol(argv[2]) : 256;
    CoInitialize(nullptr);
    const char *direct = getenv("RD_DLL");
    if (direct ? !load_direct(direct) : !loadAsioDriver((char *)"Rosetta Dual")) {
        printf("Rosetta Dual is not registered (regsvr32 build\RosettaDual.dll)\n");
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
    ASIOGetLatencies(&il, &ol);
    if (ASIOStart() != ASE_OK) {
        ASIODriverInfo d2 = {};
        printf("start failed\n");
        return 1;
    }
    for (int s = 0; s < secs; s++) {
        Sleep(1000);
        printf("t %2d s: %ld callbacks (%.1f per s expected %.1f)\n", s + 1, calls, calls / (s + 1.0), sr / frames);
    }
    ASIOStop();
    ASIOGetLatencies(&il, &ol);
    printf("latency in %ld out %ld frames\npeaks:", il, ol);
    for (long c = 0; c < nin; c++) printf("%s %.1f", c % 10 ? "" : "\n ", peak[c] > 0 ? 20 * std::log10(peak[c]) : -999.0);
    printf(" dBFS\n");
    ASIODisposeBuffers();
    if (direct) {                                        // not loaded through the registry: release it ourselves
        theAsioDriver->Release();
        theAsioDriver = nullptr;
    } else {
        ASIOExit();
    }
    return 0;
}
