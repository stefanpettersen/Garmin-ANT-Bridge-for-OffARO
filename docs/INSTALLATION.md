# Installation and USB access

## Linux Mint / Ubuntu

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip libusb-1.0-0 usbutils
bash install_linux.sh
bash run_gui_linux.sh
```

The installer creates or reuses `.venv` and installs Python requirements. Run `bash install_linux.sh --install-udev` to optionally install the bundled udev rule with sudo. Unplug/replug the stick after installing the rule. The launcher uses `.venv/bin/python` explicitly. It does not require activating the environment in your shell.

### ANTUSB2 permissions

Check `lsusb -d 0fcf:1008`. A local desktop session can use the supplied udev rule:

```sh
sudo install -m 0644 docs/99-garmin-ant.rules /etc/udev/rules.d/99-garmin-ant.rules
sudo udevadm control --reload-rules
```

Unplug and replug the ANTUSB2. Verify access with `lsusb` and `getfacl /dev/bus/usb/BBB/DDD` using the current bus/device values. `TAG+="uaccess"` relies on a supported active local seat/session. If it does not grant access (e.g. headless service), create a dedicated USB-access group and assign the device to that group instead; do not routinely use `MODE="0666"` or run the application as root. The broad `0666` rule was used only for initial lab troubleshooting.

## Windows

Install an appropriate Python version and run `install_windows.bat` or `python -m pip install -r requirements.txt`. Start with `run_gui_windows.bat`. The ANTUSB2 must be accessible via a working USB driver/backend; validate on a clean Windows machine before claiming a standalone installer. No standalone executable is included in this source release.

## macOS

Not yet validated. USB backend, permissions, Python environment and distribution require separate testing.

## Runtime dependencies

See `requirements.txt`: OpenANT, PyUSB and PySide6. libusb/USB driver access is an operating-system requirement. The Python GUI dependency can be omitted from a future headless or native OffARO implementation.

### Manual installation (alternative)

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash run_gui_linux.sh
```

If scripts lose executable permissions after extraction, run `chmod +x install_linux.sh run_gui_linux.sh`.
