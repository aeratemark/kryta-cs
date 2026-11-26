@echo off
rem Survey the Zones part (flora) of every map in Gw.dat into tools\zones_survey.txt. Close Guild Wars first.
py -3.12 "%~dp0zones_survey.py" %*
pause
