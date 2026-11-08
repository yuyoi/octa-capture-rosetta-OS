@echo off
rem Bench meter for any ASIO interface. Needs the ASIO SDK in dual\_sdk\ASIOSDK.
call "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>nul
cd /d "%~dp0"
set SDK=_sdk\ASIOSDK
cl /nologo /O2 /EHsc /std:c++17 /W3 /DNOMINMAX /D_CRT_SECURE_NO_WARNINGS /I%SDK%\common /I%SDK%\host /I%SDK%\host\pc tests\asiocap.cpp %SDK%\common\asio.cpp %SDK%\host\asiodrivers.cpp %SDK%\host\pc\asiolist.cpp /Fobuild\ /Febuild\asiocap.exe /link ole32.lib oleaut32.lib advapi32.lib user32.lib
