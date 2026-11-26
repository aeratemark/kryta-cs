@echo off
rem Copy pre-Searing's Zones part and its file list out of Gw.dat into tools\zones_113021. Close Guild Wars first.
py -3.12 "%~dp0zones_dump.py" %*
pause
