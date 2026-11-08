@echo off
rem Rosetta Dual - Windows build (MSVC 2022 Build Tools or Community).
rem   build.cmd          driver DLL + test host + core test
rem   build.cmd test     core test only
rem The ASIO SDK 2.3.4 (GPLv3 option) is expected in dual\_sdk\ASIOSDK (not in git: download it from
rem https://www.steinberg.net/asiosdk and unzip it there).
setlocal
set VC=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat
if not exist "%VC%" set VC=%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat
call "%VC%" >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
set CF=/nologo /O2 /EHsc /std:c++17 /W3 /DNOMINMAX /D_CRT_SECURE_NO_WARNINGS
cl %CF% tests\test_drift.cpp /Fobuild\ /Febuild\test_drift.exe || exit /b 1
cl %CF% tests\test_paced.cpp /Fobuild\ /Febuild\test_paced.exe || exit /b 1
if "%1"=="test" exit /b 0
set SDK=_sdk\ASIOSDK
if not exist %SDK%\common\asio.h (echo ASIO SDK missing in dual\%SDK% & exit /b 1)
cl %CF% /LD /I%SDK%\common /Isrc ^
   src\win\rosetta_dual.cpp src\win\wasapi_unit.cpp ^
   %SDK%\common\combase.cpp %SDK%\common\dllentry.cpp %SDK%\common\register.cpp ^
   /Fobuild\ /Febuild\RosettaDual.dll /link /DEF:src\win\rosetta_dual.def ^
   ole32.lib oleaut32.lib uuid.lib advapi32.lib user32.lib winmm.lib avrt.lib || exit /b 1
cl %CF% /I%SDK%\common /I%SDK%\host /I%SDK%\host\pc tests\dualhost.cpp ^
   %SDK%\common\asio.cpp %SDK%\host\asiodrivers.cpp %SDK%\host\pc\asiolist.cpp ^
   /Fobuild\ /Febuild\dualhost.exe /link ole32.lib oleaut32.lib advapi32.lib user32.lib || exit /b 1
echo built build\RosettaDual.dll, build\dualhost.exe, build\test_drift.exe
