#!/usr/bin/env python3
"""Capture read-only inventory from an HK Invoke no-NAND USB ACM root shell.

This is a deliberate serial-open helper, not a passive host probe. It only opens
an already-exposed serial device, sends read-only shell commands, and writes
host-side transcripts plus summaries under ~/.local/state/hk-invoke/sessions.
The device-side commands read /proc, /sys, /dev, and RAM-only /tmp helpers from
the generated no-NAND initramfs. They do not load modules, start Wi-Fi, start
USB networking, invoke ADB, or write persistent storage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import select
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Any

HOME = Path.home()
DEFAULT_SESSION_ROOT = HOME / ".local/state/hk-invoke/sessions"
DEFAULT_PORT_PATTERNS = [
    "/dev/cu.usbmodemno_nand_probe*",
    "/dev/cu.usbmodem*",
    "/dev/cu.usbserial*",
]

BEGIN_MARKER = "__JOE_NO_NAND_SERIAL_BEGIN__"
END_MARKER = "__JOE_NO_NAND_SERIAL_END__"

READ_ONLY_COMMANDS = [
    "bb=/bin/busybox; [ -x \"$bb\" ] || bb=busybox",
    f"echo {BEGIN_MARKER}",
    "$bb id || id || true",
    "$bb uname -a || uname -a || true",
    "$bb cat /tmp/NO_NAND_PROBE_READY 2>/dev/null || true",
    "$bb cat /proc/cmdline 2>/dev/null || true",
    "$bb cat /proc/mtd 2>/dev/null || true",
    "$bb mount 2>/dev/null || mount 2>/dev/null || true",
    "$bb cat /proc/partitions 2>/dev/null || true",
    "$bb cat /proc/net/dev 2>/dev/null || true",
    "$bb ls -la /dev/snd 2>/dev/null || true",
    "$bb ls -la /sys/class/sound 2>/dev/null || true",
    "$bb cat /proc/asound/cards 2>/dev/null || true",
    "$bb cat /proc/asound/pcm 2>/dev/null || true",
    "$bb sh /no-nand-inventory.sh 2>&1 || true",
    "$bb cat /tmp/no-nand-hardware-inventory.txt 2>/dev/null || true",
    "$bb sh /no-nand-audio-inventory.sh 2>&1 || true",
    "$bb cat /tmp/no-nand-audio-inventory.txt 2>/dev/null || true",
    "$bb sh /no-nand-network-inventory.sh 2>&1 || true",
    "$bb cat /tmp/no-nand-network-inventory.txt 2>/dev/null || true",
    "$bb ps w 2>/dev/null || $bb ps 2>/dev/null || ps 2>/dev/null || true",
    f"echo {END_MARKER}",
]


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def safety_profile() -> dict[str, Any]:
    return {
        "classification": "deliberate-serial-open-read-only-inventory",
        "opens_serial": True,
        "sends_usb_boot_payloads": False,
        "runs_adb": False,
        "loads_modules": False,
        "starts_daemons": False,
        "wifi_or_network_changes": False,
        "writes_device_storage": False,
        "persistent_storage_changes_allowed": False,
        "device_write_scope": "read-only shell commands plus RAM-only /tmp inventory files",
        "default_session_root": str(DEFAULT_SESSION_ROOT),
        "default_port_patterns": DEFAULT_PORT_PATTERNS,
        "begin_marker": BEGIN_MARKER,
        "end_marker": END_MARKER,
        "command_count": len(READ_ONLY_COMMANDS),
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def allocate_session_dir(session_root: Path) -> Path:
    session_root.mkdir(parents=True, exist_ok=True)
    base = session_root / f"{utc_stamp()}-no-nand-serial-inventory"
    if not base.exists():
        return base
    suffix = 1
    while True:
        candidate = session_root / f"{base.name}-{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def discover_port() -> str | None:
    matches: list[str] = []
    for pattern in DEFAULT_PORT_PATTERNS:
        matches.extend(glob.glob(pattern))
    unique = sorted(dict.fromkeys(matches))
    return unique[0] if unique else None


def set_baud(attrs: list[Any], baud: int) -> None:
    speed = getattr(termios, f"B{baud}", None)
    if speed is not None:
        attrs[4] = speed
        attrs[5] = speed


def configure_serial(fd: int, baud: int) -> list[Any]:
    old_attrs = termios.tcgetattr(fd)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    set_baud(attrs, baud)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    os.set_blocking(fd, False)
    return old_attrs


def restore_serial(fd: int, old_attrs: list[Any]) -> None:
    try:
        termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
    except termios.error:
        pass


def read_available(fd: int, duration: float) -> bytes:
    deadline = time.monotonic() + max(0.0, duration)
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], min(0.25, remaining))
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if data:
            chunks.append(data)
    return b"".join(chunks)


def read_until_marker(fd: int, marker: bytes, timeout: float) -> tuple[bytes, bool]:
    deadline = time.monotonic() + max(0.0, timeout)
    chunks: list[bytes] = []
    transcript = b""
    seen = False
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], min(0.5, remaining))
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if not data:
            continue
        chunks.append(data)
        transcript += data
        if marker in transcript:
            seen = True
            # Collect a short trailing echo/prompt without extending the run.
            chunks.append(read_available(fd, 0.5))
            break
    return b"".join(chunks), seen


def write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        _, writable, _ = select.select([], [fd], [], 2.0)
        if not writable:
            raise TimeoutError("serial port did not become writable")
        offset += os.write(fd, payload[offset:])


def command_script() -> str:
    return "\n" + "\n".join(READ_ONLY_COMMANDS) + "\n"


def summarize_transcript(transcript: str, port: str, session_dir: Path) -> dict[str, Any]:
    return {
        **safety_profile(),
        "port": port,
        "session_dir": str(session_dir),
        "transcript_bytes": len(transcript.encode(errors="replace")),
        "begin_marker_observed": BEGIN_MARKER in transcript,
        "end_marker_observed": END_MARKER in transcript,
        "ready_file_observed": "No-NAND probe init reached userspace" in transcript,
        "root_identity_observed": "uid=0(root)" in transcript,
        "stock_rcs_not_run_observed": "Stock rcS was not run" in transcript,
        "hardware_inventory_observed": "## storage-map-read-only" in transcript,
        "network_inventory_observed": "## kernel-network-state" in transcript,
        "audio_inventory_observed": "## kernel-audio-surfaces" in transcript,
    }


def write_session_artifacts(
    session_dir: Path,
    port: str,
    args: argparse.Namespace,
    command_text: str,
    transcript: str,
    summary: dict[str, Any],
) -> None:
    session_dir.mkdir(parents=True, exist_ok=False)
    (session_dir / "command.json").write_text(
        json.dumps(
            {
                **safety_profile(),
                "port": port,
                "baud": args.baud,
                "read_timeout_seconds": args.read_timeout,
                "initial_read_seconds": args.initial_read_seconds,
                "commands": READ_ONLY_COMMANDS,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (session_dir / "commands.sh").write_text(command_text)
    (session_dir / "serial-transcript.txt").write_text(transcript)
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    (session_dir / "summary.md").write_text(
        "\n".join(
            [
                "# HK Invoke no-NAND serial inventory",
                "",
                f"- Classification: `{summary['classification']}`",
                f"- Port: `{port}`",
                f"- End marker observed: `{summary['end_marker_observed']}`",
                f"- Ready file observed: `{summary['ready_file_observed']}`",
                f"- Root identity observed: `{summary['root_identity_observed']}`",
                f"- Transcript: `{session_dir / 'serial-transcript.txt'}`",
                "",
                "Safety: deliberate serial open, read-only commands, no ADB, no module load, no Wi-Fi/USB-net changes, no persistent storage writes.",
                "",
            ]
        )
    )


def run_capture(args: argparse.Namespace) -> int:
    port = args.port or discover_port()
    if not port:
        print(
            "ERROR: no USB ACM serial port found. Expected one of: "
            + ", ".join(DEFAULT_PORT_PATTERNS),
            file=sys.stderr,
        )
        return 2
    port_path = Path(port)
    if not port_path.exists():
        print(f"ERROR: serial port does not exist: {port}", file=sys.stderr)
        return 2

    session_dir = args.session_dir or allocate_session_dir(args.session_root)
    if session_dir.exists():
        print(f"ERROR: session directory already exists: {session_dir}", file=sys.stderr)
        return 2
    command_text = command_script()
    transcript_bytes = b""

    fd = os.open(str(port_path), os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old_attrs: list[Any] | None = None
    try:
        old_attrs = configure_serial(fd, args.baud)
        write_all(fd, b"\n")
        transcript_bytes += read_available(fd, args.initial_read_seconds)
        write_all(fd, command_text.encode())
        body, _ = read_until_marker(fd, END_MARKER.encode(), args.read_timeout)
        transcript_bytes += body
    finally:
        if old_attrs is not None:
            restore_serial(fd, old_attrs)
        os.close(fd)

    transcript = transcript_bytes.decode(errors="replace")
    summary = summarize_transcript(transcript, str(port_path), session_dir)
    write_session_artifacts(session_dir, str(port_path), args, command_text, transcript, summary)
    print_json(summary)
    return 0 if summary["end_marker_observed"] else 3


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture HK Invoke no-NAND read-only inventory over USB ACM serial."
    )
    parser.add_argument("--check", action="store_true", help="print static safety contract; do not open serial")
    parser.add_argument("--print-commands", action="store_true", help="print JSON command list; do not open serial")
    parser.add_argument("--port", help="USB ACM serial port, e.g. /dev/cu.usbmodemno_nand_probe_1")
    parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    parser.add_argument("--session-dir", type=Path, help="exact output session directory")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--initial-read-seconds", type=float, default=1.5)
    parser.add_argument("--read-timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.check:
        print_json(safety_profile())
        return 0
    if args.print_commands:
        print_json(READ_ONLY_COMMANDS)
        return 0
    return run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
