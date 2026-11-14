#!/usr/bin/env python3
"""
Garmin ANT Bridge v0.7.0 - reference release
===================================

Cross-platform Garmin Alpha/Astro "Broadcast Dog Data" receiver using OpenANT.

Receives ANT+ Asset Tracker messages and injects valid dog positions into OffARO
using its Local Position Injection JSON/UDP interface.

Field-test focus:
  - quiet normal output
  - rolling log file
  - clear Alpha/dog state transitions
  - automatic recovery/reconnect after ANT errors
  - preserve all Garmin dog telemetry in logs
  - Windows/Linux/macOS compatible Python code

Default OffARO target:
  127.0.0.1:2023
"""

import argparse
import platform
import importlib.metadata
import json
import logging
from logging.handlers import RotatingFileHandler
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from openant.easy.node import Node
from openant.easy.channel import Channel
from openant.base.message import Message

ANT_PLUS_NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]

DEVICE_TYPE = 41
MESSAGE_PERIOD = 2048
RF_FREQUENCY = 57

PAGE_LOCATION_FIRST = 0x01
PAGE_LOCATION_SECOND = 0x02
PAGE_NO_ASSETS = 0x03
PAGE_IDENTIFICATION_FIRST = 0x10
PAGE_IDENTIFICATION_SECOND = 0x11
PAGE_DISCONNECT = 0x20
PAGE_REQUEST_DATA = 0x46

STATUS = {
    0: "sitting",
    1: "moving",
    2: "pointed",
    3: "treed",
    4: "unknown",
}

@dataclass
class Dog:
    index: int
    name: Optional[str] = None
    color: Optional[int] = None
    asset_type: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    distance_m: Optional[int] = None
    bearing_deg: Optional[float] = None
    status: str = "unknown"
    low_battery: bool = False
    gps_lost: bool = False
    comm_lost: bool = False
    should_remove: bool = False
    last_rx: float = 0.0
    last_udp: float = 0.0
    last_udp_lat: Optional[float] = None
    last_udp_lon: Optional[float] = None
    last_console_state: Optional[tuple] = None
    has_seen_valid: bool = False
    last_valid_lat: Optional[float] = None
    last_valid_lon: Optional[float] = None
    last_loss_signature: Optional[tuple] = None


