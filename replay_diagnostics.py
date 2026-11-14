#!/usr/bin/env python3
"""Offline, cross-platform regression checks for captured ANT diagnostic JSONL."""
import argparse
import collections
import json
from pathlib import Path


def check(path):
    events = [json.loads(line) for line in Path(path).open(encoding="utf-8") if line.strip()]
    counts = collections.Counter(x.get("event") for x in events)
    packets = [x for x in events if x.get("event") == "ant_packet"]
    assert packets, "No ANT packets in capture"
    for x in packets:
        data = bytes.fromhex(x["payload"])
        assert len(data) == 8, "Invalid ANT payload length"
        assert data[0] == int(x["page"], 16), "Page mismatch"
    assert any(x["page"] == "0x20" for x in packets), "No Garmin disconnect captured"
    tx = [x for x in events if x.get("event") == "offaro_tx"]
    assert tx, "No OffARO output in capture"
    valid = [x for x in events if x.get("event") == "position_decoded" and not x.get("gps_lost") and not x.get("comm_lost")]
    assert valid, "No valid GPS positions"
    assert any(x.get("name") == "Cooper" for x in tx), "Cooper not transmitted"
    assert not any(x.get("name") in ("Max", "Wilmer") for x in tx), "Inactive dog transmitted"
    print(f"PASS: {len(packets)} ANT packets, {len(valid)} valid decoded positions, {len(tx)} UDP events")
    print("Event counts:", dict(counts))
    print("NOTE: Replay validates capture invariants, not a live re-execution of the decoder.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", help="Path to ant-diagnostics.jsonl")
    check(parser.parse_args().capture)
