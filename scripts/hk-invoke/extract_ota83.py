#!/usr/bin/env python3
"""Safely extract partition payloads from a Harman Kardon Invoke OTA2 83_IMAGE.

This is an offline host-side parser/extractor. It never talks to the Invoke and
never writes firmware. The output directory must be absent or empty; files are
created with exclusive-open semantics to avoid clobbering prior evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import parse_ota83


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return cleaned or "unnamed"


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def write_exclusive(path: Path, data: bytes) -> str:
    with path.open("xb") as fh:
        fh.write(data)
    return sha256_bytes(data)


def extract(image: Path, out_dir: Path) -> dict[str, Any]:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"output directory exists and is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    parsed = parse_ota83.parse(image)
    if not parsed["all_crc32_ok"]:
        bad = [e["name"] for e in parsed["entries"] if not e["crc32_ok"]]
        raise ValueError(f"refusing to extract; CRC mismatch for: {', '.join(bad)}")

    data = image.read_bytes()
    files: list[dict[str, Any]] = []

    for entry in parsed["entries"]:
        payload = data[entry["payload_offset"] : entry["payload_end"]]
        rel = f"{entry['index']:02d}_{safe_name(entry['name'])}.bin"
        out_path = out_dir / rel
        digest = write_exclusive(out_path, payload)
        files.append(
            {
                "kind": "partition",
                "name": entry["name"],
                "path": rel,
                "size": len(payload),
                "sha256": digest,
                "payload_offset": entry["payload_offset"],
                "payload_end": entry["payload_end"],
                "crc32": entry["crc32"],
                "crc32_ok": entry["crc32_ok"],
                "nand_start_block": entry["nand_start_block"],
                "nand_block_count": entry["nand_block_count"],
                "data_type": entry["data_type"],
            }
        )

    if parsed["tail_bytes"]:
        tail = data[parsed["payload_end"] :]
        rel = f"{len(parsed['entries']):02d}_metadata_tail.bin"
        digest = write_exclusive(out_dir / rel, tail)
        files.append(
            {
                "kind": "tail",
                "name": "metadata_tail",
                "path": rel,
                "size": len(tail),
                "sha256": digest,
                "payload_offset": parsed["payload_end"],
                "payload_end": len(data),
            }
        )

    manifest = {
        "image": str(image),
        "output_dir": str(out_dir),
        "parsed": parsed,
        "files": files,
    }

    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    write_exclusive(out_dir / "manifest.json", manifest_bytes)

    lines = [f"{f['sha256']}  {f['path']}" for f in files]
    lines.append(f"{sha256_bytes(manifest_bytes)}  manifest.json")
    write_exclusive(out_dir / "SHA256SUMS", ("\n".join(lines) + "\n").encode())

    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path, help="Path to OTA2 83_IMAGE")
    ap.add_argument(
        "output_dir",
        type=Path,
        help="Absent or empty output directory for extracted partition payloads",
    )
    args = ap.parse_args()

    manifest = extract(args.image, args.output_dir)
    print(f"extracted {len(manifest['files'])} files to {args.output_dir}")
    for item in manifest["files"]:
        print(f"{item['path']}\t{item['size']}\t{item['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
