@echo off
rem Start the Kryta Construction Set with the 64-bit Python 3.12 that has PySide6.
py -3.12 -c "import PySide6" >nul 2>&1
if errorlevel 1 goto fallback
py -3.12 "%~dp0run_editor.py" %*
if errorlevel 1 pause
exit /b %errorlevel%

:fallback
echo Python 3.12 with PySide6 was not found; trying the default Python.
echo (Install with:  py -3.12 -m pip install PySide6)
py "%~dp0run_editor.py" %*
if errorlevel 1 pause
exit /b %errorlevel%
