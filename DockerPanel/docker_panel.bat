@echo off
REM Launcher del DockerPanel. Pegalo en Win+R o en Settings > dockerpanel_ruta.
cd /d "%~dp0"
start "" pythonw docker_panel.py
