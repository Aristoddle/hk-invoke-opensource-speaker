#!/usr/bin/env python3
"""List host CoreAudio devices relevant to the Invoke voice-satellite prototype."""
from __future__ import annotations

import argparse
import sys
from typing import Any

import sounddevice as sd


def device_name(device: dict[str, Any]) -> str:
    return str(device.get("name", ""))


def matches(name: str, needle: str) -> bool:
    return needle.lower() in name.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-input", help="substring that must match an input device")
    parser.add_argument("--require-output", help="substring that must match an output device")
    args = parser.parse_args()

    devices = sd.query_devices()
    default_input, default_output = sd.default.device
    print(f"default: input={default_input} output={default_output}")
    print()

    found_required_input = args.require_input is None
    found_required_output = args.require_output is None

    print("input devices:")
    for idx, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        name = device_name(dev)
        marker = "*" if idx == default_input else " "
        if args.require_input and matches(name, args.require_input):
            found_required_input = True
            marker = "!"
        print(
            f"{marker} {idx:2d} {name} | "
            f"in={int(dev.get('max_input_channels', 0))} "
            f"rate={int(float(dev.get('default_samplerate', 0)))}"
        )

    print()
    print("output devices:")
    for idx, dev in enumerate(devices):
        if int(dev.get("max_output_channels", 0)) <= 0:
            continue
        name = device_name(dev)
        marker = "*" if idx == default_output else " "
        if args.require_output and matches(name, args.require_output):
            found_required_output = True
            marker = "!"
        print(
            f"{marker} {idx:2d} {name} | "
            f"out={int(dev.get('max_output_channels', 0))} "
            f"rate={int(float(dev.get('default_samplerate', 0)))}"
        )

    missing: list[str] = []
    if not found_required_input:
        missing.append(f"input matching {args.require_input!r}")
    if not found_required_output:
        missing.append(f"output matching {args.require_output!r}")
    if missing:
        print(f"ERROR: missing required {' and '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
