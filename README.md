# Garmin ANT Bridge for OffARO

**v0.7.0 — Public Technical Preview / Reference Implementation**

A community-developed Python bridge that receives Garmin **Broadcast Dog Data** over an ANTUSB2 USB stick and forwards dog locations and status to **OffARO** using local UDP (TNC2/APRS or JSON). This is an independent project, not an official Garmin or ANT+ product.

**Maintainer:** Stefan Pettersen. For questions, bug reports and compatibility results, please use **GitHub Issues**. GitHub Discussions may be enabled later. No public email address is provided.

## Project status

- Demonstrated end-to-end on Windows and Linux Mint using an ANTUSB2 (USB VID:PID `0fcf:1008`).
- Live Garmin dog locations, names, status flags and OffARO injection demonstrated.
- Recovery after temporarily disabling Garmin broadcasts tested on Windows with v0.6.7; extended soak testing and additional Garmin devices remain to be verified.
- This release changes packaging, Linux launch and documentation, **not** the ANT receive/recovery algorithm.

## Quick start

See [Installation](docs/INSTALLATION.md). On Linux, run `bash install_linux.sh` to install dependencies into `.venv`, then `bash run_gui_linux.sh`. On Windows, install Python and dependencies, then run `run_gui_windows.bat`. Select a Garmin transmitter in the GUI, then start tracking. Default OffARO endpoint: `127.0.0.1:2023`.

Enable **Broadcast Dog Data** on the Garmin device. Enable local position injection in OffARO. For local injection, note that some OffARO builds may reject mixed-case dog names; confirm the OffARO version includes the local-injection validation fix.

## Documentation

- [Installation and USB permissions](docs/INSTALLATION.md)
- [Garmin ANT protocol observations and captured packets](docs/GARMIN_ANT_PROTOCOL.md)
- [OffARO integration guidance](docs/OFFARO_INTEGRATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Changes](CHANGELOG.md)

## Dependencies and acknowledgements

- [OpenANT](https://github.com/Tigge/openant) — ANT communication from Python.
- [PyUSB](https://github.com/pyusb/pyusb) — USB access.
- [libusb](https://libusb.info/) — USB backend (platform-dependent).
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) — standalone GUI.
- [ANT Message Protocol and Usage, Rev. 5.1](https://www.thisisant.com/assets/resources/ANT%20Protocol/D00000652_ANT_Message_Protocol_and_Usage_Rev_5.1.pdf) — ANT message IDs and search timeout semantics.

The code is provided as an independent reference implementation. Review upstream package licenses and distribution obligations before shipping bundled binaries. Garmin and ANT+ names are used descriptively.

## Contributing and contact

Please open a GitHub Issue for questions, bugs, compatibility reports or requests. Pull requests are welcome. Avoid uploading credentials or other unrelated sensitive information in issues.

## License

The project source code is licensed under the [MIT License](LICENSE). Third-party dependencies retain their own licenses; see [Third-party licensing](docs/THIRD_PARTY_LICENSES.md).

## Linux first-time setup (v0.7.0)

Run `bash install_linux.sh` to create `.venv`, install Python dependencies, and check for an ANTUSB2. If USB access is denied, run `bash install_linux.sh --install-udev`, unplug/replug the stick, and try again. See [Linux installation](docs/INSTALLATION.md). The script does not run the GUI as root.
