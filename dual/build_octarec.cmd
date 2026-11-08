@echo off
rem octarec with libusbK (static). Needs dual\_sdk\libusbk_dl: libusbK-3.1.0.0-bin.7z and -src.7z from github.com/mcuee/libusbk.
rem Builds build\octarec_new.exe so a running app doesn't lock the linker; copy it over octarec.exe when the app is closed.
call "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>nul
cd /d "%~dp0"
set K=_sdk\libusbk_dl
cl /nologo /O2 /EHsc /std:c++17 /W3 /DNOMINMAX /D_CRT_SECURE_NO_WARNINGS /MT /Isrc /Isrc\win /I%K%\libusbK-3.1.0.0-src\includes src\win\octarec.cpp src\win\kbox.cpp /Fobuild\ /Febuild\octarec_new.exe winusb.lib setupapi.lib %K%\libusbK-3.1.0.0-bin\bin\lib\static\amd64\libusbK.lib legacy_stdio_definitions.lib ole32.lib advapi32.lib user32.lib shell32.lib /link /LTCG
