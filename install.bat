@echo off
rem Install what the Kryta Construction Set needs, into the 64-bit Python 3.12 it runs with,
rem then run the offline tests. Double-click it, or run it from this folder.
setlocal
cd /d "%~dp0"

echo === Kryta Construction Set: install ===
echo.

py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo Python 3.12 was not found.
    echo Install the 64-bit Python 3.12 from https://www.python.org/downloads/ and run this again.
    goto failed
)

py -3.12 -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
    echo The Python 3.12 found is 32-bit. PySide6 needs the 64-bit Python 3.12.
    echo Install it from https://www.python.org/downloads/ and run this again.
    goto failed
)

echo Installing PySide6, lupa and pytest ...
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements.txt pytest
if errorlevel 1 (
    echo.
    echo The install failed: see the messages above.
    goto failed
)

echo.
echo Checking that Lua runs ...
py -3.12 -c "import lupa.lua54 as l; print(l.LuaRuntime().eval('_VERSION'), 'through lupa', l.LUA_VERSION)"
if errorlevel 1 (
    echo lupa is installed but Lua did not start. The editor still works; Check reads syntax only.
)

echo.
echo Running the tests ...
py -3.12 -m pytest tests -q
if errorlevel 1 (
    echo.
    echo Some tests failed. Copy the text above and send it to Claude.
    goto failed
)

echo.
echo All done. Start the editor with run_editor.bat; Gameplay ^> Edit Scripts ^(Ctrl+E^) opens the Lua.
pause
exit /b 0

:failed
echo.
pause
exit /b 1
