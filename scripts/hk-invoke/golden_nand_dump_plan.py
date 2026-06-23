#!/usr/bin/env python3
"""Generate a read-only golden NAND dump preflight plan for HK Invoke.

This is host-side planning only. It does not open USB/serial, does not read the
physical device, and never writes NAND/eMMC/SPI/U-Boot environment state. The
first generated device script is intentionally *preflight-only*: it inventories
`/proc/mtd` and tool availability into `/tmp` so a later operator-present gate
can decide whether a real `nanddump --oob` transfer is safe.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_OUT_ROOT = Path.home() / ".local/state/hk-invoke/golden-nand-dump-plans"
MTD_RE = re.compile(r'^(mtd\d+):\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"([^"]+)"')
FORBIDDEN_EXEC_TOKENS = (
    "l2nand",
    "tftp2nand",
    "nanderase",
    "nand write",
    "nand erase",
    "nandwr",
    "nandmarkbad",
    "nandverify",
    "flash_erase",
    "flashcp",
    "nandwrite",
    "saveenv",
    "fw_setenv",
    "ubiformat",
)
KNOWN_PROC_MTD = """dev:    size   erasesize  name
mtd0: 00020000 00020000 "block0"
mtd1: 00100000 00020000 "prebootloader"
mtd2: 00400000 00020000 "TZ"
mtd3: 00400000 00020000 "TZ-B"
mtd4: 00080000 00020000 "postbootloader"
mtd5: 00080000 00020000 "postbootloader-B"
mtd6: 00800000 00020000 "kernel"
mtd7: 06900000 00020000 "rootfs"
mtd8: 07260000 00020000 "cache"
mtd9: 00f00000 00020000 "recovery"
mtd10: 00080000 00020000 "fts"
mtd11: 00200000 00020000 "factory_store"
mtd12: 00100000 00020000 "bbt"
mtd13: 10000000 00020000 "mv_nand"
"""


@dataclass(frozen=True)
class MtdPartition:
    dev: str
    size_hex: str
    erase_size_hex: str
    name: str

    @property
    def size_bytes(self) -> int:
        return int(self.size_hex, 16)

    @property
    def erase_size_bytes(self) -> int:
        return int(self.erase_size_hex, 16)

    @property
    def index(self) -> int:
        return int(self.dev.removeprefix("mtd"))

    @property
    def is_boot_critical(self) -> bool:
        return 0 <= self.index <= 5

    @property
    def is_aggregate(self) -> bool:
        return self.name == "mv_nand"


def utcish_stamp() -> str:
    # Keep deterministic enough for tests while still making unique artifact dirs.
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_proc_mtd(text: str) -> list[MtdPartition]:
    parts: list[MtdPartition] = []
    for raw in text.splitlines():
        match = MTD_RE.match(raw.strip())
        if not match:
            continue
        parts.append(
            MtdPartition(
                dev=match.group(1),
                size_hex=match.group(2).lower(),
                erase_size_hex=match.group(3).lower(),
                name=match.group(4),
            )
        )
    if not parts:
        raise ValueError("no /proc/mtd partition rows parsed")
    expected = list(range(len(parts)))
    actual = [part.index for part in parts]
    if actual != expected:
        raise ValueError(f"non-contiguous MTD indexes: {actual}, expected {expected}")
    return parts


def active_script_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def assert_no_forbidden_exec(script_text: str) -> None:
    joined = "\n".join(active_script_lines(script_text)).lower()
    for token in FORBIDDEN_EXEC_TOKENS:
        if token in joined:
            raise AssertionError(f"forbidden executable token in generated script: {token}")


def device_preflight_script() -> str:
    return r'''#!/bin/sh
# HK Invoke golden NAND dump PRE-FLIGHT ONLY.
# Classification: RAM/tmpfs-only read-only inventory. This script does not dump
# NAND and does not run any NAND/eMMC/SPI/env write command.
set -eu
bb=/bin/busybox
out=/tmp/hk-golden-nand-preflight.$$
"$bb" mkdir -p "$out"
{
  echo "## uname"
  "$bb" uname -a || true
  echo "## cmdline"
  "$bb" cat /proc/cmdline || true
  echo "## proc-mtd"
  "$bb" cat /proc/mtd || true
  echo "## mounts"
  "$bb" mount || true
  echo "## mtd devices"
  "$bb" ls -l /dev/mtd* 2>/dev/null || true
  echo "## tool paths"
  command -v nanddump || true
  command -v sha256sum || true
  command -v nc || true
  command -v busybox || true
  echo "## nanddump help"
  if command -v nanddump >/dev/null 2>&1; then
    nanddump --help 2>&1 || true
  else
    echo "nanddump_missing"
  fi
} > "$out/preflight.txt" 2>&1
"$bb" cat "$out/preflight.txt"
echo "preflight_written=$out/preflight.txt"
'''


def plan_markdown(parts: list[MtdPartition]) -> str:
    part_rows = "\n".join(
        f"| `{p.dev}` | `{p.name}` | `{p.size_hex}` | {p.size_bytes} | "
        f"`{p.erase_size_hex}` | {'yes' if p.is_boot_critical else 'no'} | "
        f"{'yes' if p.is_aggregate else 'no'} |"
        for p in parts
    )
    non_aggregate_total = sum(p.size_bytes for p in parts if not p.is_aggregate)
    aggregate_total = sum(p.size_bytes for p in parts if p.is_aggregate)
    return f"""# HK Invoke golden NAND dump preflight plan

