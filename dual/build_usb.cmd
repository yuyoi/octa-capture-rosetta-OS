@echo off
call "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "%~dp0"
if not exist build mkdir build
set CF=/nologo /O2 /EHsc /std:c++17 /W3 /DNOMINMAX /D_CRT_SECURE_NO_WARNINGS
cl %CF% tests\usbprobe.cpp /Fobuild\ /Febuild\usbprobe.exe ole32.lib advapi32.lib || exit /b 1
cl %CF% tests\usbdual.cpp /Fobuild\ /Febuild\usbdual.exe ole32.lib advapi32.lib || exit /b 1
