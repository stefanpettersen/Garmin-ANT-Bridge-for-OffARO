# Garmin Broadcast Dog Data — protocol observations

**Scope:** This document describes what the supplied Python reference implementation actually does and what captured test logs demonstrate. It is **not** an official Garmin protocol specification. Raw data are included unmodified (including sender identifiers and coordinates).

## ANT transport and channel setup

| Parameter | Reference implementation |
|---|---|
| ANT+ network key | `B9 A5 21 FB BD 72 C3 45` |
| Device type | `41` (`0x29`) |
| RF frequency setting | `57` (ANT RF channel, nominal 2457 MHz) |
| Message period | `2048` ANT ticks (tracked receive mode) |
| Search timeout | `255` via `set_search_timeout` (`0x44`), intended infinite search |
| USB hardware tested | Dynastream ANTUSB2, `0fcf:1008` |
| Scan mode | OpenANT `open_rx_scan_mode()` (`0x5B`), multiple matching transmitters |
| Tracking mode | Bidirectional receive channel locked to selected device number/type/transmission type when known |

`0x44` is **Channel Search Timeout**, `0x51` is **Channel ID**, and `0x63` is **Low Priority Search Timeout** in ANT protocol documentation. The timeout fix introduced in v0.6.7 is preserved unchanged in v0.6.9. No change to the recovery state machine is made here.

## Page IDs observed/handled by the decoder

| Page | Decoder interpretation |
|---|---|
| `0x01` | Location part 1 |
| `0x02` | Location part 2 |
| `0x03` | No assets |
| `0x10` | Identification part 1 |
| `0x11` | Identification part 2 |
| `0x20` | Disconnect |
| `0x46` | Request data (bridge sends request for identification) |

These are **implementation interpretations**; additional page types (including `0x04`/`0x05`) occur in the supplied captures and should not be assumed to have been fully decoded.

## Decoding implemented in `garmin_dog_bridge.py`

- Asset index: `data[1] & 0x1F`.
- Location pages `0x01` and `0x02` are paired for the same asset index.
- Page `0x01`: distance `data[2] | (data[3] << 8)` metres; bearing `data[4] * 360/256` degrees; flags `data[5]`.
- Flags: low 3 bits map to `sitting`, `moving`, `pointed`, `treed` (other values unknown); bit 3 low battery; bit 4 GPS lost; bit 5 communications lost; bit 6 remove asset.
- Latitude: signed little-endian 32-bit integer from page 1 bytes 6–7 followed by page 2 bytes 2–3; longitude: signed little-endian 32-bit integer from page 2 bytes 4–7. Convert ANT semicircles using `degrees = raw * 180 / 2^31`.
- Identification pages `0x10`/`0x11`: name bytes are concatenated from `[3:8]` of both pages, stripped of trailing `00`/`FF`, decoded as UTF-8. Name case is preserved.
- Bridge sends request page `46 FF FF FF FF 04 10 04` when requesting identification.

## Raw captures and decoded examples

See [`../examples/ant-capture-samples.jsonl`](../examples/ant-capture-samples.jsonl) for **verbatim records selected from previously captured diagnostic logs**, and [`../examples/README.md`](../examples/README.md) for capture provenance. Example `position_decoded` records include the original first/second hex payloads and computed coordinates. Captures are not guaranteed to be from v0.6.9 and are not a comprehensive packet corpus.

## Reliability behavior

- On reception loss, bridge retains known dog state and tracks GPS/communication status separately.
- No position is emitted until a usable name and sane coordinates are available; forwarding stale locations is controlled by runtime options.
- Existing channel recovery and reopen logic is preserved. Search timeout `255` was tested with broadcast OFF/ON on Windows; longer soak and Linux recovery tests remain open.

## Identifiers and compatibility

ANT device numbers and Garmin UI menu IDs are **not interchangeable**. During testing, ANT device numbers `17720`, `11164`, `11183` were observed; the Garmin UI displayed `410936`, `207791`, `207772`. The pairing `207772 ↔ 11164` was confirmed; do not infer a complete mapping for the other IDs. Alpha 200i and 300i are available for testing; Astro 320 remains a future compatibility target.

## References

- [ANT Message Protocol and Usage Rev. 5.1](https://www.thisisant.com/assets/resources/ANT%20Protocol/D00000652_ANT_Message_Protocol_and_Usage_Rev_5.1.pdf)
- [OpenANT Python project](https://github.com/Tigge/openant)
- Decoder implementation: `garmin_dog_bridge.py` (source of field mappings above)

**Important:** These field assignments are based on working code and empirical captures, not a claim that Garmin publicly specifies the layout. Further controlled captures are needed for battery levels, multiple assets, and model differences.
