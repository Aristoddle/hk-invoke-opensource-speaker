#!/usr/bin/env python3
"""Probe an explicitly supplied HK Invoke Wi-Fi IP from the host.

This is an active network reachability probe. It intentionally stays on the
host side: no serial open, no ADB connect/shell, no USB boot payload, and no
device storage writes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
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
DEFAULT_ARTIFACT_ROOT = HOME / ".local/state/hk-invoke/sessions"
DEFAULT_PORTS = (22, 23, 80, 443, 5555, 8008, 8009, 8080, 10700)


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def run_cmd(cmd: list[str], timeout: int = 15) -> dict[str, Any]:
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


def parse_ports(value: str) -> list[int]:
    ports: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            port = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid port: {item}") from exc
        if port < 1 or port > 65535:
            raise argparse.ArgumentTypeError(f"port out of range: {port}")
        ports.append(port)
    if not ports:
        raise argparse.ArgumentTypeError("at least one port is required")
    return ports


def validate_ipv4(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid IP address: {value}") from exc
    if ip.version != 4:
        raise argparse.ArgumentTypeError("only IPv4 LAN candidates are supported")
    return str(ip)


def parse_ping(output: str) -> dict[str, Any]:
    tx_rx = re.search(
        r"(?P<tx>\d+)\s+packets transmitted,\s+(?P<rx>\d+)\s+packets "
        r"(?:received|recvd)",
        output,
    )
    loss = re.search(r"(?P<loss>[0-9.]+)%\s+packet loss", output)
    rtt = re.search(
        r"round-trip .* = "
        r"(?P<min>[0-9.]+)/(?P<avg>[0-9.]+)/(?P<max>[0-9.]+)/(?P<stddev>[0-9.]+)",
        output,
    )
    parsed: dict[str, Any] = {}
    if tx_rx:
        parsed["packets_transmitted"] = int(tx_rx.group("tx"))
        parsed["packets_received"] = int(tx_rx.group("rx"))
    if loss:
        parsed["packet_loss_pct"] = float(loss.group("loss"))
    if rtt:
        parsed["rtt_ms"] = {
            "min": float(rtt.group("min")),
            "avg": float(rtt.group("avg")),
            "max": float(rtt.group("max")),
            "stddev": float(rtt.group("stddev")),
        }
    parsed["reachable"] = parsed.get("packets_received", 0) > 0
    return parsed


def parse_nc(port: int, output: str, exit_code: int) -> dict[str, Any]:
    lowered = output.lower()
    if exit_code == 0 or "succeeded" in lowered or "open" in lowered:
        status = "open"
    elif "refused" in lowered:
        status = "refused"
    elif "timed out" in lowered or "timeout" in lowered:
        status = "timeout"
    elif "missing command" in lowered:
        status = "missing_nc"
    else:
        status = "closed_or_unknown"
    return {"port": port, "status": status, "exit_code": exit_code, "output": output}


def check_payload() -> dict[str, Any]:
    return {
        "classification": "active-network-reachability-probe",
        "requires_explicit_ip": True,
        "host_only": True,
        "sends_usb_payloads": False,
        "opens_serial": False,
        "runs_adb": False,
        "adb_connects": False,
        "writes_device_storage": False,
        "persistent_storage_changes_allowed": False,
        "commands": ["route get <ip>", "arp -n <ip>", "ping -c <n> <ip>", "nc -G <timeout> -vz <ip> <port>"],
        "default_ports": list(DEFAULT_PORTS),
    }


def artifact_dir(root: Path, stamp: str, ip: str) -> Path:
    slug = ip.replace(".", "-")
    base = root / f"{stamp}-wifi-ip-probe-{slug}"
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = Path(f"{base}-{counter}")
        counter += 1
    candidate.mkdir(parents=True)
    return candidate


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "HK Invoke Wi-Fi/IP probe",
        f"timestamp_utc={report['timestamp_utc']}",
        f"candidate_ip={report['candidate_ip']}",
        f"classification={report['classification']} host-only",
        "safety=no serial, no adb shell/connect, no usb payloads, no device storage writes",
        f"ping_reachable={str(report['ping'].get('reachable')).lower()}",
    ]
    if "packets_received" in report["ping"]:
        lines.append(
            "ping_packets="
            f"{report['ping'].get('packets_received')}/{report['ping'].get('packets_transmitted')}"
        )
    open_ports = [p["port"] for p in report["tcp_ports"] if p["status"] == "open"]
    refused_ports = [p["port"] for p in report["tcp_ports"] if p["status"] == "refused"]
    lines.append(f"tcp_open_ports={open_ports}")
    lines.append(f"tcp_refused_ports={refused_ports}")
    lines.append("")
    for key in ("route", "arp_before", "ping_command", "arp_after"):
        result = report["commands"][key]
        lines.append(f"== {key}: {' '.join(result['cmd'])} ==")
        lines.append(result["output"].rstrip())
        lines.append("")
    lines.append("== tcp port checks ==")
    for item in report["tcp_ports"]:
        lines.append(f"port={item['port']} status={item['status']}")
        if item["output"].strip():
            lines.append(item["output"].rstrip())
    lines.append("")
    return "\n".join(lines)


def probe(args: argparse.Namespace) -> dict[str, Any]:
    stamp = utc_stamp()
    ip = args.ip
    ports = args.ports
    route = run_cmd(["route", "get", ip], timeout=10)
    arp_before = run_cmd(["arp", "-n", ip], timeout=10)
    ping_cmd = run_cmd(["ping", "-c", str(args.ping_count), ip], timeout=max(10, args.ping_count * 5))
    arp_after = run_cmd(["arp", "-n", ip], timeout=10)

    tcp_ports: list[dict[str, Any]] = []
    for port in ports:
        result = run_cmd(["nc", "-G", str(args.tcp_timeout), "-vz", ip, str(port)], timeout=args.tcp_timeout + 3)
        tcp_ports.append(parse_nc(port, result["output"], result["exit_code"]) | {"duration_ms": result["duration_ms"]})

    report: dict[str, Any] = {
        "timestamp_utc": stamp,
        "candidate_ip": ip,
        "classification": "active-network-reachability-probe",
        "host_only": True,
        "sends_usb_payloads": False,
        "opens_serial": False,
        "runs_adb": False,
        "adb_connects": False,
        "writes_device_storage": False,
        "persistent_storage_changes_allowed": False,
        "commands": {
            "route": route,
            "arp_before": arp_before,
            "ping_command": ping_cmd,
            "arp_after": arp_after,
        },
        "ping": parse_ping(ping_cmd["output"]),
        "tcp_ports": tcp_ports,
    }
    report["network_presence_alive"] = bool(report["ping"].get("reachable"))
    report["control_tcp_ports_open"] = [p["port"] for p in tcp_ports if p["status"] == "open"]
    if not args.no_artifact:
        out_dir = artifact_dir(args.artifact_root, stamp, ip)
        report["artifact_dir"] = str(out_dir)
        (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        (out_dir / "probe.txt").write_text(render_text(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", type=validate_ipv4, help="explicit Invoke LAN IPv4 candidate")
    parser.add_argument("--ports", type=parse_ports, default=list(DEFAULT_PORTS), help="comma-separated TCP ports")
    parser.add_argument("--ping-count", type=int, default=3)
    parser.add_argument("--tcp-timeout", type=int, default=2)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--no-artifact", action="store_true")
    parser.add_argument("--check", action="store_true", help="print static safety metadata and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(check_payload(), indent=2, sort_keys=True))
        return 0
    if not args.ip:
        parser.error("--ip is required unless --check is used")
    if args.ping_count < 1:
        parser.error("--ping-count must be >= 1")
    if args.tcp_timeout < 1:
        parser.error("--tcp-timeout must be >= 1")
    report = probe(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
