@echo off
rem Read every NPC type's model files out of Gw.dat into tools\npc_models. Close Guild Wars first.
py -3.12 "%~dp0npc_models.py" %*
pause
