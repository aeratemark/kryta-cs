@echo off
rem Open real game maps from Gw.dat and build each back unedited; each must come out byte for byte.
rem Writes nothing to Gw.dat. Pass map ids, or "all", to check others than pre-Searing.
py -3.12 "%~dp0game_map_check.py" %*
pause
