#!/usr/bin/env python3
"""Analyze StockRoot/OTA83 bootimgs without touching hardware.

The speaker-P0 question is whether the StockRoot 83 image contains an
`81_IMAGE`-style kernel that can be reused for a RAM-only boot with a custom
initramfs. This tool is host-only: it reads either a full OTA83 `83_IMAGE` or an
already-extracted `bootimgs` partition, reports the inferred embedded segments,
and refuses to call the opaque kernel candidate bootm-compatible unless plain
uImage/zImage/ARM/FDT evidence is actually present.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

try:
    import parse_ota83
except ImportError:  # pragma: no cover - direct import is available in repo use.
    parse_ota83 = None  # type: ignore[assignment]

DEFAULT_STOCKROOT_83 = Path("/tmp/hk-invoke-stockroot-83/83_IMAGE")
UIMAGE_MAGIC_BE = b"\x27\x05\x19\x56"
UIMAGE_MAGIC_LE = b"\x56\x19\x05\x27"
ZIMAGE_MAGIC_LE = b"\x18\x28\x6f\x01"
FDT_MAGIC_BE = b"\xd0\x0d\xfe\xed"
ANDROID_BOOT_MAGIC = b"ANDROID!"
GZIP_MAGIC = b"\x1f\x8b\x08"
KERNEL_CANDIDATE_OFFSET = 0x20000


@dataclass(frozen=True)
class Segment:
    name: str
    offset: int
    size: int
    sha256: str
    description: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_all(data: bytes, needle: bytes, limit: int = 32) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    while True:
        off = data.find(needle, cursor)
        if off == -1:
            break
        offsets.append(off)
        cursor = off + 1
        if len(offsets) >= limit:
            break
    return offsets


def decompress_gzip_member(data: bytes, offset: int) -> tuple[int, int, str]:
    """Return (compressed_size, uncompressed_size, sha256) for gzip at offset."""
    obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
    uncompressed = obj.decompress(data[offset:]) + obj.flush()
    consumed = len(data[offset:]) - len(obj.unused_data)
    if consumed <= 0:
        raise ValueError(f"gzip candidate at 0x{offset:x} did not consume data")
    # Validate with gzip too so failures are explicit if zlib accepted garbage.
    gzip.decompress(data[offset : offset + consumed])
    return consumed, len(uncompressed), sha256_hex(uncompressed)


def is_full_ota83(path: Path) -> bool:
    if parse_ota83 is None or not path.is_file() or path.stat().st_size < 4:
        return False
    with path.open("rb") as fh:
        return fh.read(4) == struct.pack("<I", parse_ota83.MAGIC)


def bootimgs_from_full_ota83(path: Path, slot: str) -> tuple[bytes, dict[str, Any]]:
    if parse_ota83 is None:
        raise RuntimeError("parse_ota83 module is unavailable")
    parsed = parse_ota83.parse(path)
    if not parsed["all_crc32_ok"]:
        raise ValueError(f"OTA83 CRC validation failed for {path}")
    wanted = "bootimgs_B" if slot == "B" else "bootimgs"
    matches = [entry for entry in parsed["entries"] if entry["name"] == wanted]
    if not matches:
        raise ValueError(f"{path} has no {wanted!r} entry")
    entry = matches[0]
    data = path.read_bytes()[entry["payload_offset"] : entry["payload_end"]]
    return data, {"source_kind": "ota83", "ota83_entry": entry, "ota83_all_crc32_ok": True}


def infer_segments(data: bytes) -> tuple[list[Segment], dict[str, Any]]:
    if len(data) < KERNEL_CANDIDATE_OFFSET:
        raise ValueError(f"bootimgs partition too small: {len(data)} bytes")

    header_words = list(struct.unpack_from("<40I", data, 0))
    gzip_offsets = find_all(data, GZIP_MAGIC)
    gzip_members: list[dict[str, Any]] = []
    for off in gzip_offsets:
        try:
            compressed_size, uncompressed_size, uncompressed_sha256 = decompress_gzip_member(data, off)
            gzip_members.append(
                {
                    "offset": off,
                    "compressed_size": compressed_size,
                    "uncompressed_size": uncompressed_size,
                    "uncompressed_sha256": uncompressed_sha256,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as evidence, not hidden.
            gzip_members.append({"offset": off, "error": str(exc)})

    valid_gzip = [member for member in gzip_members if "compressed_size" in member]
    first_valid_gzip_offset = min((member["offset"] for member in valid_gzip), default=None)

    # The observed StockRoot final-update bootimgs has descriptor words at 0x28
    # and an opaque kernel-like block starting at 0x20000. Descriptor word 0x30
    # is the unaligned candidate length; word 0x34/0x3c/0x40 are aligned copies.
    descriptor = {
        "word_0x28_candidate_type": header_words[0x28 // 4],
        "word_0x2c_load_or_entry": header_words[0x2C // 4],
        "word_0x30_candidate_size": header_words[0x30 // 4],
        "word_0x34_candidate_aligned_size": header_words[0x34 // 4],
        "word_0x38_candidate_type_2": header_words[0x38 // 4],
        "word_0x3c_candidate_aligned_size_2": header_words[0x3C // 4],
        "word_0x40_candidate_aligned_size_3": header_words[0x40 // 4],
        "word_0x80_gzip_size_hint": header_words[0x80 // 4],
        "word_0x84_ramdisk_addr_hint": header_words[0x84 // 4],
        "word_0x88_ramdisk_limit_hint": header_words[0x88 // 4],
    }

    kernel_size = descriptor["word_0x30_candidate_size"]
    aligned_size = descriptor["word_0x34_candidate_aligned_size"]
    if kernel_size <= 0 or kernel_size > len(data) - KERNEL_CANDIDATE_OFFSET:
        # Fall back to everything before first valid gzip/padding if the header
        # is not the observed StockRoot shape.
        if first_valid_gzip_offset is None:
            kernel_size = len(data) - KERNEL_CANDIDATE_OFFSET
        else:
            kernel_size = max(0, first_valid_gzip_offset - KERNEL_CANDIDATE_OFFSET)
        aligned_size = kernel_size

    segments = [
        Segment(
            name="opaque-kernel-candidate",
            offset=KERNEL_CANDIDATE_OFFSET,
            size=kernel_size,
            sha256=sha256_hex(data[KERNEL_CANDIDATE_OFFSET : KERNEL_CANDIDATE_OFFSET + kernel_size]),
            description=(
                "Header-described kernel candidate. It is not automatically "
                "bootm-compatible; compatibility must be proven by image magic "
                "or live read-only boot evidence."
            ),
        )
    ]
    if first_valid_gzip_offset is not None:
        member = [m for m in valid_gzip if m["offset"] == first_valid_gzip_offset][0]
        segments.append(
            Segment(
                name="gzip-cpio-ou-recovery-ramdisk",
                offset=first_valid_gzip_offset,
                size=member["compressed_size"],
                sha256=sha256_hex(
                    data[first_valid_gzip_offset : first_valid_gzip_offset + member["compressed_size"]]
                ),
                description=(
                    "Embedded gzip cpio member. In the observed StockRoot image "
                    "this is the OU/recovery ramdisk with burning tools, not the "
                    "main ALSA userspace rootfs."
                ),
            )
        )

    candidate = data[KERNEL_CANDIDATE_OFFSET : KERNEL_CANDIDATE_OFFSET + kernel_size]
    compatibility = {
        "bootimgs_starts_with_uimage_magic": data.startswith(UIMAGE_MAGIC_BE) or data.startswith(UIMAGE_MAGIC_LE),
        "kernel_candidate_starts_with_uimage_magic": candidate.startswith(UIMAGE_MAGIC_BE)
        or candidate.startswith(UIMAGE_MAGIC_LE),
        "kernel_candidate_has_zimage_magic": bool(find_all(candidate, ZIMAGE_MAGIC_LE, limit=1)),
        "kernel_candidate_has_fdt_magic": bool(find_all(candidate, FDT_MAGIC_BE, limit=1)),
        "kernel_candidate_has_android_boot_magic": candidate.startswith(ANDROID_BOOT_MAGIC),
        "kernel_candidate_has_linux_version_string": b"Linux version" in candidate,
        "kernel_candidate_has_arm_nop_at_start": candidate.startswith(b"\x00\x00\xa0\xe1"),
        "bootm_compatible_plain_evidence": False,
    }
    compatibility["bootm_compatible_plain_evidence"] = any(
        compatibility[key]
        for key in [
            "bootimgs_starts_with_uimage_magic",
            "kernel_candidate_starts_with_uimage_magic",
            "kernel_candidate_has_zimage_magic",
            "kernel_candidate_has_android_boot_magic",
            "kernel_candidate_has_arm_nop_at_start",
        ]
    )

    evidence = {
        "file_size": len(data),
        "sha256": sha256_hex(data),
        "header_words_le_0x00_to_0x9c": header_words,
        "observed_descriptor": descriptor,
        "gzip_members": gzip_members,
        "magic_offsets": {
            "uimage_be": find_all(data, UIMAGE_MAGIC_BE),
            "uimage_le": find_all(data, UIMAGE_MAGIC_LE),
            "zimage_magic_le": find_all(data, ZIMAGE_MAGIC_LE),
            "fdt_be": find_all(data, FDT_MAGIC_BE),
            "android_boot": find_all(data, ANDROID_BOOT_MAGIC),
            "linux_version": find_all(data, b"Linux version"),
        },
        "kernel_candidate_aligned_size": aligned_size,
        "kernel_candidate_aligned_end": KERNEL_CANDIDATE_OFFSET + aligned_size,
        "first_valid_gzip_offset": first_valid_gzip_offset,
        "gap_aligned_kernel_to_first_gzip": (
            None
            if first_valid_gzip_offset is None
            else first_valid_gzip_offset - (KERNEL_CANDIDATE_OFFSET + aligned_size)
        ),
        "compatibility": compatibility,
        "safe_next_step": (
            "Do not wrap this opaque candidate as 81_IMAGE yet; first prove a "
            "plain bootable kernel source or a read-only U-Boot/NAND boot path."
            if not compatibility["bootm_compatible_plain_evidence"]
            else "Plain boot evidence exists; review manually before building a RAM-only artifact."
        ),
    }
    return segments, evidence


def analyze(path: Path, slot: str) -> dict[str, Any]:
    source_info: dict[str, Any] = {"path": str(path), "slot": slot}
    if is_full_ota83(path):
        data, ota_info = bootimgs_from_full_ota83(path, slot)
        source_info.update(ota_info)
    else:
        data = path.read_bytes()
        source_info["source_kind"] = "bootimgs-partition"

    segments, evidence = infer_segments(data)
    return {
        "source": source_info,
        "segments": [asdict(segment) for segment in segments],
        "evidence": evidence,
        "classification": "host-only-read-only-analysis",
        "safety": {
            "touches_device": False,
            "writes_device_storage": False,
            "opens_serial_or_usb": False,
            "persistent_commands": [],
        },
    }


def emit_text(result: dict[str, Any]) -> None:
    source = result["source"]
    evidence = result["evidence"]
    compat = evidence["compatibility"]
    print("HK Invoke StockRoot bootimgs analysis")
    print(f"classification: {result['classification']}")
    print(f"source: {source['path']} ({source['source_kind']}, slot={source['slot']})")
    if source.get("ota83_entry"):
        entry = source["ota83_entry"]
        print(
            f"ota83_entry: {entry['name']} offset=0x{entry['payload_offset']:x} "
            f"size={entry['image_size']} crc_ok={entry['crc32_ok']}"
        )
    print(f"bootimgs_size: {evidence['file_size']}")
    print(f"bootimgs_sha256: {evidence['sha256']}")
    print()
    print("segments:")
    for segment in result["segments"]:
        print(
            f"- {segment['name']}: offset=0x{segment['offset']:x} "
            f"size={segment['size']} sha256={segment['sha256']}"
        )
        print(f"  {segment['description']}")
    print()
    print("compatibility:")
    for key, value in compat.items():
        print(f"- {key}: {str(value).lower()}")
    print()
    print(f"safe_next_step: {evidence['safe_next_step']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_STOCKROOT_83,
        help=f"full OTA83 83_IMAGE or extracted bootimgs partition (default: {DEFAULT_STOCKROOT_83})",
    )
    parser.add_argument("--slot", choices=["A", "B"], default="A", help="bootimgs slot when PATH is full OTA83")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="assert the observed StockRoot final-update shape when the default image exists",
    )
    args = parser.parse_args()

    if not args.path.exists():
        if args.check and args.path == DEFAULT_STOCKROOT_83:
            print(f"skip: {DEFAULT_STOCKROOT_83} not present")
            return 0
        raise SystemExit(f"missing path: {args.path}")

    result = analyze(args.path, args.slot)
    if args.check:
        evidence = result["evidence"]
        segments = {segment["name"]: segment for segment in result["segments"]}
        assert evidence["file_size"] == 8651264, evidence["file_size"]
        assert segments["opaque-kernel-candidate"]["offset"] == 0x20000, segments
        assert segments["opaque-kernel-candidate"]["size"] == 0x4B5FFE, segments
        assert segments["gzip-cpio-ou-recovery-ramdisk"]["offset"] == 0x4E0000, segments
        assert segments["gzip-cpio-ou-recovery-ramdisk"]["size"] == 0x344AA1, segments
        assert evidence["compatibility"]["bootm_compatible_plain_evidence"] is False, evidence
        print("stockroot bootimgs host-only analysis validation passed")
        return 0

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        emit_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
