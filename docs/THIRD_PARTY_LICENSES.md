# Third-party dependencies and licensing

This repository licenses its **project-authored code** under MIT. This does not relicense external libraries, USB drivers, or protocol specifications.

Runtime dependencies are installed separately from upstream distributions by the install scripts, not vendored in this source archive:

- [OpenANT](https://github.com/Tigge/openant) — Python ANT interface. Consult the license shipped with the **exact installed version**.
- [PyUSB](https://github.com/pyusb/pyusb) — Python USB API. Consult its installed distribution license.
- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) — Qt GUI bindings; multiple license options and deployment obligations apply.
- [libusb](https://libusb.info/) — USB backend, subject to its own license.

**Before bundling executables or distributing third-party binaries**, confirm licenses and required notices for the specific versions included. The MIT license above applies only to code owned by the project maintainer or contributed under compatible terms.

Garmin and ANT+ are trademarks of their respective owners. This is an independent implementation and is not endorsed by them.