Classification: **host-generated plan plus RAM/tmpfs-only read-only preflight**.
No hardware is touched by generating this artifact. The generated device script
only writes under `/tmp/hk-golden-nand-preflight` and only reads `/proc`, mounts,
`/dev/mtd*` metadata, and tool help.

## Current known MTD map

| Device | Name | Size hex | Size bytes | Erase size | Boot-critical mtd0-mtd5 | Aggregate |
| --- | --- | ---: | ---: | ---: | --- | --- |
{part_rows}

Non-aggregate byte total: `{non_aggregate_total}`.
Aggregate `mv_nand` byte total: `{aggregate_total}`.

## Why this plan exists

Before any persistent Path B/data-overlay work, capture a golden dump that can be
verified and preserved off-device. The current artifact is **not** that dump; it
is only the preflight that proves the running RAM shell has the right read-only
capabilities and transfer path.

## Execution gates

1. Joe/operator present and explicitly says the exact gate is open.
2. Device is in the known generated no-NAND RAM shell, not stock full init.
3. Bluetooth baseline/recovery expectation is recorded before the experiment.
4. Run `device-preflight-readonly.sh` only; preserve its output in the session.
5. Continue to a real dump only after reviewing whether `nanddump`, hashing, and
   a host transfer path are actually present.

## Dump policy for the later gate

- Preferred reader: `nanddump --oob` from mtd-utils, after confirming its exact
  help syntax on this device.
- Preferred destination: stream directly to the Mac over an explicitly selected
  transport, not to NAND-backed paths. RAM `/tmp` is allowed only for small
  preflight logs, not for the full dump unless free RAM is proven sufficient.
- Dump `mtd0`-`mtd12` as individual partitions. Treat `mtd13`/`mv_nand` as an
  optional aggregate/full-chip cross-check because it duplicates the individual
  partition contents and is large.
- For every captured file: verify expected byte size, compute at least two host
  hashes, and store manifest + transcript under `~/.local/state/hk-invoke/`.

## Hard no-go tokens

The later dump gate must still refuse: `l2nand`, `tftp2nand`, `nanderase`,
`nand write`, `nand erase`, `nandwr`, `nandmarkbad`, `nandverify`, `flash_erase`,
`flashcp`, `nandwrite`, `saveenv`, `fw_setenv`, and `ubiformat`.

