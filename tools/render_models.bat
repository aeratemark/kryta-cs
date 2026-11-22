@echo off
rem Renders every donor prop model to tools\model_renders (see render_models.py).
py -3.12 "%~dp0render_models.py" %*
pause
