#!/usr/bin/env python3
"""Watch host-visible surfaces after an HK Invoke no-NAND RAM boot.

This is a host-observation tool, not a purely passive one when hk_usb_boot is
available: the default probe runs `hk_usb_boot detect`, which is a bounded
libusb enumeration/protocol-client check. By default it does not open serial
devices, run ADB, send USB boot payloads, write to the Invoke, or mutate any
device storage. It captures enough host state to tell whether the no-NAND
initramfs produced an ACM serial port, ADB-looking USB descriptor, audio device,
or network interface.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

HOME = Path.home()
DEFAULT_ARTIFACT_ROOT = HOME / ".local/state/hk-invoke/no-nand-initramfs"
DEFAULT_SESSION_ROOT = HOME / ".local/state/hk-invoke/sessions"
DEFAULT_BOOT_BIN = HOME / ".local/state/hk-invoke/bin/hk_usb_boot"
DEFAULT_IMAGE_ADB = Path("/tmp/hk-invoke-ota2-work-current/adb")

USB_PATTERN = re.compile(
    r"1286|8174|MRVL|Marvell|USB SDK|Invoke|Harman|Kardon|Android|ADB|ACM|Serial|no-NAND|0d02",
    re.I,
)
SERIAL_PATTERN = re.compile(r"usb|modem|acm|serial|android|invoke|mrv|nand", re.I)
AUDIO_PATTERN = re.compile(r"HK Invoke|Invoke|Harman|Kardon|Bluetooth", re.I)
NET_PATTERN = re.compile(
    r"usb|rndis|ecm|ncm|cdc|android|mrv|marvell|invoke|1286|0d02", re.I
)


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def latest_artifact() -> Path | None:
    if not DEFAULT_ARTIFACT_ROOT.exists():
        return None
    dirs = sorted(path for path in DEFAULT_ARTIFACT_ROOT.iterdir() if path.is_dir())
    return dirs[-1] if dirs else None


def run_cmd(cmd: list[str], timeout: int = 20) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return {
            "cmd": cmd,
            "exit_code": proc.returncode,
            "duration_ms": int((time.time() - started) * 1000),
            "output": proc.stdout,
        }
    except FileNotFoundError:
        return {
            "cmd": cmd,
            "exit_code": 127,
            "duration_ms": int((time.time() - started) * 1000),
            "output": f"missing command: {cmd[0]}\n",
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return {
            "cmd": cmd,
            "exit_code": 124,
            "duration_ms": int((time.time() - started) * 1000),
            "output": output + f"\nTIMEOUT after {timeout}s\n",
        }


def matching_lines(text: str, pattern: re.Pattern[str]) -> list[str]:
    return [line for line in text.splitlines() if pattern.search(line)]


def serial_devices() -> list[str]:
    paths: list[Path] = []
    for root in (Path("/dev"),):
        paths.extend(root.glob("cu.*"))
        paths.extend(root.glob("tty.*"))
    return sorted(str(path) for path in paths if SERIAL_PATTERN.search(path.name))


def adb_commands(artifact: Path | None) -> list[list[str]]:
    commands: list[list[str]] = []
    system_adb = shutil.which("adb")
    if system_adb:
        commands.append([system_adb, "devices", "-l"])
    if artifact:
        candidate = artifact / "image-dir" / "adb"
        if candidate.exists() and os.access(candidate, os.X_OK):
            commands.append([str(candidate), "devices", "-l"])
    if DEFAULT_IMAGE_ADB.exists() and os.access(DEFAULT_IMAGE_ADB, os.X_OK):
        commands.append([str(DEFAULT_IMAGE_ADB), "devices", "-l"])
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[list[str]] = []
    for cmd in commands:
        key = "\0".join(cmd)
        if key not in seen:
            unique.append(cmd)
            seen.add(key)
    return unique


def artifact_summary(artifact: Path | None) -> dict[str, Any]:
    if artifact is None:
        return {"artifact": None, "exists": False}
    manifest_path = artifact / "manifest.json"
    commands_path = artifact / "uboot-ram-commands.txt"
    image_dir = artifact / "image-dir"
    summary: dict[str, Any] = {
        "artifact": str(artifact),
        "exists": artifact.exists(),
        "image_dir": str(image_dir),
        "image_dir_exists": image_dir.exists(),
        "uboot_commands": str(commands_path),
        "uboot_commands_exists": commands_path.exists(),
    }
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            summary.update(
                {
                    "ready": manifest.get("ready"),
                    "path_separation_ok": manifest.get("path_separation_ok"),
                    "custom_82_size": manifest.get("custom_82_size"),
                    "live_classification": manifest.get("live_classification"),
                }
            )
        except json.JSONDecodeError as exc:
            summary["manifest_error"] = str(exc)
    return summary


def one_sample(artifact: Path | None, include_adb_devices: bool = False) -> dict[str, Any]:
    sample: dict[str, Any] = {
        "timestamp": utc_stamp(),
        "serial_devices": serial_devices(),
    }

    usb = run_cmd(
        ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"], timeout=25
    )
    audio = run_cmd(["system_profiler", "SPAudioDataType"], timeout=25)
    ifconfig = run_cmd(["ifconfig"], timeout=10)
    route = run_cmd(["netstat", "-rn"], timeout=10)

    sample["usb_matches"] = matching_lines(usb["output"], USB_PATTERN)
    sample["audio_matches"] = matching_lines(audio["output"], AUDIO_PATTERN)
    sample["network_matches"] = matching_lines(ifconfig["output"], NET_PATTERN)
    sample["commands"] = {
        "usb": {k: v for k, v in usb.items() if k != "output"},
        "audio": {k: v for k, v in audio.items() if k != "output"},
        "ifconfig": {k: v for k, v in ifconfig.items() if k != "output"},
        "route": {k: v for k, v in route.items() if k != "output"},
    }
    sample["raw"] = {
        "usb": usb["output"],
        "audio": audio["output"],
        "ifconfig": ifconfig["output"],
        "route": route["output"],
    }

    if DEFAULT_BOOT_BIN.exists() and os.access(DEFAULT_BOOT_BIN, os.X_OK):
        detect = run_cmd([str(DEFAULT_BOOT_BIN), "detect"], timeout=10)
        sample["marvell_detect"] = detect["output"]
        sample["marvell_visible"] = (
            "no Invoke/Marvell WTP device currently visible" not in detect["output"]
        )

    sample["adb"] = []
    if include_adb_devices:
        adb_results = []
        for cmd in adb_commands(artifact):
            result = run_cmd(cmd, timeout=10)
            lines = [
                line
                for line in result["output"].splitlines()
                if line.strip() and "List of devices" not in line
            ]
            adb_results.append(
                {
                    "cmd": cmd,
                    "exit_code": result["exit_code"],
                    "duration_ms": result["duration_ms"],
                    "device_lines": lines,
                    "output": result["output"],
                }
            )
        sample["adb"] = adb_results

    return sample


def has_surface(sample: dict[str, Any]) -> bool:
    return bool(
        sample.get("serial_devices")
        or sample.get("usb_matches")
        or sample.get("audio_matches")
        or sample.get("network_matches")
        or sample.get("marvell_visible")
        or any(result.get("device_lines") for result in sample.get("adb", []))
    )


def write_report(
    session_dir: Path,
    artifact: Path | None,
    samples: list[dict[str, Any]],
    include_adb_devices: bool = False,
) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    runs_libusb_detect = DEFAULT_BOOT_BIN.exists() and os.access(DEFAULT_BOOT_BIN, os.X_OK)
    classification = (
        "host-observation-with-libusb-detect"
        if runs_libusb_detect
        else "host-observation"
    )
    summary = {
        "classification": classification,
        "safety": {
            "opens_serial": False,
            "runs_libusb_detect": runs_libusb_detect,
            "runs_adb_devices": include_adb_devices,
            "runs_adb_shell": False,
            "sends_usb_boot_payloads": False,
            "writes_device_storage": False,
        },
        "artifact": artifact_summary(artifact),
        "sample_count": len(samples),
        "surface_seen": any(has_surface(sample) for sample in samples),
        "samples": [
            {k: v for k, v in sample.items() if k != "raw"} for sample in samples
        ],
    }
    (session_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )

    lines = [
        "# HK Invoke no-NAND post-boot host probe",
        "",
        (
            f"Classification: {classification.replace('-', ' ')}. "
            "No serial open, "
            f"ADB device-list probe: {include_adb_devices}, no adb shell, "
            "USB boot payloads sent: False, no device storage writes."
        ),
        "",
        f"Artifact: {artifact or 'none'}",
        f"Surface seen: {summary['surface_seen']}",
        f"Samples: {len(samples)}",
        "",
    ]
    for idx, sample in enumerate(samples, 1):
        lines.extend(
            [
                f"## Sample {idx} — {sample['timestamp']}",
                "",
                f"Serial devices: {sample.get('serial_devices') or 'none'}",
                f"Marvell visible: {sample.get('marvell_visible', False)}",
                f"USB matches: {sample.get('usb_matches') or 'none'}",
                f"Audio matches: {sample.get('audio_matches') or 'none'}",
                f"Network matches: {sample.get('network_matches') or 'none'}",
                "ADB device lines:",
            ]
        )
        adb_lines = []
        for result in sample.get("adb", []):
            adb_lines.extend(result.get("device_lines") or [])
        lines.append("  " + "\n  ".join(adb_lines) if adb_lines else "  none")
        lines.append("")
    (session_dir / "summary.md").write_text("\n".join(lines) + "\n")

    raw_dir = session_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    for idx, sample in enumerate(samples, 1):
        for name, output in sample.get("raw", {}).items():
            (raw_dir / f"sample-{idx:02d}-{name}.txt").write_text(output)
        if "marvell_detect" in sample:
            (raw_dir / f"sample-{idx:02d}-marvell-detect.txt").write_text(
                sample["marvell_detect"]
            )
        for adb_idx, result in enumerate(sample.get("adb", []), 1):
            (raw_dir / f"sample-{idx:02d}-adb-{adb_idx}.txt").write_text(
                result["output"]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="generated no-NAND artifact dir; defaults to latest",
    )
    parser.add_argument(
        "--session-dir", type=Path, default=None, help="where to write probe artifacts"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=45.0,
        help="seconds to poll; 0 takes one sample",
    )
    parser.add_argument(
        "--interval", type=float, default=3.0, help="seconds between samples"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate paths/config and print JSON; do not run host probes",
    )
    parser.add_argument(
        "--adb-devices",
        action="store_true",
        help=(
            "also run 'adb devices -l' as an explicit read-only discovery step; "
            "not enabled by default because it may perform an ADB/USB handshake"
        ),
    )
    args = parser.parse_args()

    artifact = args.artifact.expanduser() if args.artifact else latest_artifact()
    session_dir = (
        args.session_dir.expanduser()
        if args.session_dir
        else DEFAULT_SESSION_ROOT / f"{utc_stamp()}-no-nand-post-boot-probe"
    )
    runs_libusb_detect = DEFAULT_BOOT_BIN.exists() and os.access(DEFAULT_BOOT_BIN, os.X_OK)
    classification = (
        "host-observation-with-libusb-detect"
        if runs_libusb_detect
        else "host-observation"
    )

    check_summary = {
        "classification": classification,
        "runs_libusb_detect": runs_libusb_detect,
        "adb_devices_probe": args.adb_devices,
        "artifact": artifact_summary(artifact),
        "session_dir": str(session_dir),
    }
    if args.check:
        print(json.dumps(check_summary, indent=2, sort_keys=True))
        artifact_info = check_summary["artifact"]
        return (
            0
            if artifact_info.get("image_dir_exists")
            and artifact_info.get("uboot_commands_exists")
            else 1
        )

    samples: list[dict[str, Any]] = []
    deadline = time.time() + max(0.0, args.duration)
    while True:
        sample = one_sample(artifact, include_adb_devices=args.adb_devices)
        samples.append(sample)
        write_report(session_dir, artifact, samples, include_adb_devices=args.adb_devices)
        if has_surface(sample) or args.duration <= 0 or time.time() >= deadline:
            break
        time.sleep(max(0.2, args.interval))

    print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
