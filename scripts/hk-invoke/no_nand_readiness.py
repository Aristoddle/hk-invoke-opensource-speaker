#!/usr/bin/env python3
"""Classify whether an HK Invoke RAM-only no-NAND attempt is currently sensible.

This is a host-observation gate. In default mode it runs the local state script,
which may run bounded `hk_usb_boot detect`; it does not send USB boot payloads,
open serial devices, run ADB, or write device storage.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HOME = Path.home()
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_ARTIFACT_ROOT = HOME / ".local/state/hk-invoke/no-nand-initramfs"

BT_PATTERN = re.compile(r"HK Invoke|Invoke|Harman|Kardon", re.I)
AUDIO_PATTERN = re.compile(r"HK Invoke|Invoke|Harman|Kardon|Bluetooth", re.I)
MARVELL_READY_PATTERN = re.compile(
    r"device ready|BG2CD|subclass\s*[=: ]\s*(ff|fe)|class\s*[=: ]\s*ff|iROM|U-Boot|MV88DE",
    re.I,
)
MARVELL_ABSENT_PATTERN = re.compile(
    r"no Invoke/Marvell WTP device currently visible|hk_usb_boot not built",
    re.I,
)


def utc_stamp() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def run_cmd(cmd: list[str], cwd: Path = REPO_ROOT, timeout: int = 30) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
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


def latest_artifact() -> Path | None:
    if not DEFAULT_ARTIFACT_ROOT.exists():
        return None
    dirs = sorted(path for path in DEFAULT_ARTIFACT_ROOT.iterdir() if path.is_dir())
    return dirs[-1] if dirs else None


def artifact_summary(artifact: Path | None) -> dict[str, Any]:
    if artifact is None:
        return {"artifact": None, "exists": False}
    image_dir = artifact / "image-dir"
    commands = artifact / "uboot-ram-commands.txt"
    manifest = artifact / "manifest.json"
    summary: dict[str, Any] = {
        "artifact": str(artifact),
        "exists": artifact.exists(),
        "image_dir": str(image_dir),
        "image_dir_exists": image_dir.exists(),
        "uboot_commands": str(commands),
        "uboot_commands_exists": commands.exists(),
        "ready_for_check_only": image_dir.exists() and commands.exists(),
    }
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            summary.update(
                {
                    "manifest_ready": data.get("ready"),
                    "path_separation_ok": data.get("path_separation_ok"),
                    "base_79_image_safe": data.get("base_79_image_safe"),
                    "live_classification": data.get("live_classification"),
                }
            )
        except json.JSONDecodeError as exc:
            summary["manifest_error"] = str(exc)
    return summary


def section(text: str, title: str) -> str:
    current: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("== ") and line.endswith(" =="):
            in_section = title.lower() in line.lower()
            continue
        if in_section:
            current.append(line)
    return "\n".join(current)


def has_match_without_absence(text: str, pattern: re.Pattern[str]) -> bool:
    return any(pattern.search(line) for line in text.splitlines() if line.strip())


def classify_state(text: str) -> dict[str, bool]:
    bluetooth = section(text, "Bluetooth connected")
    audio = section(text, "macOS audio")
    marvell = section(text, "Marvell service-loader USB check")
    marvell_absent = bool(MARVELL_ABSENT_PATTERN.search(marvell))
    marvell_ready = (not marvell_absent) and bool(MARVELL_READY_PATTERN.search(marvell))
    return {
        "bluetooth_visible": has_match_without_absence(bluetooth, BT_PATTERN),
        "audio_visible": has_match_without_absence(audio, AUDIO_PATTERN),
        "marvell_service_loader_visible": marvell_ready,
    }


def builder_check(skip: bool) -> dict[str, Any]:
    if skip:
        return {"skipped": True}
    result = run_cmd(["scripts/hk-invoke/build_no_nand_initramfs.py", "--check"], timeout=45)
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(result["output"])
    except json.JSONDecodeError:
        parsed = {"parse_error": True}
    return {
        "skipped": False,
        "exit_code": result["exit_code"],
        "ready": parsed.get("ready"),
        "path_separation_ok": parsed.get("path_separation_ok"),
        "base_79_image_safe": parsed.get("base_79_image_safe"),
        "tools": parsed.get("tools"),
    }


def decide(facts: dict[str, bool]) -> tuple[str, bool, str, list[str]]:
    if facts["marvell_service_loader_visible"]:
        return (
            "arm_ram_listener_allowed",
            True,
            "Marvell service-loader/U-Boot surface is visible; only arm the guarded RAM listener with a fresh generated artifact.",
            [
                "artifact=$(make no-nand-initramfs | tail -1)",
                'scripts/hk-invoke/ram_boot_console.sh --check-only "$artifact/image-dir"',
                'scripts/hk-invoke/ram_boot_console.sh "$artifact/image-dir"',
                'python3 scripts/hk-invoke/no_nand_post_boot_probe.py --artifact "$artifact"',
            ],
        )
    if facts["bluetooth_visible"] or facts["audio_visible"]:
        return (
            "normal_baseline_visible",
            False,
            "Normal Bluetooth/audio baseline is visible; do not enter service mode unless Joe explicitly asks for a RAM-only session.",
            [
                "make state",
                "make host-audio-devices",
                "make normal-probe",
            ],
        )
    return (
        "hold_no_custom_boot",
        False,
        "Neither normal Bluetooth/audio baseline nor Marvell service-loader is visible; preserve this state and restore one surface before custom boot.",
        [
            "make surface-watch",
            "Power-cycle or re-pair in normal mode while surface-watch is running",
            "Do not start a RAM listener until either service-loader visibility or normal baseline is re-established",
        ],
    )


def readiness_report(
    state_text: str,
    artifact: Path | None,
    skip_builder_check: bool,
) -> dict[str, Any]:
    facts = classify_state(state_text)
    decision, custom_next, reason, commands = decide(facts)
    return {
        "classification": "host-observation-with-libusb-detect",
        "timestamp_utc": utc_stamp(),
        **facts,
        "decision": decision,
        "custom_ram_boot_next": custom_next,
        "reason": reason,
        "recommended_commands": commands,
        "builder_check": builder_check(skip_builder_check),
        "latest_artifact": artifact_summary(artifact),
        "sends_usb_payloads": False,
        "opens_serial": False,
        "runs_adb": False,
        "writes_device_storage": False,
        "persistent_storage_changes_allowed": False,
    }


def human_report(report: dict[str, Any]) -> str:
    lines = [
        "# HK Invoke no-NAND readiness",
        "",
        f"Classification: {report['classification']}",
        f"Decision: {report['decision']}",
        f"Custom RAM boot next: {str(report['custom_ram_boot_next']).lower()}",
        f"Bluetooth visible: {str(report['bluetooth_visible']).lower()}",
        f"Audio visible: {str(report['audio_visible']).lower()}",
        f"Marvell service-loader visible: {str(report['marvell_service_loader_visible']).lower()}",
        "",
        report["reason"],
        "",
        "Recommended commands:",
    ]
    lines.extend(f"  {cmd}" for cmd in report["recommended_commands"])
    lines.extend(
        [
            "",
            "Safety: no USB payloads, serial opens, ADB, or device-storage writes are performed by this readiness gate.",
        ]
    )
    builder = report.get("builder_check", {})
    if not builder.get("skipped"):
        lines.append(f"Builder check ready: {builder.get('ready')}")
    artifact = report.get("latest_artifact", {})
    if artifact.get("exists"):
        lines.append(f"Latest artifact: {artifact.get('artifact')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--check", action="store_true", help="print static safety classification; do not run host probes")
    parser.add_argument("--state-file", type=Path, help="parse saved hk_invoke_state.sh output instead of probing")
    parser.add_argument("--artifact", type=Path, help="generated no-NAND artifact; defaults to latest if present")
    parser.add_argument("--skip-builder-check", action="store_true", help="do not run build_no_nand_initramfs.py --check")
    args = parser.parse_args()

    if args.check:
        print(
            json.dumps(
                {
                    "classification": "host-observation-with-libusb-detect",
                    "sends_usb_payloads": False,
                    "opens_serial": False,
                    "runs_adb": False,
                    "writes_device_storage": False,
                    "persistent_storage_changes_allowed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.state_file:
        state_text = args.state_file.expanduser().read_text()
    else:
        state = run_cmd(["zsh", "scripts/hk-invoke/hk_invoke_state.sh"], timeout=30)
        state_text = state["output"]

    artifact = args.artifact.expanduser() if args.artifact else latest_artifact()
    report = readiness_report(state_text, artifact, args.skip_builder_check)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(human_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
