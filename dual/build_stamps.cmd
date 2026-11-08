@echo off
call "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "%~dp0"
cl /nologo /O2 /EHsc /std:c++17 /DNOMINMAX /Isrc tests\stamps.cpp src\win\wasapi_unit.cpp /Fobuild\ /Febuild\stamps.exe ole32.lib avrt.lib
