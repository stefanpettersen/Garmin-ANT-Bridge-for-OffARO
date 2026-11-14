@echo off
setlocal
cd /d "%~dp0"
title Garmin Dog Bridge v0.3.3 - Receive Only
py garmin_dog_bridge.py --no-offaro
echo.
pause
