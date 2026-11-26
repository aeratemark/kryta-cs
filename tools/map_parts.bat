@echo off
rem List the parts inside pre-Searing's map file, the test map's donor and the test map. Close Guild Wars first.
py -3.12 "%~dp0map_parts.py" %*
pause
