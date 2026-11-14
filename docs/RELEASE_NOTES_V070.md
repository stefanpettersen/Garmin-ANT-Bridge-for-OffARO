# v0.7.0 — Public Technical Preview

This release is intended for review, interoperability experiments, and community testing. It is **not** a production-stability claim.

## Verified so far

- End-to-end Garmin ANTUSB2 → bridge → local OffARO injection demonstrated on Windows and Linux Mint.
- Broadcast OFF/ON recovery observed on Windows using v0.6.7 search-timeout configuration.
- Linux v0.6.9 first-time install and GUI startup reported successful.

## Not yet verified comprehensively

- 24-hour uninterrupted operation, Linux recovery after extended broadcast loss, multiple dogs in parallel, Astro 320, clean Windows installs, and macOS.

## Scope of this release

Source, English documentation, selected diagnostic records and installation scripts. No bundled Python runtime, GUI executable or USB drivers. ANT receive/decoder/recovery algorithms are unchanged from v0.6.9.