## Current conclusion

Do **not** ask Joe for service mode solely for this artifact. Use this plan to
make the next operator-present dump gate precise and auditable.
"""


def manifest(parts: list[MtdPartition]) -> dict[str, Any]:
    return {
        "classification": "host-only plan; generated device preflight is RAM/tmpfs-only read-only inventory",
        "touches_device_when_generated": False,
        "device_preflight_writes": ["/tmp/hk-golden-nand-preflight.$$/preflight.txt"],
        "persistent_device_writes": [],
        "forbidden_exec_tokens": list(FORBIDDEN_EXEC_TOKENS),
        "partition_count": len(parts),
        "partitions": [
            {
                **asdict(part),
                "size_bytes": part.size_bytes,
                "erase_size_bytes": part.erase_size_bytes,
                "index": part.index,
                "boot_critical_mtd0_mtd5": part.is_boot_critical,
                "aggregate": part.is_aggregate,
            }
            for part in parts
        ],
        "default_dump_scope_later_gate": [part.dev for part in parts if not part.is_aggregate],
        "optional_aggregate_later_gate": [part.dev for part in parts if part.is_aggregate],
        "next_operator_phrase": "NOW: keep device powered; starting read-only golden NAND dump preflight",
    }


def write_plan(out_dir: Path, parts: list[MtdPartition]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    preflight = device_preflight_script()
    assert_no_forbidden_exec(preflight)
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest(parts), indent=2, sort_keys=True) + "\n")
    (out_dir / "PLAN.md").write_text(plan_markdown(parts))
    preflight_path = out_dir / "device-preflight-readonly.sh"
    preflight_path.write_text(preflight)
    preflight_path.chmod(0o755)
    (out_dir / "README.md").write_text(
        "# HK Invoke golden NAND dump preflight\n\n"
        "Run nothing on-device until Joe explicitly opens the preflight gate. "
        "This artifact contains no dump executor; it only makes the next read-only "
        "capability check precise.\n"
    )
    return out_dir


def load_mtd_text(path: Path | None) -> str:
    if path is None:
        return KNOWN_PROC_MTD
    return path.read_text()


def run_check() -> None:
    parts = parse_proc_mtd(KNOWN_PROC_MTD)
    assert len(parts) == 14, len(parts)
    assert parts[0].dev == "mtd0" and parts[0].name == "block0", parts[0]
    assert parts[5].is_boot_critical and parts[5].name == "postbootloader-B", parts[5]
    assert parts[13].is_aggregate and parts[13].size_bytes == 0x10000000, parts[13]
    with tempfile.TemporaryDirectory() as tmp:
        out = write_plan(Path(tmp) / "golden", parts)
        preflight = (out / "device-preflight-readonly.sh").read_text()
        assert_no_forbidden_exec(preflight)
        assert "nanddump --help" in preflight, preflight
        rendered = json.loads((out / "MANIFEST.json").read_text())
        assert rendered["persistent_device_writes"] == [], rendered
        assert rendered["default_dump_scope_later_gate"] == [f"mtd{i}" for i in range(13)], rendered
        assert rendered["optional_aggregate_later_gate"] == ["mtd13"], rendered
    print("golden NAND dump preflight plan validation passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proc-mtd", type=Path, help="parse a captured /proc/mtd text file")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT, help="host artifact root")
    parser.add_argument("--json", action="store_true", help="print manifest JSON")
    parser.add_argument("--check", action="store_true", help="run static validation and exit")
    args = parser.parse_args()

    if args.check:
        run_check()
        return 0

    parts = parse_proc_mtd(load_mtd_text(args.proc_mtd))
    out_dir = args.out_root / utcish_stamp()
    write_plan(out_dir, parts)
    if args.json:
        print(json.dumps({"artifact": str(out_dir), **manifest(parts)}, indent=2, sort_keys=True))
    else:
        print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
