@echo off
rem feed.h test without boxes (tests/feedtest.cpp): tone into a playback device, read back by loopback
call "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>nul
cd /d "%~dp0"
cl /nologo /O2 /EHsc /std:c++17 /W3 /DNOMINMAX /DUNICODE /D_UNICODE /MT /Isrc /Isrc\win tests\feedtest.cpp /Fobuild\ /Febuild\feedtest.exe ole32.lib user32.lib
