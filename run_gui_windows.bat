@echo off
cd /d "%~dp0"
py -X utf8 garmin_gui.py
if errorlevel 1 pause