class GarminDogBridge:
    def __init__(self, args):
        self.args = args
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dogs: Dict[int, Dog] = {}
        self.prev_payload = None
        self.packet_count = 0
        self.udp_count = 0
        self.location_pairs = 0
        self.location_rejected = 0
        self.location_page_counts = {1: 0, 2: 0}
        self._diag_last_summary = 0.0
        self._diag_file = None
        if args.diagnostics and not args.scan:
            diag_path = Path(args.diagnostics_file)
            diag_path.parent.mkdir(parents=True, exist_ok=True)
            self._diag_file = diag_path.open("a", encoding="utf-8", buffering=1)
            print(f"[DIAG] Capture enabled: {diag_path}")
        self.started = time.monotonic()
        self.last_packet_rx = 0.0
        self.alpha_seen = False
        self.stop_requested = False
        self.session_started = 0.0
        self.watchdog_restart = False
        self.broadcast_disconnected_at = None
        self.usb_session_generation = 0
        self._search_opened_at = None
        self._search_open_stage = None
        self.disconnect_usb_generation = None
        self.channel_recovery_attempted = False
        self.channel_recovery_at = None
        self.channel_recovery_lock = threading.Lock()
        self._recovery_reopens = 0
        self._last_recovery_reopen = 0.0
        self.ant_reset_attempted = False
        self.ant_reset_at = None
        self._last_ant_event = None
        self._last_ant_status = None
        self._status_probe_lock = threading.Lock()
        self._status_probe_seq = 0
        self._last_status_probe_at = 0.0

        self.node = None
        self.channel = None
        self.detected_ids = set()
        self.sender_details = {}
        self.scan_cycle_lock = threading.Lock()
        self.scan_started = time.monotonic()
        self.scan_round = 0
        self.current_sender_id = None
        self.last_id_probe = 0.0
        self.pairing_path = Path(self.args.pairing_file)
        self.paired_id = self.load_pairing()
        self.paired_tx = self.load_pairing_tx()
        if self.args.device_id is not None:
            self.paired_id = self.args.device_id

        self._id_request_lock = threading.Lock()
        self._id_request_in_progress = False
        self._last_id_request = 0.0

        self.log = logging.getLogger("garmin-dog-bridge")
        self.configure_logging()

    def load_pairing(self):
        try:
            data = json.loads(self.pairing_path.read_text(encoding="utf-8"))
            value = int(data["ant_device_number"])
            if not 1 <= value <= 65535:
                raise ValueError("ANT device number outside 1..65535")
            return value
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise RuntimeError(f"Invalid pairing file {self.pairing_path}: {exc}") from exc

    def load_pairing_tx(self):
        try:
            data = json.loads(self.pairing_path.read_text(encoding="utf-8"))
            tx = data.get("transmission_type", 0)
            return int(tx) if 0 <= int(tx) <= 255 else 0
        except (FileNotFoundError, ValueError, TypeError, OSError):
            return 0

    @staticmethod
    def calculate_garmin_id(device_number, transmission_type):
        if transmission_type is None:
            return None
        return ((int(transmission_type) >> 4) << 16) | int(device_number)

    def save_pairing(self, device_number, garmin_menu_id=None, transmission_type=None):
        self.pairing_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.pairing_path.with_suffix(self.pairing_path.suffix + ".tmp")
        record = {"ant_device_number": device_number}
        if transmission_type is not None:
            record["transmission_type"] = int(transmission_type)
        if garmin_menu_id is not None:
            record["garmin_menu_id"] = garmin_menu_id
        tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.pairing_path)
        print(f"[PAIR] Saved Garmin ANT device number {device_number} to {self.pairing_path}")

    def read_extended_sender_id(self, data):
        # ANT extended receive: 8-byte payload + flags + optional 4-byte
        # channel ID (little-endian device number, type, transmission type).
        if len(data) >= 13 and (data[8] & 0x80):
            number = data[9] | (data[10] << 8)
            device_type = data[11] & 0x7F
            transmission_type = data[12]
            if device_type == DEVICE_TYPE and number:
                self.note_sender(number, device_type, transmission_type, "extended")
                return number
        return None

    def note_sender(self, device_number, device_type=None, transmission_type=None, source="channel-id"):
        if device_type is not None and (device_type & 0x7f) != DEVICE_TYPE:
            self.log.warning("Ignored non-asset ANT sender %d type=%s", device_number, device_type)
            return
        if device_number not in self.detected_ids:
            self.detected_ids.add(device_number)
            print(f"[SCAN] ANT device {device_number} | type={device_type if device_type is not None else '?'} | tx={transmission_type if transmission_type is not None else '?'} | source={source}")
            self.log.info("Garmin ANT sender discovered device_number=%d device_type=%s transmission_type=%s source=%s", device_number, device_type, transmission_type, source)
        previous = self.sender_details.get(device_number, {})
        self.sender_details[device_number] = {
            "device_type": device_type if device_type is not None else previous.get("device_type"),
            "transmission_type": transmission_type if transmission_type is not None else previous.get("transmission_type"),
            "source": source if device_type is not None else previous.get("source", source),
        }
        if self.current_sender_id != device_number:
            self.current_sender_id = device_number
            self.prev_payload = None
            self.dogs.clear()
        # Discovery only records IDs; selection happens after scanning.

    def request_sender_id_async(self):
        now = time.monotonic()
        if now - self.last_id_probe < 3.0 or self.channel is None:
            return
        self.last_id_probe = now
        # Requesting 0x51 may return a response depending on OpenANT/firmware.
        # We do not infer identity from an unverified response.
        def worker():
            try:
                result = self.channel.request_message(0x51)
                self.log.debug("ANT channel ID request result: %r", result)
                # Some OpenANT builds return the full 5-byte channel-ID
                # response; others only acknowledge the request.
                if isinstance(result, (bytes, bytearray, list, tuple)):
                    value = list(result)
                    if len(value) >= 5 and value[3] & 0x7F == DEVICE_TYPE:
                        self.note_sender(value[1] | value[2] << 8, value[3], value[4], "requested-channel-id")
            except Exception as exc:
                self.log.debug("ANT channel ID probe unavailable: %r", exc)
        threading.Thread(target=worker, daemon=True).start()

    def configure_logging(self):
        self.log.setLevel(logging.DEBUG)
        self.log.handlers.clear()

        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")

        log_path = Path(self.args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_path,
            maxBytes=self.args.log_max_mb * 1024 * 1024,
            backupCount=self.args.log_backups,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        self.log.addHandler(fh)

        if self.args.debug:
            sh = logging.StreamHandler()
            sh.setLevel(logging.DEBUG)
            sh.setFormatter(fmt)
            self.log.addHandler(sh)

    @staticmethod
    def hex_payload(data):
        return " ".join(f"{b:02X}" for b in data)

    @staticmethod
    def asset_index(data):
        return data[1] & 0x1F

    @staticmethod
    def signed32_le(raw4):
        return int.from_bytes(bytes(raw4), "little", signed=True)

    @staticmethod
    def semicircle_to_deg(value):
        return value * 180.0 / (2 ** 31)

    def dog_for(self, idx):
        dog = self.dogs.get(idx)
        if dog is None:
            dog = Dog(index=idx)
            self.dogs[idx] = dog
            self.log.info("New dog index discovered: idx=%02d", idx)
        return dog

    def decode_location(self, first, second):
        idx = self.asset_index(first)
        dog = self.dog_for(idx)

        dog.distance_m = first[2] | (first[3] << 8)
        dog.bearing_deg = first[4] * 360.0 / 256.0

        flags = first[5]
        dog.status = STATUS.get(flags & 0x07, f"status-{flags & 0x07}")
        dog.low_battery = bool((flags >> 3) & 1)
        dog.gps_lost = bool((flags >> 4) & 1)
        dog.comm_lost = bool((flags >> 5) & 1)
        dog.should_remove = bool((flags >> 6) & 1)

        lat_raw = [first[6], first[7], second[2], second[3]]
        lon_raw = [second[4], second[5], second[6], second[7]]
        dog.lat = self.semicircle_to_deg(self.signed32_le(lat_raw))
        dog.lon = self.semicircle_to_deg(self.signed32_le(lon_raw))
        dog.last_rx = time.monotonic()

        return dog

    def decode_identification(self, first, second):
        idx = self.asset_index(first)
        dog = self.dog_for(idx)
        dog.color = first[2]
        dog.asset_type = second[2]

        raw_name = bytes(first[3:8] + second[3:8]).rstrip(b"\x00\xff")
        name = raw_name.decode("utf-8", errors="replace").strip()
        if name and name != dog.name:
            old = dog.name
            dog.name = name
            self.log.info("Dog name learned: idx=%02d old=%r new=%r", idx, old, name)
            print(f"[DOG] idx={idx:02d} name={name}")
        return dog

    def request_identification_async(self, reason="unknown asset", force=False):
        now = time.monotonic()
        with self._id_request_lock:
            if self._id_request_in_progress:
                return
            if not force and (now - self._last_id_request) < self.args.id_request_interval:
                return
            self._id_request_in_progress = True
            self._last_id_request = now

        payload = [PAGE_REQUEST_DATA, 0xFF, 0xFF, 0xFF, 0xFF, 0x04, 0x10, 0x04]

        def worker():
            try:
                time.sleep(0.10)
                if self.channel is not None:
                    self.channel.send_acknowledged_data(payload)
                    self.log.debug("Identification request sent (%s)", reason)
            except Exception as exc:
                self.log.warning("Identification request failed: %r", exc)
            finally:
                with self._id_request_lock:
                    self._id_request_in_progress = False

        threading.Thread(target=worker, daemon=True).start()

    def coordinates_are_sane(self, lat, lon):
        return (
            lat is not None and lon is not None
            and -90 <= lat <= 90
            and -180 <= lon <= 180
        )

    def position_is_valid(self, dog):
        if not self.coordinates_are_sane(dog.lat, dog.lon):
            return False
        if dog.should_remove:
            return False
        if not self.args.send_stale and (dog.gps_lost or dog.comm_lost):
            return False
        return True

    def output_name(self, dog):
        if dog.name:
            return dog.name
        if self.args.fallback_names:
            return f"DOG-{dog.index:02d}"
        return None

    def build_offaro_comment(self, dog):
        """Build a compact, always-explicit tracker status for OffARO."""
        status = (dog.status or "unknown").capitalize()
        gps = "LOST" if dog.gps_lost else "OK"
        comm = "LOST" if dog.comm_lost else "OK"
        batt = "LOW" if dog.low_battery else "OK"
        return f"{status} - GPS:{gps} - Comm:{comm} - Battery:{batt}"

    @staticmethod
    def aprs_lat(lat):
        hemi = "N" if lat >= 0 else "S"
        value = abs(lat)
        deg = int(value)
        minutes = (value - deg) * 60.0
        return f"{deg:02d}{minutes:05.2f}{hemi}"

    @staticmethod
    def aprs_lon(lon):
        hemi = "E" if lon >= 0 else "W"
        value = abs(lon)
        deg = int(value)
        minutes = (value - deg) * 60.0
        return f"{deg:03d}{minutes:05.2f}{hemi}"

    @staticmethod
    def valid_tnc2_name(name):
        # Matches OffARO developer's stated local-injection name validation.
        import re
        return bool(re.fullmatch(r"[A-Za-z0-9-]{1,9}", name or ""))

    def make_offaro_payload(self, dog, name):
        comment = self.build_offaro_comment(dog)

        if self.args.output_format == "tnc2":
            if not self.valid_tnc2_name(name):
                # Do not silently mangle a Garmin dog name. Fall back to JSON
                # for names that cannot be represented by OffARO's TNC2 path.
                self.log.warning(
                    "Dog name %r not valid for TNC2 source; falling back to JSON",
                    name,
                )
            else:
                # APRS uncompressed position, no timestamp.
                # Primary symbol table '/' + symbol code 'p' = Rover / puppy dog.
                lat = self.aprs_lat(dog.lat)
                lon = self.aprs_lon(dog.lon)
                packet = f"{name}>APGDOG:!{lat}/{lon}p"
                if comment:
                    packet += comment
                return packet.encode("ascii", errors="replace"), "tnc2", comment

        packet = {
            "name": name,
            "lat": round(dog.lat, 7),
            "lon": round(dog.lon, 7),
            "comment": comment,
        }
        raw = json.dumps(
            packet, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return raw, "json", comment

    def send_offaro_packet(self, dog, name, lat, lon, force=False):
        """Send one OffARO packet using either TNC2/APRS or JSON."""
        now = time.monotonic()

        if not force:
            changed = (
                dog.last_udp_lat is None
                or dog.last_udp_lon is None
                or abs(lat - dog.last_udp_lat) > self.args.position_epsilon
                or abs(lon - dog.last_udp_lon) > self.args.position_epsilon
            )
            if not changed and (now - dog.last_udp) < self.args.udp_heartbeat:
                return False
            if (now - dog.last_udp) < self.args.udp_min_interval:
                return False

        orig_lat, orig_lon = dog.lat, dog.lon
        dog.lat, dog.lon = lat, lon
        try:
            raw, payload_format, comment = self.make_offaro_payload(dog, name)
        finally:
            dog.lat, dog.lon = orig_lat, orig_lon

        try:
            if self._diag_file is not None:
                self.diag_event("offaro_tx", format=payload_format, name=name,
                                lat=lat, lon=lon, payload=raw.decode("utf-8", errors="replace"),
                                destination=f"{self.args.udp_host}:{self.args.udp_port}")
                print(f"[DIAG] OffARO TX {payload_format}: {raw.decode('utf-8', errors='replace')}")
            self.sock.sendto(raw, (self.args.udp_host, self.args.udp_port))
            self.udp_count += 1
            dog.last_udp = now
            dog.last_udp_lat = lat
            dog.last_udp_lon = lon
            self.log.info(
                "UDP format=%s name=%r lat=%.7f lon=%.7f comment=%r host=%s port=%d payload=%r",
                payload_format, name, lat, lon, comment,
                self.args.udp_host, self.args.udp_port,
                raw.decode("utf-8", errors="replace")
            )
            return True
        except OSError as exc:
            self.diag_event("offaro_tx_failed", error=str(exc))
            self.log.error("UDP send failed: %r", exc)
            print(f"[WARN] OffARO UDP send failed: {exc}")
            return False

    def maybe_send_offaro(self, dog):
        if not self.args.offaro:
            if self._diag_file is not None:
                self.diag_event("offaro_disabled", idx=dog.index)
            return
        if dog.should_remove:
            if self._diag_file is not None:
                self.diag_rejection(dog, "remove flag set")
            return

        name = self.output_name(dog)
        if not name:
            if self._diag_file is not None:
                self.diag_rejection(dog, "name not yet received")
            self.request_identification_async(reason=f"name missing idx {dog.index}")
            return

        lost = dog.gps_lost or dog.comm_lost

        if not lost:
            if not self.coordinates_are_sane(dog.lat, dog.lon):
                if self._diag_file is not None:
                    self.diag_rejection(dog, "coordinates invalid or missing")
                return

            dog.has_seen_valid = True
            dog.last_valid_lat = dog.lat
            dog.last_valid_lon = dog.lon
            dog.last_loss_signature = None
            self.send_offaro_packet(dog, name, dog.lat, dog.lon, force=False)
            return

        # Do not inject dogs that were already stale when this bridge session
        # started. A valid live fix must first have been seen.
        if not dog.has_seen_valid:
            if self._diag_file is not None:
                self.diag_rejection(dog, "LOST before first valid fix")
            self.log.debug(
                "LOST suppressed idx=%02d name=%r: no valid fix seen this session",
                dog.index, dog.name
            )
            return

        loss_signature = (
            dog.gps_lost, dog.comm_lost, dog.low_battery, dog.status
        )

        # One forced update when LOST state changes, at the last valid position.
        # No heartbeat while lost, so OffARO does not keep treating stale
        # coordinates as continuously refreshed.
        if loss_signature != dog.last_loss_signature:
            dog.last_loss_signature = loss_signature
            lat, lon = dog.last_valid_lat, dog.last_valid_lon
            if not self.coordinates_are_sane(lat, lon):
                return

            comment = self.build_offaro_comment(dog)
            print(f"[DOG] {name}: LOST -> sending last valid position to OffARO")
            print(f"      OffARO comment: {comment}")
            self.send_offaro_packet(dog, name, lat, lon, force=True)

    def maybe_print_state(self, dog):
        state = (
            dog.name, dog.status, dog.gps_lost, dog.comm_lost,
            dog.low_battery, dog.should_remove
        )
        if state != dog.last_console_state:
            dog.last_console_state = state
            n = dog.name or f"idx {dog.index:02d}"
            print(
                f"[DOG] {n}: status={dog.status}, "
                f"gps={'LOST' if dog.gps_lost else 'OK'}, "
                f"link={'LOST' if dog.comm_lost else 'OK'}, "
                f"battery={'LOW' if dog.low_battery else 'OK'}"
            )

    def log_dog_telemetry(self, dog):
        self.log.debug(
            "DOG idx=%02d name=%r lat=%s lon=%s dist=%s bearing=%s "
            "status=%s lowbat=%s gpslost=%s commlost=%s remove=%s",
            dog.index, dog.name, dog.lat, dog.lon, dog.distance_m,
            dog.bearing_deg, dog.status, dog.low_battery,
            dog.gps_lost, dog.comm_lost, dog.should_remove
        )

    def diag_event(self, kind, **fields):
        if self._diag_file is None:
            return
        event = {"time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                 "event": kind, **fields}
        try:
            self._diag_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            self.log.error("Diagnostic capture write failed: %s", exc)

    def diag_rejection(self, dog, reason):
        self.location_rejected += 1
        self.diag_event("position_rejected", idx=dog.index, name=dog.name,
                        reason=reason, lat=dog.lat, lon=dog.lon,
                        gps_lost=dog.gps_lost, comm_lost=dog.comm_lost)
        # Keep every rejection in JSONL, but only report a change of reason
        # (or the first occurrence) in the live console.
        key = dog.index
        if not hasattr(self, "_last_rejection_reason"):
            self._last_rejection_reason = {}
        if self._last_rejection_reason.get(key) != reason:
            print(f"[DIAG] Position rejected idx={dog.index:02d}: {reason} (repeats in JSONL)")
            self._last_rejection_reason[key] = reason

    def on_data(self, data):
        data = list(data)
        if len(data) < 8:
            self.log.warning("Short ANT packet: %r", data)
            return
        sender_id = self.read_extended_sender_id(data)
        if sender_id is not None:
            pass  # read_extended_sender_id already recorded full identity
        elif self.paired_id is not None:
            # Hardware channel filtering still enforces the configured ID.
            sender_id = self.paired_id
        elif self.current_sender_id is not None:
            sender_id = self.current_sender_id
        elif not self.args.scan:
            self.request_sender_id_async()

        if self.args.scan:
            return
        if self.paired_id is None:
            # Fail closed: never publish a dog from an unidentified sender.
            if self.packet_count == 0:
                print("[PAIR] No Garmin selected. Run scan or specify --device-id.")
            return
        if sender_id is not None and sender_id != self.paired_id:
            self.log.warning("Ignoring sender %s (paired %s)", sender_id, self.paired_id)
            return
        data = data[:8]

        self.packet_count += 1
        self.last_packet_rx = time.monotonic()

        if not self.alpha_seen:
            self.alpha_seen = True
            print("[ANT] Garmin Alpha/Astro detected - receiving dog data")
            self.log.info("First Garmin ANT packet received")

        page = data[0]
        if page != PAGE_DISCONNECT:
            self.note_broadcast_recovered()
        idx = self.asset_index(data)
        if self._diag_file is not None:
            self.diag_event("ant_packet", count=self.packet_count, page=f"0x{page:02X}",
                            idx=idx, sender_id=sender_id, payload=self.hex_payload(data))
            if page in self.location_page_counts:
                self.location_page_counts[page] += 1
            if self.packet_count % 100 == 0:
                print(f"[DIAG] ANT RX={self.packet_count} location pages "
                      f"01={self.location_page_counts[1]} 02={self.location_page_counts[2]} "
                      f"decoded={self.location_pairs} rejected={self.location_rejected} OffARO TX={self.udp_count}")

        if self.args.raw:
            print(
                f"[RAW] #{self.packet_count:06d} "
                f"page=0x{page:02X} idx={idx:02d} {self.hex_payload(data)}"
            )
        self.log.debug(
            "RAW packet=%06d page=0x%02X idx=%02d bytes=%s",
            self.packet_count, page, idx, self.hex_payload(data)
        )

        if self.packet_count == 1:
            self.request_identification_async("first Garmin packet", force=True)

        if page == PAGE_LOCATION_SECOND and self.prev_payload is not None:
            if self.prev_payload[0] == PAGE_LOCATION_FIRST and self.asset_index(self.prev_payload) == idx:
                dog = self.decode_location(self.prev_payload, data)
                self.location_pairs += 1
                self.diag_event("position_decoded", idx=idx, name=dog.name,
                                lat=dog.lat, lon=dog.lon, gps_lost=dog.gps_lost,
                                comm_lost=dog.comm_lost, removed=dog.should_remove,
                                first=self.hex_payload(self.prev_payload), second=self.hex_payload(data))
                if self._diag_file is not None:
                    print(f"[DIAG] Position decoded idx={idx:02d} "
                          f"lat={dog.lat:.7f} lon={dog.lon:.7f} "
                          f"gps={'LOST' if dog.gps_lost else 'OK'} "
                          f"link={'LOST' if dog.comm_lost else 'OK'}")
                self.log_dog_telemetry(dog)
                self.maybe_print_state(dog)

                if dog.should_remove:
                    self.log.info("Dog removed idx=%02d name=%r", dog.index, dog.name)
                    print(f"[DOG] removed: {dog.name or dog.index}")
                    self.dogs.pop(dog.index, None)
                else:
                    if not dog.name:
                        self.request_identification_async(f"name missing idx {dog.index}")
                    self.maybe_send_offaro(dog)

        elif page == PAGE_LOCATION_SECOND and self._diag_file is not None:
            self.diag_event("unmatched_location_second", idx=idx,
                            previous=self.hex_payload(self.prev_payload) if self.prev_payload else None)

        elif page == PAGE_IDENTIFICATION_SECOND and self.prev_payload is not None:
            if self.prev_payload[0] == PAGE_IDENTIFICATION_FIRST and self.asset_index(self.prev_payload) == idx:
                dog = self.decode_identification(self.prev_payload, data)
                if self.position_is_valid(dog):
                    self.maybe_send_offaro(dog)

        elif page == PAGE_NO_ASSETS:
            self.log.info("Garmin reports no assets")
            print("[ANT] Garmin reports no active dogs")
            self.dogs.clear()

        elif page == PAGE_DISCONNECT:
            self.log.info("Garmin tracker disconnect page; retaining dog identities and last valid fixes")
            print("[ANT] Garmin broadcast disconnected - waiting for fresh data")
            # A disconnect is not a new bridge session. Keep identities and
            # last known good fixes so LOST can be reported accurately.
            # Never treat cached positions as current after a disconnect.
            for dog in self.dogs.values():
                dog.gps_lost = True
                dog.comm_lost = True
                dog.last_console_state = None
                self.maybe_send_offaro(dog)
            self.broadcast_disconnected_at = time.monotonic()
            self.disconnect_usb_generation = self.usb_session_generation
            self.channel_recovery_attempted = False
            self.channel_recovery_at = None
            self._recovery_reopens = 0
            self._last_recovery_reopen = 0.0
            self.prev_payload = None

        self.prev_payload = data

    def note_broadcast_recovered(self):
        """Report the actual USB lifecycle, not an inferred retained session."""
        if self.broadcast_disconnected_at is not None:
            gap = time.monotonic() - self.broadcast_disconnected_at
            same_usb = self.disconnect_usb_generation == self.usb_session_generation
            method = ("same USB session" if same_usb else "after USB session rebuild")
            if same_usb and self.channel_recovery_attempted:
                method += ", ANT channel reopened"
            elif same_usb:
                method += ", no channel restart"
            self.broadcast_disconnected_at = None
            self.channel_recovery_at = None
            self._recovery_reopens = 0
            self.log.info("Garmin broadcast resumed after %.1f s (%s)", gap, method)
            print(f"[ANT] Garmin broadcast resumed after {gap:.1f} s ({method})")

    def _discard_stale_channel_closed(self, node, channel_id):
        """Remove only stale close events for this channel before CLOSE_CHANNEL.

        OpenANT uses a shared event deque for the Node. Preserve unrelated events.
        """
        with node._event_cond:
            retained = [event for event in node._events
                        if not (event[0] == channel_id and
                                event[1] == Message.Code.EVENT_CHANNEL_CLOSED)]
            node._events.clear()
            node._events.extend(retained)

    def _await_channel_closed(self, node, channel_id, generation, timeout):
        """Wait for ANT EVENT_CHANNEL_CLOSED, not just CLOSE_CHANNEL ACK.

        OpenANT Channel.wait_for_event has no caller-specified deadline. Its
        underlying Node condition/deque allows a bounded, channel-specific wait.
        """
        deadline = time.monotonic() + timeout
        while not self.stop_requested and self.node is node and self.usb_session_generation == generation:
            with node._event_cond:
                for event in tuple(node._events):
                    if event[0] == channel_id and event[1] == Message.Code.EVENT_CHANNEL_CLOSED:
                        node._events.remove(event)
                        self.log.info('ANT EVENT_CHANNEL_CLOSED received channel=%s usb_generation=%s',
                                      channel_id, generation)
                        print(f'[ANT] EVENT_CHANNEL_CLOSED confirmed on USB session {generation}')
                        self.probe_channel_status('after-channel-closed')
                        return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                node._event_cond.wait(timeout=min(remaining, 0.2))
        self.log.warning('ANT EVENT_CHANNEL_CLOSED not confirmed within %.1fs channel=%s usb_generation=%s',
                         timeout, channel_id, generation)
        print(f'[ANT] EVENT_CHANNEL_CLOSED timeout ({timeout:.1f}s); USB fallback')
        return False

    def _mark_search_open(self, stage):
        """Timestamp channel opens; diagnostics only, no ANT commands."""
        self._search_opened_at = time.monotonic()
        self._search_open_stage = stage
        self.log.info('ANT-SEARCH timeline OPEN stage=%s usb_generation=%s monotonic=%.3f',
                      stage, self.usb_session_generation, self._search_opened_at)

    def _probe_low_priority_capability(self, channel, stage):
        """Report whether OpenANT exposes a low-priority timeout setter.

        Never issue undocumented low-level ANT commands in default mode.
        Experimental opt-in only calls an actual public channel method.
        """
        method = getattr(channel, 'set_low_priority_search_timeout', None)
        self.log.info('ANT-SEARCH low-priority stage=%s method_available=%s experiment=%s',
                      stage, callable(method), self.args.experimental_low_priority_timeout)
        if not self.args.experimental_low_priority_timeout:
            return
        if not callable(method):
            self.log.warning('ANT-SEARCH low-priority experiment skipped: method not available in OpenANT')
            print('[ANT-DIAG] Low-priority setter not exposed by this OpenANT version; skipped')
            return
        try:
            result = method(0)
            self.log.info('ANT-SEARCH low-priority setter returned stage=%s result=%r', stage, result)
            print('[ANT-DIAG] Experimental low-priority search timeout=0 requested')
        except Exception as exc:
            self.log.exception('ANT-SEARCH low-priority experiment failed stage=%s error=%r',stage,exc)
            print(f'[ANT-DIAG] Low-priority experiment failed: {exc}')

    def _set_search_timeout_diagnostic(self, channel, stage):
        """Configure infinite ANT channel search (0x44=255) without altering recovery policy.

        OpenANT's high-level setter may consume the ANT RESPONSE_EVENT internally.
        A successful return is NOT proof of the stick's effective timeout.
        """
        generation = self.usb_session_generation
        start = time.monotonic()
        self.log.info('ANT-TIMEOUT request stage=%s generation=%s channel=%s value=255 (0x44 infinite search)',
                      stage, generation, channel.id)
        try:
            result = channel.set_search_timeout(255)
        except Exception as exc:
            self.log.exception('ANT-TIMEOUT failed stage=%s generation=%s elapsed_ms=%.1f error=%r',
                               stage, generation, (time.monotonic()-start)*1000, exc)
            raise
        self.log.info('ANT-TIMEOUT returned stage=%s generation=%s elapsed_ms=%.1f '
                      'result=%r result_type=%s (raw ANT ACK not exposed by setter)',
                      stage, generation, (time.monotonic()-start)*1000,
                      result, type(result).__name__)
        # Observe, never consume, queued events. These are not necessarily ACKs.
        node = self.node
        if node is not None and hasattr(node, '_event_cond') and hasattr(node, '_events'):
            try:
                with node._event_cond:
                    events = tuple(node._events)[-8:]
                self.log.info('ANT-TIMEOUT event-queue snapshot stage=%s events=%r', stage, events)
            except Exception as exc:
                self.log.debug('ANT-TIMEOUT event-queue unavailable stage=%s error=%r', stage, exc)
        self._probe_low_priority_capability(channel, stage)
        return result

    def recover_ant_channel(self):
        """Close, confirm EVENT_CHANNEL_CLOSED, then reopen on the same USB Node."""
        with self.channel_recovery_lock:
            if self.stop_requested or self.node is None or self.channel is None:
                return False
            channel = self.channel
            node = self.node
            generation = self.usb_session_generation
            self.log.info('ANT event-driven recovery begin usb_generation=%d channel=%d', generation, channel.id)
            print(f'[ANT] Event-driven channel recovery on USB session {generation}')
            try:
                self._discard_stale_channel_closed(node, channel.id)
                self.log.info('ANT recovery command: CLOSE_CHANNEL channel=%d', channel.id)
                print('[ANT] Recovery: CLOSE_CHANNEL')
                channel.close()
                self.log.info('ANT CLOSE_CHANNEL command acknowledged; awaiting EVENT_CHANNEL_CLOSED')
                if not self._await_channel_closed(node, channel.id, generation,
                                                  self.args.channel_close_timeout):
                    return False
                if self.stop_requested or self.node is not node or self.usb_session_generation != generation:
                    return False
                # A closed ANT channel retains configuration by specification,
                # but reapply the initial setup to test whether stale channel
                # parameters explain the failure to reacquire Garmin broadcasts.
                # Do not create a second Node or change the USB session here.
                self.log.info('ANT recovery reconfigure begin channel=%d usb_generation=%d '
                              'period=%d rf=%d device=%d type=%d tx=%d',
                              channel.id, generation, MESSAGE_PERIOD, RF_FREQUENCY,
                              self.paired_id, DEVICE_TYPE, self.paired_tx)
                print(f'[ANT] Recovery: reconfiguring channel on USB session {generation} '
                      f'(device={self.paired_id}, type={DEVICE_TYPE}, tx={self.paired_tx}, '
                      f'rf={RF_FREQUENCY}, period={MESSAGE_PERIOD})')
                # Apply in the same order as setup_ant(). Every command is
                # acknowledged by OpenANT or raises an exception for USB fallback.
                self.log.info('ANT recovery command: SET_NETWORK_KEY network=0')
                node.set_network_key(0x00, ANT_PLUS_NETWORK_KEY)
                self.log.info('ANT recovery command: SET_CHANNEL_PERIOD period=%d', MESSAGE_PERIOD)
                channel.set_period(MESSAGE_PERIOD)
                self.log.info('ANT recovery command: SET_SEARCH_TIMEOUT timeout=0')
                self._set_search_timeout_diagnostic(channel, 'recovery-config')
                self.log.info('ANT recovery command: SET_RF_FREQ rf=%d', RF_FREQUENCY)
                channel.set_rf_freq(RF_FREQUENCY)
                self.log.info('ANT recovery command: SET_CHANNEL_ID device=%d type=%d tx=%d',
                              self.paired_id, DEVICE_TYPE, self.paired_tx)
                # Diagnostic experiment: keep device number locked in ANT
                # hardware, but allow a changed transmission type after
                # Garmin Broadcast Dog Data is toggled. Never wildcard the
                # device number: callback identity is not always extended.
                recovery_tx = (0 if self.args.recovery_wildcard_tx else self.paired_tx)
                self.log.info('ANT recovery search identity: device=%d type=%d tx=%d '
                              '(paired_tx=%s wildcard_tx=%s)',
                              self.paired_id, DEVICE_TYPE, recovery_tx,
                              self.paired_tx, self.args.recovery_wildcard_tx)
                print(f'[ANT] Recovery search: locked device={self.paired_id}, '
                      f'type={DEVICE_TYPE}, tx={recovery_tx} '
                      f'({"wildcard TX" if self.args.recovery_wildcard_tx else "exact TX"})')
                channel.set_id(self.paired_id, DEVICE_TYPE, recovery_tx)
                self.log.info('ANT recovery reconfigure complete channel=%d usb_generation=%d',
                              channel.id, generation)
                print('[ANT] Recovery: channel configuration accepted')
                self.log.info('ANT recovery command: OPEN_CHANNEL channel=%d', channel.id)
                print('[ANT] Recovery: OPEN_CHANNEL')
                channel.open()
                self._mark_search_open('recovery-config')
            except Exception as exc:
                self.log.exception('ANT event-driven recovery failed usb_generation=%d', generation)
                print(f'[ANT] Channel recovery failed: {exc}; full USB fallback')
                return False
            self.channel_recovery_at = time.monotonic()
            self._recovery_reopens = 0
            self._last_recovery_reopen = time.monotonic()
            self.log.info('ANT OPEN_CHANNEL acknowledged usb_generation=%d', generation)
            print(f'[ANT] OPEN_CHANNEL accepted on USB session {generation}; waiting for Garmin packets')
            self.probe_channel_status('after-recovery-open')
            return True

    def reopen_closed_recovery_channel(self, reason, generation):
        """Reopen a channel which returned to ASSIGNED after recovery.

        Do not CLOSE_CHANNEL again: the ANT chip already closed it.
        A powered-off Garmin is normal: retry without a fixed count limit.
        USB restart remains a fallback for actual ANT command errors.
        """
        if not self.channel_recovery_lock.acquire(blocking=False):
            return
        try:
            if (self.stop_requested or self.node is None or self.channel is None
                    or self.usb_session_generation != generation
                    or self.broadcast_disconnected_at is None
                    or self.channel_recovery_at is None):
                return
            now = time.monotonic()
            if now - self._last_recovery_reopen < 2.0:
                return
            self._last_recovery_reopen = now
            self._recovery_reopens += 1
            ch = self.channel
            self.log.warning('ANT recovery reopen #%d reason=%s generation=%d',
                             self._recovery_reopens, reason, generation)
            print(f'[ANT] Channel returned to ASSIGNED/CLOSED; reopening '
                  f'#{self._recovery_reopens} ({reason})')
            # Reapply settings in case channel-close discarded search parameters.
            ch.set_period(MESSAGE_PERIOD)
            self._set_search_timeout_diagnostic(ch, "recovery-reopen")
            ch.set_rf_freq(RF_FREQUENCY)
            ch.set_id(self.paired_id, DEVICE_TYPE,
                      0 if self.args.recovery_wildcard_tx else self.paired_tx)
            ch.open()
            self._mark_search_open('recovery-reopen')
            self.log.info('ANT recovery reopen #%d OPEN_CHANNEL acknowledged', self._recovery_reopens)
            self.probe_channel_status('after-reopen')
        except Exception as exc:
            self.log.exception('ANT recovery reopen failed: %r', exc)
            print(f'[ANT] Recovery reopen failed: {exc}; USB restart')
            self.request_full_restart('ANT recovery reopen command failed')
        finally:
            self.channel_recovery_lock.release()

    def probe_channel_status(self, label):
        """Request ANT 0x52 CHANNEL_STATUS without blocking watchdog thread.

        The OpenANT request_message return type varies by version. Log the raw
        response, and only decode a byte sequence if its shape is unambiguous.
        """
        node, channel = self.node, self.channel
        if node is None or channel is None or self.stop_requested:
            return
        if not self._status_probe_lock.acquire(blocking=False):
            self.log.info('ANT-STATUS skipped (%s): previous probe in progress', label)
            return
        generation = self.usb_session_generation
        channel_id = channel.id
        self._status_probe_seq += 1
        seq = self._status_probe_seq
        def worker():
            try:
                self.log.info('ANT-STATUS request #%d label=%s generation=%d channel=%d msg=0x52',
                              seq, label, generation, channel_id)
                print(f'[ANT-STATUS] Request #{seq} ({label}), USB session {generation}')
                result = channel.request_message(0x52)
                if self.node is not node or self.usb_session_generation != generation:
                    self.log.info('ANT-STATUS stale response #%d ignored after USB change: %r', seq, result)
                    return
                self.log.info('ANT-STATUS response #%d label=%s raw=%r type=%s',
                              seq, label, result, type(result).__name__)
                print(f'[ANT-STATUS] #{seq} {label}: raw={result!r}')
                # OpenANT 1.3.4 returns (channel, 0x52, array('B', [status])).
                # The status byte low two bits encode channel state.
                if (isinstance(result, tuple) and len(result) == 3
                        and result[0] == channel_id and result[1] == 0x52
                        and len(result[2]) == 1):
                    status = int(result[2][0])
                    state = {0: 'UNASSIGNED', 1: 'ASSIGNED', 2: 'SEARCHING',
                             3: 'TRACKING'}.get(status & 3, 'UNKNOWN')
                    self.log.info('ANT-STATUS decoded #%d state=%s raw=0x%02X', seq, state, status)
                    print(f'[ANT-STATUS] #{seq}: {state} (0x{status:02X})')
                    if (state == 'ASSIGNED' and self.channel_recovery_at is not None
                            and self.broadcast_disconnected_at is not None):
                        self.reopen_closed_recovery_channel('status-assigned', generation)

                if isinstance(result, (bytes, bytearray, list, tuple)):
                    b = list(result)
                    # ANT channel-status payload is channel-number, status byte.
                    # Some OpenANT versions return the complete ANT message.
                    if len(b) == 2 and all(isinstance(x, int) for x in b) and b[0] == channel_id:
                        status = b[1]
                        state = {0:'UNASSIGNED', 1:'ASSIGNED', 2:'SEARCHING', 3:'TRACKING'}.get(status & 3, 'UNKNOWN')
                        self.log.info('ANT-STATUS decoded #%d channel=%d state=%s status=0x%02X network=%d typebits=%d',
                                      seq, channel_id, state, status, (status >> 2) & 3, (status >> 4) & 3)
                        print(f'[ANT-STATUS] #{seq}: {state} (0x{status:02X})')
            except Exception as exc:
                self.log.warning('ANT-STATUS request #%d label=%s failed: %r', seq, label, exc)
                print(f'[ANT-STATUS] #{seq} {label}: request unavailable/failed: {exc}')
            finally:
                self._status_probe_lock.release()
        threading.Thread(target=worker, name=f'ant-status-{seq}', daemon=True).start()

    def _ant_event_monitor(self, node, generation):
        """Observe ANT events without consuming OpenANT's event deque.

        Event deque is also used by _await_channel_closed; do not remove items.
        """
        seen = set()
        while not self.stop_requested and self.node is node and self.usb_session_generation == generation:
            try:
                with node._event_cond:
                    snapshot = tuple(node._events)
                for event in snapshot:
                    key = (repr(event), generation)
                    if key not in seen:
                        seen.add(key)
                        code = event[1] if len(event) > 1 else None
                        # OpenANT Node event tuples: (channel, event_code, payload).
                        names = {0x01:'RX_SEARCH_TIMEOUT', 0x02:'RX_FAIL',
                                 0x03:'TX', 0x04:'TRANSFER_RX_FAILED',
                                 0x05:'TRANSFER_TX_COMPLETED', 0x06:'TRANSFER_TX_FAILED',
                                 0x07:'CHANNEL_CLOSED', 0x08:'RX_FAIL_GO_TO_SEARCH',
                                 0x09:'CHANNEL_COLLISION', 0x0A:'TRANSFER_TX_START',
                                 0x15:'RX_FAIL_GO_TO_SEARCH (alternate)'}
                        name = names.get(code, f'UNKNOWN_0x{code:02X}' if isinstance(code, int) else 'UNKNOWN')
                        elapsed = (time.monotonic() - self._search_opened_at
                                   if self._search_opened_at is not None else None)
                        self.log.info('ANT-SEARCH timeline EVENT name=%s since_open_s=%s open_stage=%s usb_generation=%s',
                                      name, '%.3f' % elapsed if elapsed is not None else 'unknown',
                                      self._search_open_stage, generation)
                        self.log.info('ANT event observed usb_generation=%d name=%s event=%r', generation, name, event)
                        print(f'[ANT-EVENT] session={generation} {name} {event!r}')
                        if (code == 0x07 and self.channel_recovery_at is not None
                                and self.broadcast_disconnected_at is not None):
                            threading.Thread(
                                target=self.reopen_closed_recovery_channel,
                                args=('event-channel-closed', generation),
                                name='ant-recovery-reopen', daemon=True).start()

                if len(seen) > 2048:
                    seen.clear()
            except Exception as exc:
                self.log.debug('ANT event observer unavailable: %r', exc)
                return
            time.sleep(0.1)

    def attempt_ant_reset(self):
        """Experimental reset on same USB Node, followed by channel recreation.

        A reset invalidates channel configuration. Only use when explicitly
        enabled; if API is missing or fails, normal USB fallback remains.
        """
        with self.channel_recovery_lock:
            node = self.node
            generation = self.usb_session_generation
            if self.stop_requested or node is None:
                return False
            ant = getattr(node, '_ant', None)
            reset = getattr(ant, 'reset_system', None)
            if not callable(reset):
                self.log.warning('ANT reset unavailable: Node._ant.reset_system not callable')
                print('[ANT] Same-USB reset unavailable in this OpenANT build; USB fallback')
                return False
            try:
                print(f'[ANT] Experimental RESET_SYSTEM on USB session {generation}')
                self.log.warning('ANT RESET_SYSTEM requested usb_generation=%d', generation)
                reset()
                time.sleep(1.0)
                if self.node is not node or self.stop_requested:
                    return False
                # Reset clears network and channel configuration. Rebuild
                # receiver state on the same Node; don't open a second USB handle.
                node.set_network_key(0, ANT_PLUS_NETWORK_KEY)
                ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
                ch.on_broadcast_data = self.on_data
                ch.on_burst_data = self.on_data
                ch.on_acknowledge_data = self.on_data
                ch.set_period(MESSAGE_PERIOD)
                self._set_search_timeout_diagnostic(ch, "experimental-reset")
                ch.set_rf_freq(RF_FREQUENCY)
                ch.set_id(self.paired_id, DEVICE_TYPE, 0)
                ch.open()
                self.channel = ch
                self.ant_reset_at = time.monotonic()
                print(f'[ANT] Same-USB reset and receiver reinitialization accepted (session {generation})')
                return True
            except Exception as exc:
                self.log.exception('ANT same-USB reset failed')
                print(f'[ANT] Same-USB reset failed: {exc}; USB fallback')
                return False

    def request_full_restart(self, reason):
        self.watchdog_restart = True
        self.log.warning("ANT watchdog full USB restart requested: %s", reason)
        print(f"[ANT] Full USB session restart: {reason}")
        try:
            self.node.stop()
        except Exception as exc:
            self.log.debug("Watchdog node.stop() returned: %r", exc)

    def watchdog_loop(self, watched_node):
        """Cycle scan channels or restart normal tracking after silence."""
        while not self.stop_requested and self.node is watched_node:
            time.sleep(0.25)
            if self.node is not watched_node:
                return
            if self.args.stop_file and Path(self.args.stop_file).exists():
                print("[PROCESS] Graceful stop requested by GUI")
                self.stop_requested = True
                try:
                    self.node.stop()
                except Exception as exc:
                    self.log.debug("Stop request node.stop: %r", exc)
                return
            now = time.monotonic()
            if self.args.scan:
                if now - self.scan_started >= self.args.scan_duration:
                    self.stop_requested = True
                    try:
                        self.node.stop()
                    except Exception:
                        pass
                    return
                if now - self.session_started >= self.args.scan_dwell:
                    self.session_started = now
                    print(f"[SCAN] Listening: {len(self.detected_ids)} unique sender(s) so far")
                continue

            if self.alpha_seen and self.last_packet_rx:
                silent_for = now - self.last_packet_rx
                # An explicit Garmin disconnect is not a USB failure. Keep the
                # receiver open for a grace period to allow natural resumption.
                threshold = (self.args.disconnect_grace if self.broadcast_disconnected_at
                             is not None else self.args.ant_silence_timeout)
                if self.broadcast_disconnected_at is not None:
                    elapsed = now - self.broadcast_disconnected_at
                    if not self.channel_recovery_attempted and elapsed >= self.args.channel_recovery_delay:
                        self.channel_recovery_attempted = True
                        if not self.recover_ant_channel():
                            self.request_full_restart("ANT channel-only recovery failed")
                            return
                    if self.channel_recovery_at is not None:
                        if self.args.channel_status_diagnostics and now - self._last_status_probe_at >= self.args.channel_status_interval:
                            self._last_status_probe_at = now
                            self.probe_channel_status('recovery-wait')
                        # Explicit Garmin disconnect can last minutes or hours.
                        # Never interpret the elapsed OFF time alone as USB failure.
                        # A nonzero optional timeout is retained for diagnostics only.
                        if (self.args.channel_recovery_timeout > 0
                                and now - self.channel_recovery_at >= self.args.channel_recovery_timeout):
                            self.request_full_restart("optional recovery timeout reached")
                            return
                    elif elapsed >= self.args.disconnect_grace:
                        self.request_full_restart("disconnect grace expired without channel recovery")
                        return
                elif silent_for >= threshold:
                    self.request_full_restart(f"no Garmin packets for {silent_for:.1f} s")
                    return

            elif not self.alpha_seen and self.session_started:
                waiting_for = now - self.session_started
                if waiting_for >= self.args.initial_search_restart:
                    self.watchdog_restart = True
                    self.log.warning(
                        "ANT watchdog: no first Garmin packet after %.1f s; restarting search",
                        waiting_for,
                    )
                    print(
                        f"[ANT] no Garmin found after {waiting_for:.0f} s - "
                        "restarting ANT search..."
                    )
                    try:
                        self.node.stop()
                    except Exception as exc:
                        self.log.debug("Initial-search node.stop() returned: %r", exc)
                    return

    def setup_ant(self):
        self.log.info("USB session opening (new OpenANT Node)")
        print("[USB] Opening OpenANT USB session...")
        self.log.info("USB diagnostic platform=%s python=%s", platform.platform(), sys.version.split()[0])
        try:
            self.log.info("USB diagnostic packages openant=%s pyusb=%s",
                          importlib.metadata.version("openant"),
                          importlib.metadata.version("pyusb"))
        except importlib.metadata.PackageNotFoundError:
            self.log.info("USB diagnostic: package version unavailable")
        self.node = Node()
        self.usb_session_generation += 1
        self.log.info("USB session opened generation=%d", self.usb_session_generation)
        print(f"[USB] Session opened: generation {self.usb_session_generation}")
        self.log.info("USB diagnostic Node=%s; driver=%s; driver_backend=%s",
                      type(self.node).__name__,
                      type(getattr(self.node, "_driver", None)).__name__,
                      type(getattr(getattr(self.node, "_driver", None), "_backend", None)).__name__)
        self.node.set_network_key(0x00, ANT_PLUS_NETWORK_KEY)
        self.channel = self.node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)

        self.channel.on_broadcast_data = self.on_data
        self.channel.on_burst_data = self.on_data
        self.channel.on_acknowledge_data = self.on_data

        if not self.args.scan:
            self.channel.set_period(MESSAGE_PERIOD)
            self._set_search_timeout_diagnostic(self.channel, "initial-setup")
        self.channel.set_rf_freq(RF_FREQUENCY)
        # Device-number filtering happens inside the ANT USB stick itself.
        # Use complete ANT identity for locked tracking when available.
        self.channel.set_id(0 if self.args.scan else self.paired_id, DEVICE_TYPE,
                            0 if self.args.scan else self.paired_tx)
        self.log.info("ANT initial channel identity device=%s type=%s tx=%s rf=%s period=%s",
                      0 if self.args.scan else self.paired_id, DEVICE_TYPE,
                      0 if self.args.scan else self.paired_tx, RF_FREQUENCY,
                      None if self.args.scan else MESSAGE_PERIOD)
        if self.args.scan:
            try:
                self.channel.enable_extended_messages(True)
            except Exception as exc:
                self.log.warning("Extended ANT messages unavailable: %r", exc)
                print("[SCAN] Extended ID unavailable; trying channel-ID requests")
        if self.args.scan:
            # ANT 0x5B: receive from multiple matching transmitters on channel 0.
            # This is intentionally a scan-only operation, not a tracking mode.
            scan_open = getattr(self.channel, "open_rx_scan_mode", None)
            if not callable(scan_open):
                raise RuntimeError("OpenANT does not expose open_rx_scan_mode(); upgrade OpenANT. No pairing changed.")
            scan_open()
            print("[SCAN] Continuous RX scan enabled (ANT 0x5B); USB stays open")
            self.log.info("ANT continuous RX scan enabled")
        else:
            self.channel.open()
            self._mark_search_open('initial-or-scan')
        self.log.info("ANT channel opened usb_generation=%d", self.usb_session_generation)

    def teardown_ant(self):
        try:
            if self.node is not None:
                self.log.info("USB session teardown requested generation=%d", self.usb_session_generation)
                print(f"[USB] Closing OpenANT session generation {self.usb_session_generation}")
                self.node.stop()
        except Exception:
            pass
        self.node = None
        self.channel = None

    def run_once(self):
        self.alpha_seen = False
        self.last_packet_rx = 0.0
        self.prev_payload = None
        self.current_sender_id = None
        self._last_rejection_reason = {}
        self.watchdog_restart = False
        self.channel_recovery_attempted = False
        self.channel_recovery_at = None
        self.ant_reset_attempted = False
        self.ant_reset_at = None
        self._last_status_probe_at = time.monotonic()
        self.session_started = time.monotonic()
        if self.args.scan:
            self.scan_round += 1
            print(f"[SCAN] Round {self.scan_round}, discovered {len(self.detected_ids)} unique sender(s)")

        if not self.args.scan and self.paired_id is None:
            raise RuntimeError("No paired Garmin. Run scan first or pass --device-id 12345")
        self.setup_ant()
        print("[ANT] USB stick ready - waiting for Broadcast Dog Data...")
        self.log.info("ANT channel opened")

        observer = threading.Thread(target=self._ant_event_monitor,
                                    args=(self.node, self.usb_session_generation), daemon=True)
        if self.args.ant_event_monitor:
            observer.start()
        watchdog = threading.Thread(target=self.watchdog_loop, args=(self.node,), daemon=True)
        watchdog.start()
        try:
            self.node.start()
        finally:
            # Do not let an old watchdog act on a newly created USB session.
            watchdog.join(timeout=1.0)

        if self.watchdog_restart and not self.stop_requested:
            raise RuntimeError("ANT watchdog restart")

    def choose_scanned_sender(self):
        ids = sorted(self.detected_ids)
        print("\nGarmin ANT senders discovered:")
        if not ids:
            print("  None. Check Broadcast Dog Data and try a longer scan.")
            return
        for i, sender in enumerate(ids, 1):
            d = self.sender_details.get(sender, {})
            tx = d.get("transmission_type")
            garmin_id = self.calculate_garmin_id(sender, tx)
            print(f"  {i}. Garmin ID {garmin_id if garmin_id is not None else '?'} | ANT ID {sender} | type={d.get('device_type') or '?'} | tx={tx if tx is not None else '?'}" + (" [currently paired]" if sender == self.paired_id else ""))
        print("Garmin menu ID candidate = ((TX >> 4) * 65536) + ANT ID (experimental).")
        print("These are reference observations, NOT automatically discovered Garmin IDs.")
        print("Only select a sender you have identified as your own.")
        try:
            choice = input("Select number to lock, or Enter to keep existing pairing: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nPairing unchanged.")
            return
        if not choice:
            print("Pairing unchanged.")
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(ids):
            print("Invalid selection. Pairing unchanged.")
            return
        sender = ids[int(choice) - 1]
        try:
            menu_id = input("Garmin menu ID for this unit (optional; Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            menu_id = ""
        if menu_id:
            if not menu_id.isdecimal() or int(menu_id) % 65536 != sender:
                print("[PAIR] Garmin menu ID does not match ANT lower 16 bits. Pairing unchanged.")
                return
            self.save_pairing(sender, int(menu_id), self.sender_details.get(sender, {}).get("transmission_type"))
        else:
            self.save_pairing(sender, transmission_type=self.sender_details.get(sender, {}).get("transmission_type"))
        self.paired_id = sender
        print(f"[PAIR] Locked to {sender}. Run run_windows_field.bat to track.")

    def run(self):
        print()
        print("Garmin ANT Bridge for OffARO v0.7.0")
        print("==================================")
        print(f"OffARO : {self.args.udp_host}:{self.args.udp_port} ({self.args.output_format.upper()})")
        print(f"Log    : {self.args.log_file}")
        print()
        print(f"Garmin : {'SCAN ONLY' if self.args.scan else self.paired_id or 'NOT PAIRED'}")
        if self.args.scan:
            print(f"SCAN MODE: continuous RX for {self.args.scan_duration:.0f}s, progress every {self.args.scan_dwell:.0f}s. OffARO disabled.")
        print("Enable OffARO Local Position Injection and Garmin Broadcast Dog Data.")
        print("Ctrl+C stops the bridge.")
        print()

        retry = 0
        while not self.stop_requested:
            try:
                self.run_once()
                retry = 0
            except KeyboardInterrupt:
                self.stop_requested = True
                break
            except Exception as exc:
                self.teardown_ant()

                if self.stop_requested:
                    break

                if self.watchdog_restart:
                    retry = 0
                    self.log.info("Rebuilding ANT session after watchdog restart")
                    time.sleep(self.args.watchdog_restart_delay)
                    continue

                retry += 1
                self.log.exception("ANT session failed")
                print(f"[ANT] connection error: {exc}")

                delay = min(
                    self.args.reconnect_max,
                    self.args.reconnect_initial * (2 ** min(retry - 1, 4))
                )
                print(f"[ANT] retrying in {delay:.0f} s...")
                if self.args.stop_file and Path(self.args.stop_file).exists():
                    self.stop_requested = True
                    break
                time.sleep(delay)

        self.teardown_ant()
        if self.args.scan:
            if self.args.scan_json:
                results = []
                for number in sorted(self.detected_ids):
                    detail = self.sender_details.get(number, {})
                    tx = detail.get("transmission_type")
                    results.append({"ant_device_number": number,
                                    "device_type": detail.get("device_type"),
                                    "transmission_type": tx,
                                    "garmin_menu_id": self.calculate_garmin_id(number, tx)})
                Path(self.args.scan_json).write_text(
                    json.dumps(results, indent=2) + "\n", encoding="utf-8")
                print(f"[SCAN] Results saved: {self.args.scan_json}")
            else:
                self.choose_scanned_sender()
        self.sock.close()

        elapsed = max(time.monotonic() - self.started, 0.001)
        print()
        print(f"Stopped. ANT RX={self.packet_count}, OffARO TX={self.udp_count}, runtime={elapsed/60:.1f} min")
        print(f"Log saved to: {self.args.log_file}")
        self.log.info(
            "Stopped ANT_RX=%d UDP_TX=%d runtime_sec=%.1f",
            self.packet_count, self.udp_count, elapsed
        )


def parse_args():
    p = argparse.ArgumentParser(
        description="Garmin Alpha/Astro ANT+ Broadcast Dog Data -> OffARO bridge"
    )

    p.add_argument("--scan", action="store_true", help="Discover sender ID; never send to OffARO")
    p.add_argument("--stop-file", default=None, help="GUI graceful-stop request file")
    p.add_argument("--scan-json", help="In scan mode, save results as JSON without interactive prompt")
    p.add_argument("--scan-duration", type=float, default=75, help="Scan time in seconds (default 75)")
    p.add_argument("--scan-dwell", type=float, default=15, help="Progress interval in seconds during continuous scan")
    p.add_argument("--device-id", type=int, help="Use only this ANT device number (1..65535)")
    p.add_argument("--pairing-file", default="garmin_pairing.json", help="Persistent ANT pairing file")
    p.add_argument("--udp-host", default="127.0.0.1")
    p.add_argument("--udp-port", type=int, default=2023)
    p.add_argument(
        "--output-format",
        choices=("json", "tnc2"),
        default="tnc2",
        help="OffARO injection format. tnc2 gives APRS Dog icon /p (default).",
    )

    p.add_argument("--no-offaro", dest="offaro", action="store_false")
    p.set_defaults(offaro=True)

    p.add_argument("--fallback-names", action="store_true")
    p.add_argument("--send-stale", action="store_true")
    p.add_argument("--raw", action="store_true")
    p.add_argument('--experimental-low-priority-timeout', action='store_true',
                   help='Opt-in: call OpenANT low-priority timeout setter if available (default: OFF)')
    p.add_argument("--diagnostics", action="store_true", help="Capture all ANT pages and decode decisions to JSONL")
    p.add_argument("--diagnostics-file", default="logs/ant-diagnostics.jsonl")
    p.add_argument("--debug", action="store_true")

    p.add_argument("--udp-min-interval", type=float, default=0.75)
    p.add_argument("--udp-heartbeat", type=float, default=2.5)
    p.add_argument("--position-epsilon", type=float, default=0.000001)
    p.add_argument("--id-request-interval", type=float, default=2.0)

    p.add_argument("--ant-event-monitor", action="store_true", default=True,
                   help="Log ANT channel events without consuming event queue (default)")
    p.add_argument("--no-ant-event-monitor", action="store_false", dest="ant_event_monitor")
    p.add_argument("--experimental-ant-reset", action="store_true",
                   help="EXPERIMENTAL: reset ANT chip on same USB session after failed channel recovery")
    p.add_argument("--ant-reset-timeout", type=float, default=12.0,
                   help="Seconds to await Garmin packets after experimental reset")
    p.add_argument("--recovery-wildcard-tx", action="store_true", default=True,
                   help="Diagnostic: search paired device with wildcard transmission type after disconnect (default)")
    p.add_argument("--recovery-exact-tx", action="store_false", dest="recovery_wildcard_tx",
                   help="Control test: keep exact transmission type during channel recovery")
    p.add_argument('--channel-status-diagnostics', action='store_true', default=True,
                   help='Request ANT channel status (0x52) during recovery (default)')
    p.add_argument('--no-channel-status-diagnostics', action='store_false', dest='channel_status_diagnostics')
    p.add_argument('--channel-status-interval', type=float, default=8.0,
                   help='Seconds between ANT channel-status probes during recovery (default 8)')
    p.add_argument('--max-recovery-reopens', type=int, default=3,
                   help='Legacy diagnostic option; ignored in persistent recovery mode')
    p.add_argument("--channel-recovery-delay", type=float, default=3.0,
                   help="Attempt ANT channel-only recovery this many seconds after disconnect (default 3)")
    p.add_argument("--channel-close-timeout", type=float, default=3.0,
                   help="Wait for EVENT_CHANNEL_CLOSED, seconds (default 3)")
    p.add_argument("--channel-recovery-timeout", type=float, default=0.0,
                   help="Optional USB restart timeout after explicit disconnect (0 disables; default 0)")
    p.add_argument("--disconnect-grace", type=float, default=30.0,
                   help="Keep ANT USB open this long after explicit Garmin disconnect (default 30 s)")
    p.add_argument("--reconnect-initial", type=float, default=2.0)
    p.add_argument("--reconnect-max", type=float, default=30.0)

    p.add_argument(
        "--ant-silence-timeout",
        type=float,
        default=8.0,
        help="Restart ANT search after this many seconds without Garmin packets (default 8).",
    )
    p.add_argument(
        "--initial-search-restart",
        type=float,
        default=30.0,
        help="Restart ANT search if no Garmin is found after this many seconds (default 30).",
    )
    p.add_argument(
        "--watchdog-restart-delay",
        type=float,
        default=1.0,
        help="Delay before rebuilding ANT session after silent loss (default 1).",
    )

    p.add_argument("--log-file", default="logs/garmin-dog-bridge.log")
    p.add_argument("--log-max-mb", type=int, default=10)
    p.add_argument("--log-backups", type=int, default=3)

    args = p.parse_args()
    if args.device_id is not None and not 1 <= args.device_id <= 65535:
        p.error("--device-id must be 1..65535")
    if args.max_recovery_reopens < 1:
        p.error("--max-recovery-reopens must be >= 1")
    if args.channel_status_interval < 2:
        p.error("--channel-status-interval must be >=2 seconds")
    if args.channel_close_timeout < 0.2 or args.channel_close_timeout > 5:
        p.error("--channel-close-timeout must be between 0.2 and 5 seconds")
    if args.channel_recovery_delay < 0.5 or args.channel_recovery_timeout < 0:
        p.error("--channel-recovery-delay must be >=0.5 and --channel-recovery-timeout >=0")
    if args.disconnect_grace < args.ant_silence_timeout:
        p.error("--disconnect-grace must be >= --ant-silence-timeout")
    if args.scan_duration < 5 or args.scan_dwell < 2:
        p.error("--scan-duration must be >=5 and --scan-dwell >=2")

    if args.debug:
        logging.getLogger("openant").setLevel(logging.DEBUG)

    return args


def main():
    args = parse_args()
    GarminDogBridge(args).run()


if __name__ == "__main__":
    main()
