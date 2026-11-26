@echo off
rem Summarise Lakeside's map export (or the map_*.json given) into a small text file beside this script.
py -3.12 "%~dp0gwmb_props.py" %*
pause
