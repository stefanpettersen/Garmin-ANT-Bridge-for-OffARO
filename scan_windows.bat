@echo off
cd /d "%~dp0"
py -3 garmin_dog_bridge.py --scan --scan-duration 75 --scan-dwell 15
pause
