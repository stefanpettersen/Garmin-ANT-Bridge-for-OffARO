# Troubleshooting

- **`[Errno 13] Access denied (insufficient permissions)` on Linux:** verify `lsusb -d 0fcf:1008`, install the supplied udev rule, unplug/replug, inspect `getfacl` on the current `/dev/bus/usb/BBB/DDD` device. Do not run the GUI as root.
- **`ModuleNotFoundError: PySide6`:** use `./run_gui_linux.sh`, which launches `.venv/bin/python`; install requirements inside `.venv` first.
- **No Garmin sender found:** enable Garmin Broadcast Dog Data; confirm correct ANTUSB2 device and pairing/scan mode.
- **No dog shown in OffARO:** confirm OffARO local injection, UDP port, valid name, and GPS fix. Mixed-case source names require an OffARO build with the local-injection validation fix.
- **Tracking stops after broadcasts are disabled:** preserve logs and diagnostic JSONL; report search timeout, channel status and USB session generation. v0.6.9 preserves the v0.6.7 infinite-search configuration.
