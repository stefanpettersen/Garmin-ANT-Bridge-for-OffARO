#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

usage() {
  cat <<'HELP'
Garmin ANT Bridge for OffARO — Linux installer (v0.7.0)
Usage: ./install_linux.sh [--install-udev] [--help]

By default, creates .venv, installs requirements, and checks ANTUSB2.
--install-udev  Also install the bundled udev rule (requires sudo),
                then replug the USB stick. No sudo is needed for the app.
HELP
}
install_udev=0
for arg in "$@"; do
  case "$arg" in
    --install-udev) install_udev=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" -eq 0 ]; then
  echo "Do not run this installer as root. Run as your normal user." >&2
  echo "Use --install-udev to authorize the system rule separately." >&2
  exit 1
fi
command -v python3 >/dev/null || { echo "Python 3 is missing. Install python3 and python3-venv." >&2; exit 1; }
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "Python venv is unavailable. On Mint/Ubuntu: sudo apt install python3-venv" >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "[1/3] Creating local Python environment (.venv)..."
  if ! python3 -m venv .venv; then
    echo "Could not create .venv. On Mint/Ubuntu: sudo apt install python3-venv" >&2
    exit 1
  fi
else
  echo "[1/3] Existing .venv found."
fi
echo "[2/3] Installing/updating Python dependencies..."
.venv/bin/python -m pip install -r requirements.txt

if [ "$install_udev" -eq 1 ]; then
  command -v sudo >/dev/null || { echo "sudo is needed to install the udev rule." >&2; exit 1; }
  command -v udevadm >/dev/null || { echo "udevadm is unavailable on this system." >&2; exit 1; }
  echo "Installing ANTUSB2 udev rule (sudo required)..."
  sudo install -m 0644 docs/99-garmin-ant.rules /etc/udev/rules.d/99-garmin-ant.rules
  sudo udevadm control --reload-rules
  echo "Unplug and replug the ANTUSB2 to apply the rule."
fi

echo "[3/3] Checking ANTUSB2 (USB ID 0fcf:1008)..."
if command -v lsusb >/dev/null 2>&1; then
  if lsusb -d 0fcf:1008 | grep -q .; then
    echo "ANTUSB2 detected."
    dev=$(lsusb -d 0fcf:1008 | head -n 1 | awk '{print "/dev/bus/usb/" $2 "/" substr($4,1,3)}')
    if [ -e "$dev" ]; then
      if [ -r "$dev" ] && [ -w "$dev" ]; then
        echo "USB device is readable and writable by the current user."
      else
        echo "WARNING: Your user cannot read/write $dev."
        echo "Try: ./install_linux.sh --install-udev"
        echo "Then unplug/replug the stick and retry."
        echo "On systems without active uaccess ACLs, a dedicated USB group may be required."
      fi
    fi
  else
    echo "ANTUSB2 not connected (or not detected). You can connect it later."
  fi
else
  echo "lsusb not found. On Mint/Ubuntu: sudo apt install usbutils"
fi
cat <<'DONE'

Installation complete.
Start the GUI with: ./run_gui_linux.sh
For system packages on Mint/Ubuntu:
  sudo apt install python3 python3-venv python3-pip libusb-1.0-0 usbutils
If USB access fails, see docs/INSTALLATION.md.
DONE
