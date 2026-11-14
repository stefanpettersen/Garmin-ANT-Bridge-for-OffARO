@echo off
setlocal
cd /d "%~dp0"
title Garmin Dog Bridge v0.3.3 - DEBUG
py garmin_dog_bridge.py --raw --debug
echo.
pause
