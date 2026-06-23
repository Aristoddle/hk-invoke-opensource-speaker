#!/usr/bin/env python3
"""Parse Harman Kardon Invoke OTA2 83_IMAGE container metadata.

This is intentionally read-only. It prints the header and partition records that
U-Boot's `l2nand 83` burns to NAND. It does not extract or write payload files.
"""
from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path
from typing import Any

MAGIC = 0xD2ADA3F1
ENTRY_SIZE = 0x40
ENTRY_START = 0x40


def read_cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def parse(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < ENTRY_START + ENTRY_SIZE:
        raise ValueError(f"{path} is too small to be an OTA83 image")

    header_words = struct.unpack_from("<16I", data, 0)
    if header_words[0] != MAGIC:
        raise ValueError(f"bad magic: 0x{header_words[0]:08x}, expected 0x{MAGIC:08x}")

    entry_count = header_words[7]
    entry_table_end = ENTRY_START + entry_count * ENTRY_SIZE
    if entry_table_end > len(data):
        raise ValueError(
            f"entry table exceeds image size: table_end=0x{entry_table_end:x}, "
            f"file_size=0x{len(data):x}"
        )

    # For the official Invoke OTA2 83_IMAGE, the payload begins immediately
    # after the 0x40-byte records. Header word 6 is 0x800, but treating that as
    # the payload start makes every per-partition CRC fail. Using table_end
    # yields exact CRC matches for all nine records.
    payload_start = entry_table_end
    cursor = payload_start

    entries: list[dict[str, Any]] = []
    for idx in range(entry_count):
        off = ENTRY_START + idx * ENTRY_SIZE
        name = read_cstr(data[off : off + 16])
        words = struct.unpack_from("<12I", data, off + 16)
        size = words[0] | (words[1] << 32)
        payload_offset = cursor
        payload_end = payload_offset + size
        if payload_end > len(data):
            raise ValueError(
                f"entry {idx} ({name}) exceeds image size: "
                f"payload_end=0x{payload_end:x}, file_size=0x{len(data):x}"
            )
        crc32_computed = zlib.crc32(data[payload_offset:payload_end]) & 0xFFFFFFFF
        entry = {
            "index": idx,
            "table_offset": off,
            "name": name,
            "image_size": size,
            "payload_offset": payload_offset,
            "payload_end": payload_end,
            "crc32": words[2],
            "crc32_computed": crc32_computed,
            "crc32_ok": crc32_computed == words[2],
            "version_a": words[3],
            "version_b": words[4],
            "nand_start_block": words[6],
            "nand_block_count": words[7],
            "data_type": words[8],
            "raw_words": list(words),
        }
        entries.append(entry)
        cursor = payload_end

    return {
        "path": str(path),
        "file_size": len(data),
        "magic": header_words[0],
        "header_words": list(header_words),
        "header_payload_hint": header_words[6],
        "entry_count": entry_count,
        "entry_table_start": ENTRY_START,
        "entry_table_end": entry_table_end,
        "payload_start": payload_start,
        "payload_end": cursor,
        "entries": entries,
        "total_declared_image_size": sum(e["image_size"] for e in entries),
        "declared_plus_payload_start": payload_start + sum(e["image_size"] for e in entries),
        "tail_bytes": len(data) - cursor,
        "all_crc32_ok": all(e["crc32_ok"] for e in entries),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path, help="Path to OTA2 83_IMAGE")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of table output")
    args = ap.parse_args()

    result = parse(args.image)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"path: {result['path']}")
    print(f"file_size: {result['file_size']}")
    print(f"magic: 0x{result['magic']:08x}")
    print(f"entry_count: {result['entry_count']}")
    print(f"entry_table_end: 0x{result['entry_table_end']:x}")
    print(f"header_payload_hint: 0x{result['header_payload_hint']:x}")
    print(f"payload_start: 0x{result['payload_start']:x}")
    print(f"payload_end: 0x{result['payload_end']:x}")
    print(f"total_declared_image_size: {result['total_declared_image_size']}")
    print(f"tail_bytes: {result['tail_bytes']}")
    print(f"all_crc32_ok: {result['all_crc32_ok']}")
    print()
    print("idx  name             offset      image_size   crc32       crc_ok  nand_start  blocks  type")
    for e in result["entries"]:
        print(
            f"{e['index']:>3}  {e['name']:<15}  0x{e['payload_offset']:07x}  "
            f"{e['image_size']:>10}  0x{e['crc32']:08x}  "
            f"{str(e['crc32_ok']):<6}  {e['nand_start_block']:>10}  "
            f"{e['nand_block_count']:>6}  {e['data_type']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
