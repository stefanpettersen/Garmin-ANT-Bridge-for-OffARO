@echo off
setlocal
cd /d "%~dp0"
title Garmin Dog Bridge v0.3.3 - JSON
py garmin_dog_bridge.py --output-format json
echo.
pause
