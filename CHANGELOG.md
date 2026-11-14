# Changelog

## v0.7.0 — Public Technical Preview / Reference Implementation

- First GitHub-oriented source release based on the tested v0.6.9 Linux installer.
- Adopted MIT license for project-authored source code; third-party licenses remain separate.
- Updated English README and installation commands to work even if ZIP extraction strips executable permissions.
- Added explicit third-party license guidance and release/testing scope.
- No changes to ANT+ decoding, USB channel recovery, GUI behavior, or OffARO forwarding.


## v0.6.9 — Linux installer test release

- Added `install_linux.sh` for repeatable first-time setup and dependency installation.
- Added optional `--install-udev` flag, USB detection and access checks.
- Packaged Linux launch and install scripts with executable permissions.
- No changes to ANT+ decoding, channel recovery, or TNC2 forwarding.


## v0.6.8 — reference release

- Linux GUI launcher now uses the project's `.venv` Python explicitly.
- Added least-privilege-oriented udev example and Linux installation instructions.
- Added English README, protocol notes, integration guide, troubleshooting, and real capture samples.
- No changes to Garmin decoding, channel recovery, packet forwarding or GUI logic beyond version label.

## v0.6.7

- Changed ANT `set_search_timeout(0)` to `set_search_timeout(255)` to request infinite channel search, retaining existing recovery logic. See `README_V067.md` for the original test notes.
