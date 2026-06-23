#!/usr/bin/env python3
"""Write a bounded HK Invoke native-connectivity operator packet.

This is host observation plus offline artifact preparation. It may run the
local state script, which can perform bounded `hk_usb_boot detect`; it does not
send USB boot payloads, open serial devices, run ADB, or write Invoke storage.
Generated no-NAND images and probe plans are host-local artifacts under the
session directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HOME = Path.home()
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_SESSION_ROOT = HOME / ".local/state/hk-invoke/sessions"
WIFI_PROFILES = {
    "named_targets": ["YOURSSID", "YOURSSID", "yourssid"],
    "credential_posture": "runtime_env_only",
    "approval_env": "HK_INVOKE_RAM_WIFI_APPROVED",
    "runtime_psk_env": [
        "HK_INVOKE_RAM_WIFI_YOURSSID_PSK",
    ],
}
ESCALATION_GATES = [
    {
        "id": "serial_read_only_inventory",
        "kind": "deliberate-serial-open-read-only-inventory",
        "approval_required": False,
        "approval_env": None,
        "host_command": "python3 scripts/hk-invoke/no_nand_serial_inventory.py --port /dev/cu.usbmodemno_nand_probe_1",
        "device_command": None,
        "requires": [
            "post-boot probe shows /dev/cu.usbmodemno_nand_probe_1",
            "generated no-NAND RAM shell is already running",
        ],
        "opens_serial": True,
        "loads_modules": False,
        "starts_daemons": False,
        "wifi_or_network_changes": False,
        "persistent_storage_changes_allowed": False,
    },
    {
        "id": "audio_read_only_inventory",
        "kind": "ram-shell-read-only-inventory",
        "approval_required": False,
        "approval_env": None,
        "host_command": None,
        "device_command": "sh /no-nand-audio-inventory.sh && cat /tmp/no-nand-audio-inventory.txt",
        "requires": ["USB ACM root shell or equivalent live RAM shell"],
        "opens_serial": False,
        "loads_modules": False,
        "starts_daemons": False,
        "wifi_or_network_changes": False,
        "persistent_storage_changes_allowed": False,
    },
    {
        "id": "network_read_only_inventory",
        "kind": "ram-shell-read-only-inventory",
        "approval_required": False,
        "approval_env": None,
        "host_command": None,
        "device_command": "sh /no-nand-network-inventory.sh && cat /tmp/no-nand-network-inventory.txt",
        "requires": ["USB ACM root shell or equivalent live RAM shell"],
        "opens_serial": False,
        "loads_modules": False,
        "starts_daemons": False,
        "wifi_or_network_changes": False,
        "persistent_storage_changes_allowed": False,
    },
    {
        "id": "ota83_module_load",
        "kind": "ram-only-kernel-state-escalation",
        "approval_required": True,
        "approval_env": "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED",
        "host_command": None,
        "device_command": "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED=yes /no-nand-ota83-module-load.sh",
        "requires": [
            "artifact generated with make no-nand-initramfs-ota83",
            "read-only serial/audio/network inventory preserved first",
            "Joe explicitly approves OTA83 module loading for this live RAM session",
        ],
        "opens_serial": False,
        "loads_modules": True,
        "starts_daemons": False,
        "wifi_or_network_changes": False,
        "persistent_storage_changes_allowed": False,
    },
    {
        "id": "usb_network",
        "kind": "ram-only-live-usb-gadget-network-escalation",
        "approval_required": True,
        "approval_env": "HK_INVOKE_RAM_USBNET_APPROVED",
        "host_command": None,
        "device_command": "HK_INVOKE_RAM_USBNET_APPROVED=yes /no-nand-usbnet.sh",
        "requires": [
            "read-only network inventory preserved first",
            "Joe explicitly approves live USB gadget/network changes for this session",
        ],
        "opens_serial": False,
        "loads_modules": False,
        "starts_daemons": False,
        "wifi_or_network_changes": True,
        "persistent_storage_changes_allowed": False,
    },
    {
        "id": "wifi_named_profiles",
        "kind": "ram-only-live-wifi-association-escalation",
        "approval_required": True,
        "approval_env": "HK_INVOKE_RAM_WIFI_APPROVED",
        "runtime_secret_env": [
            "HK_INVOKE_RAM_WIFI_YOURSSID_PSK",
        ],
        "host_command": None,
        "device_command": "HK_INVOKE_RAM_WIFI_APPROVED=yes HK_INVOKE_RAM_WIFI_YOURSSID_PSK=<psk> /no-nand-wifi.sh",
        "requires": [
            "read-only network inventory preserved first",
            "Joe explicitly approves live Wi-Fi association for this session",
            "PSKs supplied only as live runtime environment values",
        ],
        "opens_serial": False,
        "loads_modules": True,
        "starts_daemons": True,
        "wifi_or_network_changes": True,
        "persistent_storage_changes_allowed": False,
    },
]


def utc_stamp() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def run_cmd(cmd: list[str], timeout: int = 120) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
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


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_json(path: Path, data: Any) -> Path:
    return write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def check_summary() -> dict[str, Any]:
    return {
        "classification": "host-observation-with-libusb-detect",
        "sends_usb_payloads": False,
        "opens_serial": False,
        "runs_adb": False,
        "writes_device_storage": False,
        "persistent_storage_changes_allowed": False,
        "builds_artifacts_offline_only": True,
        "writes_host_session_artifacts": True,
        "escalation_packet_defined": True,
        "escalation_gates": ESCALATION_GATES,
    }


def copy_or_probe_state(args: argparse.Namespace, session_dir: Path) -> Path:
    state_path = session_dir / "state.txt"
    if args.state_file:
        source = args.state_file.expanduser()
        text = source.read_text()
    else:
        result = run_cmd(["zsh", "scripts/hk-invoke/hk_invoke_state.sh"], timeout=30)
        write_json(session_dir / "commands/state-command.json", result)
        text = result["output"]
    return write_text(state_path, text)


def run_readiness(state_path: Path, session_dir: Path) -> dict[str, Any]:
    json_path = session_dir / "readiness.json"
    result = run_cmd(
        [
            "python3",
            "scripts/hk-invoke/no_nand_readiness.py",
            "--json",
            "--state-file",
            str(state_path),
            "--skip-builder-check",
        ],
        timeout=45,
    )
    write_json(session_dir / "commands/readiness-command.json", result)
    write_text(session_dir / "readiness.raw.json", result["output"])
    try:
        data = json.loads(result["output"])
    except json.JSONDecodeError as exc:
        data = {
            "decision": "readiness_parse_error",
            "custom_ram_boot_next": False,
            "reason": f"Failed to parse readiness JSON: {exc}",
            "recommended_commands": ["Inspect readiness.raw.json"],
        }
    write_json(json_path, data)
    return data


def run_surface_watch(state_path: Path, session_dir: Path) -> dict[str, Any]:
    watch_dir = session_dir / "surface-watch"
    result = run_cmd(
        [
            "python3",
            "scripts/hk-invoke/surface_watch.py",
            "--state-file",
            str(state_path),
            "--session-dir",
            str(watch_dir),
            "--interval",
            "0",
            "--duration",
            "0",
            "--json",
        ],
        timeout=45,
    )
    write_json(session_dir / "commands/surface-watch-command.json", result)
    write_text(session_dir / "surface-watch.raw.json", result["output"])
    try:
        data = json.loads(result["output"])
    except json.JSONDecodeError as exc:
        data = {
            "decision": "surface_watch_parse_error",
            "surface_seen": False,
            "custom_ram_boot_next": False,
            "reason": f"Failed to parse surface-watch JSON: {exc}",
            "summary_json": str(watch_dir / "summary.json"),
            "summary_md": str(watch_dir / "summary.md"),
        }
    return data


def run_plan(session_dir: Path) -> dict[str, Any]:
    plans_root = session_dir / "plans"
    result = run_cmd(
        [
            "python3",
            "scripts/hk-invoke/prepare_no_nand_probe.py",
            "--out-root",
            str(plans_root),
        ],
        timeout=60,
    )
    write_json(session_dir / "commands/no-nand-plan-command.json", result)
    plan_dir = result["output"].strip().splitlines()[-1] if result["output"].strip() else ""
    return {
        "skipped": False,
        "exit_code": result["exit_code"],
        "plan_dir": plan_dir,
        "plan_dir_exists": bool(plan_dir) and Path(plan_dir).exists(),
    }


def run_artifact_build(session_dir: Path, skip: bool) -> dict[str, Any]:
    if skip:
        return {"skipped": True}
    artifacts_root = session_dir / "artifacts"
    result = run_cmd(
        [
            "python3",
            "scripts/hk-invoke/build_no_nand_initramfs.py",
            "--stage-ota83-connectivity",
            "--out-root",
            str(artifacts_root),
        ],
        timeout=180,
    )
    write_json(session_dir / "commands/ota83-artifact-build-command.json", result)
    artifact_dir = (
        result["output"].strip().splitlines()[-1] if result["output"].strip() else ""
    )
    artifact = Path(artifact_dir) if artifact_dir else None
    check: dict[str, Any] = {"skipped": True}
    if artifact and artifact.exists():
        check = run_cmd(
            [
                "zsh",
                "scripts/hk-invoke/ram_boot_console.sh",
                "--check-only",
                str(artifact / "image-dir"),
            ],
            timeout=120,
        )
        write_json(session_dir / "commands/ram-check-only-command.json", check)
    return {
        "skipped": False,
        "exit_code": result["exit_code"],
        "artifact_dir": artifact_dir,
        "artifact_dir_exists": bool(artifact and artifact.exists()),
        "ram_check_only_exit_code": check.get("exit_code"),
    }


def run_host_fallback(session_dir: Path, skip: bool) -> dict[str, Any]:
    if skip:
        return {"skipped": True}
    audio = run_cmd(
        ["python3", "scripts/host/list_audio_devices.py", "--require-input", "MacBook"],
        timeout=30,
    )
    realtime = run_cmd(
        [
            "python3",
            "scripts/host/host_realtime_assistant.py",
            "--dry-run",
            "--skip-device-query",
        ],
        timeout=30,
    )
    write_text(session_dir / "host-audio-fallback.txt", audio["output"])
    write_text(session_dir / "host-realtime-dry-run.txt", realtime["output"])
    write_json(session_dir / "commands/host-audio-fallback-command.json", audio)
    write_json(session_dir / "commands/host-realtime-dry-run-command.json", realtime)
    return {
        "skipped": False,
        "audio_exit_code": audio["exit_code"],
        "realtime_dry_run_exit_code": realtime["exit_code"],
        "audio_output": str(session_dir / "host-audio-fallback.txt"),
        "realtime_output": str(session_dir / "host-realtime-dry-run.txt"),
    }


def markdown_summary(summary: dict[str, Any]) -> str:
    recommended = summary.get("recommended_commands") or []
    lines = [
        "# HK Invoke native-connectivity packet",
        "",
        f"Session: `{summary['session_dir']}`",
        f"Classification: `{summary['classification']}`",
        f"Decision: `{summary['decision']}`",
        f"Custom RAM boot next: `{str(summary['custom_ram_boot_next']).lower()}`",
        "",
        "## Live surface",
        "",
        f"- Bluetooth visible: `{str(summary['bluetooth_visible']).lower()}`",
        f"- Audio visible: `{str(summary['audio_visible']).lower()}`",
        f"- Marvell service-loader visible: `{str(summary['marvell_service_loader_visible']).lower()}`",
        "",
        "## Safety invariant",
        "",
        "- No USB boot payloads sent by this packet.",
        "- No serial devices opened by this packet.",
        "- No ADB commands run by this packet.",
        "- No Invoke NAND/eMMC/SPI/env/device-storage writes.",
        "",
        "## Escalation packet",
        "",
        "Run gates in this order. Approval-gated rows require Joe's exact approval",
        "for the current live RAM session before the listed env var is set.",
        "",
    ]
    for gate in summary.get("escalation_gates", []):
        approval = (
            f"`{gate.get('approval_env')}`"
            if gate.get("approval_required")
            else "not required"
        )
        lines.extend(
            [
                f"### {gate['id']}",
                "",
                f"- Kind: `{gate.get('kind')}`",
                f"- Approval required: `{str(gate.get('approval_required')).lower()}`",
                f"- Approval env: {approval}",
                f"- Persistent storage writes allowed: `{str(gate.get('persistent_storage_changes_allowed')).lower()}`",
            ]
        )
        if gate.get("host_command"):
            lines.append(f"- Host command: `{gate['host_command']}`")
        if gate.get("device_command"):
            lines.append(f"- RAM-shell command: `{gate['device_command']}`")
        if gate.get("runtime_secret_env"):
            lines.append(
                "- Runtime secret env only: "
                + ", ".join(f"`{name}`" for name in gate["runtime_secret_env"])
            )
        lines.append("")
    lines.extend(
        [
            "## RAM Wi-Fi targets",
            "",
            "- Named profiles: `YOURSSID`/`YOURSSID`/`yourssid` aliases only.",
            "- Credential posture: runtime-only env vars; no PSKs are stored in the packet or artifact.",
            "- Approval gate: `HK_INVOKE_RAM_WIFI_APPROVED=yes`.",
            "- Runtime PSK env var: `HK_INVOKE_RAM_WIFI_YOURSSID_PSK`.",
            "",
            "## Recommendation",
            "",
            summary.get("reason") or "No reason recorded.",
            "",
        ]
    )
    if recommended:
        lines.append("Recommended commands:")
        lines.extend(f"- `{cmd}`" for cmd in recommended)
        lines.append("")
    surface = summary.get("surface_watch", {})
    lines.extend(
        [
            "## Surface watch",
            "",
            f"- Decision: `{surface.get('decision')}`",
            f"- Surface seen: `{str(surface.get('surface_seen')).lower()}`",
            f"- Samples: `{surface.get('samples_count')}`",
            f"- Summary: `{surface.get('summary_md') or 'not generated'}`",
            "",
        ]
    )
    artifacts = summary.get("artifacts", {})
    lines.extend(
        [
            "## Offline artifacts",
            "",
            f"- Plan directory: `{artifacts.get('plan_dir') or 'not generated'}`",
            f"- OTA83 staged artifact: `{artifacts.get('ota83_artifact_dir') or 'not generated'}`",
            f"- RAM check-only exit: `{artifacts.get('ram_check_only_exit_code')}`",
            "",
            "## Host assistant fallback",
            "",
        ]
    )
    fallback = summary.get("host_fallback", {})
    if fallback.get("skipped"):
        lines.append("- host assistant fallback was skipped for this packet run.")
    else:
        lines.extend(
            [
                f"- host assistant fallback audio gate exit: `{fallback.get('audio_exit_code')}`",
                f"- host assistant fallback realtime dry-run exit: `{fallback.get('realtime_dry_run_exit_code')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- State: `{summary['files']['state']}`",
            f"- Readiness JSON: `{summary['files']['readiness_json']}`",
            f"- Summary JSON: `{summary['files']['summary_json']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    session_dir = (
        args.session_dir.expanduser()
        if args.session_dir
        else DEFAULT_SESSION_ROOT / f"{utc_stamp()}-native-connectivity-packet"
    )
    session_dir.mkdir(parents=True, exist_ok=True)

    state_path = copy_or_probe_state(args, session_dir)
    readiness = run_readiness(state_path, session_dir)
    surface_watch = run_surface_watch(state_path, session_dir)
    plan = run_plan(session_dir)
    artifact = run_artifact_build(session_dir, args.skip_artifact_build)
    host_fallback = run_host_fallback(session_dir, args.skip_host_fallback)

    summary = {
        **check_summary(),
        "session_dir": str(session_dir),
        "timestamp_utc": utc_stamp(),
        "decision": readiness.get("decision"),
        "custom_ram_boot_next": readiness.get("custom_ram_boot_next"),
        "bluetooth_visible": readiness.get("bluetooth_visible"),
        "audio_visible": readiness.get("audio_visible"),
        "marvell_service_loader_visible": readiness.get("marvell_service_loader_visible"),
        "reason": readiness.get("reason"),
        "recommended_commands": readiness.get("recommended_commands") or [],
        "wifi_profiles": WIFI_PROFILES,
        "escalation_gates": ESCALATION_GATES,
        "surface_watch": {
            "decision": surface_watch.get("decision"),
            "surface_seen": surface_watch.get("surface_seen"),
            "surface_kind": surface_watch.get("surface_kind"),
            "custom_ram_boot_next": surface_watch.get("custom_ram_boot_next"),
            "samples_count": surface_watch.get("samples_count"),
            "first_surface_sample": surface_watch.get("first_surface_sample"),
            "summary_json": surface_watch.get("summary_json"),
            "summary_md": surface_watch.get("summary_md"),
        },
        "files": {
            "state": str(state_path),
            "readiness_json": str(session_dir / "readiness.json"),
            "summary_json": str(session_dir / "summary.json"),
            "summary_md": str(session_dir / "summary.md"),
        },
        "artifacts": {
            "plan_dir": plan.get("plan_dir"),
            "plan_dir_exists": plan.get("plan_dir_exists"),
            "ota83_artifact_dir": artifact.get("artifact_dir"),
            "ota83_artifact_dir_exists": artifact.get("artifact_dir_exists"),
            "ram_check_only_exit_code": artifact.get("ram_check_only_exit_code"),
            "artifact_build_skipped": artifact.get("skipped"),
        },
        "host_fallback": host_fallback,
        "command_results": {
            "plan_exit_code": plan.get("exit_code"),
            "artifact_build_exit_code": artifact.get("exit_code"),
        },
    }
    write_json(session_dir / "summary.json", summary)
    write_text(session_dir / "summary.md", markdown_summary(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="print static safety classification; do not run probes")
    parser.add_argument("--json", action="store_true", help="print summary JSON instead of the session path")
    parser.add_argument("--state-file", type=Path, help="parse saved hk_invoke_state.sh output instead of probing")
    parser.add_argument("--session-dir", type=Path, help="where to write the packet")
    parser.add_argument("--skip-artifact-build", action="store_true", help="skip OTA83 no-NAND artifact generation")
    parser.add_argument("--skip-host-fallback", action="store_true", help="skip host-audio-fallback and realtime dry-run checks")
    args = parser.parse_args()

    if args.check:
        print(json.dumps(check_summary(), indent=2, sort_keys=True))
        return 0

    if not shutil.which("python3"):
        print("missing python3", file=sys.stderr)
        return 127

    summary = build_summary(args)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(summary["session_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
