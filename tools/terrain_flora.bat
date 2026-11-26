@echo off
rem Picture where the pre-Searing map's ground textures are, with each texture's tag-5 value.
rem Writes tools\terrain_flora_113021\. Give another map file id as the first argument.
cd /d "%~dp0.."
py -3.12 "%~dp0terrain_flora.py" %*
pause
