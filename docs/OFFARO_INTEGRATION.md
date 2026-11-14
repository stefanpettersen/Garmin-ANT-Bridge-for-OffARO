# Integration notes for OffARO developers

## Current architecture

Garmin Alpha/Astro broadcast → ANTUSB2 → OpenANT/PyUSB Python process → Garmin page decoder → per-dog state → UDP/TNC2 or JSON → OffARO local position injection.

The standalone PySide6 GUI is a testing and control interface; it is **not** required for the decoder itself. Current default endpoint: `127.0.0.1:2023`.

## Potential integration approaches

1. **Bundled bridge process:** retain the tested Python/OpenANT USB implementation and have OffARO manage its lifecycle; package Python dependencies and USB backend per platform.
2. **Native implementation:** port ANT transport, Garmin page parsing, pairing, recovery and state tracking into OffARO's own technology stack. The Python source and captured frames serve as a reference and regression corpus.

Neither approach is selected here; the OffARO maintainer should decide based on the application's architecture and release process.

## Key reusable source areas

- `decode_location`, `decode_identification`: Garmin data parsing.
- `on_data`: incoming ANT message handling.
- `make_offaro_payload`, `send_offaro_packet`: APRS/TNC2 and JSON output.
- `setup_ant` and channel recovery helpers: USB session, ANT scan/lock, reconnect.
- `garmin_gui.py`: optional standalone interface, not essential to native integration.

## Output example

`HUND>APGDOG:!5911.45N/00942.91EpSitting - GPS:OK - Comm:OK - Battery:OK`

The example illustrates formatting; it is not presented as a byte-for-byte verified record from the bundled captures. The APRS symbol uses primary table `/` and symbol code `p` (dog). Source names are preserved when valid for the bridge's local-injection TNC2 path. Older OffARO builds may reject names based on stricter RF callsign rules; local injection needs appropriate validation.

## Dependencies and deployment

- Standalone bridge: Python, OpenANT, PyUSB, PySide6, platform USB driver/libusb.
- Headless subprocess: Python, OpenANT, PyUSB and USB backend; PySide6 can be omitted if GUI not imported.
- Native implementation: depends on chosen OffARO USB/ANT implementation; **do not assume OpenANT can be dropped without reimplementing its ANT behavior**.
- Linux USB access: see `INSTALLATION.md` and `99-garmin-ant.rules`.
- Windows: validate ANTUSB2 driver/backend on a clean system.

## Outstanding tests

Long-duration receive stability; Linux broadcast OFF/ON recovery; simultaneous multiple dogs; additional Alpha/Astro firmware models; packaging and clean-machine installation. Retain raw packet logging while testing.
