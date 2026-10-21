@echo off
rem Build opc2ixon.exe and a zip ready to send.
rem Usage: double-click this file, or run  packaging\build_windows.bat
rem Result: dist\opc2ixon-<version>-Windows.zip
setlocal
cd /d "%~dp0\.."

rem --- Python 3.10-3.14, 64 bit, via the "py" launcher installed with python.org -----
py -3 -c "import sys, struct; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.10-3.14 64 bit not found. Install it from https://www.python.org/downloads/
    goto :error
)
for /f "delims=" %%v in ('py -3 --version') do echo ^>^> Using %%v

rem --- clean build environment (separate from the development .venv) ----------------
if exist .venv-build rmdir /s /q .venv-build
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
py -3 -m venv .venv-build || goto :error
set "PYB=.venv-build\Scripts\python.exe"
"%PYB%" -m pip install --quiet --upgrade pip || goto :error
"%PYB%" -m pip install --quiet -r requirements.txt -r packaging\requirements-build.txt || goto :error

rem --- third party licenses (shipped with the app) ----------------------------------
".venv-build\Scripts\pip-licenses.exe" --with-license-file --no-license-path --format=plain-vertical ^
    --ignore-packages pyinstaller pyinstaller-hooks-contrib pip-licenses prettytable wcwidth ^
    altgraph pefile pywin32-ctypes packaging setuptools pip tomli ^
    --output-file packaging\THIRD_PARTY_LICENSES.txt || goto :error

rem --- build ---------------------------------------------------------------------------
".venv-build\Scripts\pyinstaller.exe" --noconfirm --clean --log-level ERROR packaging\opc2ixon.spec || goto :error

for /f "delims=" %%v in ('"%PYB%" -c "import opc2ixon as m; print(m.__version__)"') do set "VERSION=%%v"
set "ZIP=opc2ixon-%VERSION%-Windows.zip"
rem zip content: opc2ixon\opc2ixon.exe + license notices next to it
copy /y packaging\NOTICE.txt dist\opc2ixon\ >nul || goto :error
copy /y packaging\THIRD_PARTY_LICENSES.txt dist\opc2ixon\ >nul || goto :error
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\opc2ixon' -DestinationPath 'dist\%ZIP%'" || goto :error

echo.
echo ^>^> Done: dist\%ZIP%
echo    Test it: dist\opc2ixon\opc2ixon.exe
echo.
pause
exit /b 0

:error
echo.
echo BUILD FAILED
pause
exit /b 1
