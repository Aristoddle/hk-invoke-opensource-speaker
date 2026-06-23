#!/usr/bin/env python3
"""Watch for a safe HK Invoke surface before deciding the next operator step.

This is host observation only. Default mode repeatedly runs the bounded
`hk_invoke_state.sh` probe. Fixture mode (`--state-file`, repeatable) replays
saved state snapshots. It does not send USB boot payloads, open serial devices,
run ADB, or write Invoke storage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import no_nand_readiness

HOME = Path.home()
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_SESSION_ROOT = HOME / ".local/state/hk-invoke/sessions"

STATIC_SAFETY = {
    "classification": "host-observation-with-libusb-detect",
    "polls_for": ["normal_bluetooth_or_audio", "marvell_service_loader"],
    "sends_usb_payloads": False,
    "opens_serial": False,
    "runs_adb": False,
    "writes_device_storage": False,
    "persistent_storage_changes_allowed": False,
}


def utc_stamp() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_json(path: Path, data: Any) -> Path:
    return write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def run_state_probe(timeout: int) -> str:
    try:
        proc = subprocess.run(
            ["zsh", "scripts/hk-invoke/hk_invoke_state.sh"],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return proc.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return output + f"\nTIMEOUT after {timeout}s: hk_invoke_state.sh\n"


def surface_kind(facts: dict[str, bool]) -> str | None:
    if facts.get("marvell_service_loader_visible"):
        return "marvell_service_loader"
    if facts.get("bluetooth_visible") or facts.get("audio_visible"):
        return "normal_bluetooth_or_audio"
    return None


def markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# HK Invoke surface watch",
        "",
        f"Session: `{summary['session_dir']}`",
        f"Decision: `{summary['decision']}`",
        f"Surface seen: `{str(summary['surface_seen']).lower()}`",
        f"First surface sample: `{summary['first_surface_sample']}`",
        f"Custom RAM boot next: `{str(summary['custom_ram_boot_next']).lower()}`",
        "",
        "## Safety invariant",
        "",
        "- No USB boot payloads sent.",
        "- No serial devices opened.",
        "- No ADB commands run.",
        "- No Invoke NAND/eMMC/SPI/env/device-storage writes.",
        "",
        "## Recommendation",
        "",
        summary.get("reason") or "No reason recorded.",
        "",
        "Recommended commands:",
    ]
    lines.extend(f"- `{cmd}`" for cmd in summary.get("recommended_commands", []))
    operator = summary.get("operator_next_step", {})
    lines.extend(
        [
            "",
            "## Operator next step",
            "",
            operator.get("operator_phrase") or "not recorded",
            "",
            "Allowed physical actions:",
        ]
    )
    lines.extend(f"- {action}" for action in operator.get("allowed_physical_actions", []))
    lines.extend(["", "Forbidden physical actions:"])
    lines.extend(f"- {action}" for action in operator.get("forbidden_physical_actions", []))
    lines.extend(
        [
            "",
            "## Samples",
            "",
            f"- Samples collected: `{summary['samples_count']}`",
            f"- Surface kind: `{summary.get('surface_kind') or 'none'}`",
        ]
    )
    return "\n".join(lines) + "\n"


def collect_from_files(paths: list[Path]) -> list[dict[str, Any]]:
    samples = []
    for idx, path in enumerate(paths, start=1):
        text = path.expanduser().read_text()
        facts = no_nand_readiness.classify_state(text)
        samples.append(
            {
                "sample": idx,
                "source": str(path.expanduser()),
                "state_text": text,
                **facts,
                "surface_kind": surface_kind(facts),
            }
        )
    return samples


def collect_live(duration: float, interval: float, timeout: int) -> list[dict[str, Any]]:
    samples = []
    started = time.monotonic()
    sample_no = 0
    while True:
        sample_no += 1
        text = run_state_probe(timeout)
        facts = no_nand_readiness.classify_state(text)
        samples.append(
            {
                "sample": sample_no,
                "source": "live-hk_invoke_state",
                "state_text": text,
                **facts,
                "surface_kind": surface_kind(facts),
            }
        )
        if surface_kind(facts):
            break
        if duration <= 0 or time.monotonic() - started >= duration:
            break
        time.sleep(max(0.0, min(interval, duration - (time.monotonic() - started))))
    return samples


def summarize(samples: list[dict[str, Any]], session_dir: Path) -> dict[str, Any]:
    sample_records: list[dict[str, Any]] = []
    samples_dir = session_dir / "samples"
    artifact = no_nand_readiness.latest_artifact()
    for sample in samples:
        sample_no = int(sample["sample"])
        state_file = write_text(
            samples_dir / f"{sample_no:03d}-state.txt",
            str(sample["state_text"]),
        )
        readiness = no_nand_readiness.readiness_report(
            str(sample["state_text"]),
            artifact,
            skip_builder_check=True,
        )
        readiness_file = write_json(samples_dir / f"{sample_no:03d}-readiness.json", readiness)
        record = {k: v for k, v in sample.items() if k != "state_text"}
        record["state_file"] = str(state_file)
        record["readiness_file"] = str(readiness_file)
        sample_records.append(record)

    first_surface = next((sample for sample in samples if sample.get("surface_kind")), None)
    chosen = first_surface or samples[-1]
    facts = {
        "bluetooth_visible": bool(chosen.get("bluetooth_visible")),
        "audio_visible": bool(chosen.get("audio_visible")),
        "marvell_service_loader_visible": bool(chosen.get("marvell_service_loader_visible")),
    }
    decision, custom_next, reason, commands = no_nand_readiness.decide(facts)
    summary = {
        **STATIC_SAFETY,
        "timestamp_utc": utc_stamp(),
        "session_dir": str(session_dir),
        "samples_count": len(samples),
        "surface_seen": first_surface is not None,
        "first_surface_sample": first_surface.get("sample") if first_surface else None,
        "surface_kind": first_surface.get("surface_kind") if first_surface else None,
        **facts,
        "decision": decision,
        "custom_ram_boot_next": custom_next,
        "reason": reason,
        "recommended_commands": commands,
        "operator_next_step": no_nand_readiness.operator_next_step(decision),
        "samples": sample_records,
        "summary_json": str(session_dir / "summary.json"),
        "summary_md": str(session_dir / "summary.md"),
    }
    write_json(session_dir / "summary.json", summary)
    write_text(session_dir / "summary.md", markdown_summary(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="print static safety classification JSON and exit")
    parser.add_argument("--json", action="store_true", help="print summary JSON instead of the session path")
    parser.add_argument("--state-file", action="append", type=Path, default=[], help="saved state fixture; repeatable")
    parser.add_argument("--session-dir", type=Path, help="session output directory")
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between live samples")
    parser.add_argument("--duration", type=float, default=300.0, help="maximum seconds to watch live state")
    parser.add_argument("--probe-timeout", type=int, default=30, help="timeout for each live state probe")
    args = parser.parse_args()

    if args.check:
        print(json.dumps(STATIC_SAFETY, indent=2, sort_keys=True))
        return 0

    session_dir = (
        args.session_dir.expanduser()
        if args.session_dir
        else DEFAULT_SESSION_ROOT / f"{utc_stamp()}-surface-watch"
    )
    session_dir.mkdir(parents=True, exist_ok=True)

    if args.state_file:
        samples = collect_from_files(args.state_file)
    else:
        samples = collect_live(args.duration, args.interval, args.probe_timeout)
    if not samples:
        raise SystemExit("surface watch collected no samples")
    summary = summarize(samples, session_dir)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(session_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
