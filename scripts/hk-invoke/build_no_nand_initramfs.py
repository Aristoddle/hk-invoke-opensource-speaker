#!/usr/bin/env python3
"""Build a throwaway no-NAND HK Invoke initramfs work image.

Offline host-side only. This script copies an already-extracted 82_IMAGE rootfs,
replaces /init in the copy with a conservative probe init, repacks it as a newc
cpio gzip image, and creates a separate hk_usb_boot serving directory that uses
the rebuilt 82_IMAGE. It never opens USB, never talks to the device, and never
modifies the source rootfs or the base image directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HOME = Path.home()
DEFAULT_ROOTFS = (
    HOME
    / ".local/state/hk-invoke/recovery-baselines/20260619T012008/extracted/rootfs-82"
)
DEFAULT_IMAGE_DIR = Path("/tmp/hk-invoke-ota2-work-current")
DEFAULT_OUT_ROOT = HOME / ".local/state/hk-invoke/no-nand-initramfs"
DEFAULT_OTA83_ROOTFS = (
    HOME / ".local/state/hk-invoke/ota83-extracts/20260619T012626/06_rootfs.bin"
)

REQUIRED_ROOTFS_PATHS = [
    "bin/busybox",
    "bin/sh",
    "sbin/ueventd",
    "sbin/adbd-root",
    "etc/hotplug/wifi-fw.sh",
]
REQUIRED_BASE_IMAGES = [
    "bcm_erom.bin.usb",
    "09_IMAGE",
    "sysinit.img",
    "bootloader.img",
    "drm_erom.img",
    "06_IMAGE",
    "79_IMAGE",
    "81_IMAGE",
]
FORBIDDEN_79_TOKENS = [
    "l2nand",
    "tftp2nand",
    "usb2nand",
    "tftp2emmc",
    "usb2emmc",
    "nanderase",
    "nand write",
    "nand erase",
    "mmc write",
    "erase",
    "protect",
    "run upgrade",
    "saveenv",
]
OTA83_STAGE_MEMBERS = {
    "modules": {
        "mlan": "lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/wlan_sd8887/mlan.ko",
        "sd8xxx": "lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/wlan_sd8887/sd8xxx.ko",
        "bt8xxx": "lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/bt_sd8887/bt8xxx.ko",
        "btmrvl": "lib/modules/3.8.13-yocto-standard/kernel/drivers/bluetooth/btmrvl.ko",
    },
    "firmware": {
        "wlan_fw": "lib/firmware/mrvl/sd8887_wlan_a2_p78.bin",
        "bt_fw": "lib/firmware/mrvl/sd8887_bt_a2.bin",
        "bt_fw_new": "lib/firmware/mrvl/sd8887_bt_a2_new.bin",
        "cal_ls9ad": "lib/firmware/mrvl/WlanCalData_ext-LS9AD-20160725.conf",
        "tx_power": "lib/firmware/mrvl/txpwrlimit_cfg_8887.bin",
    },
    "audio_userspace": {
        "asound_conf": "etc/asound.conf",
        "asound_product": "etc/asound-product.conf",
        "aplay": "usr/bin/aplay",
        "arecord": "usr/bin/arecord",
        "libasound": "usr/lib/libasound.so.2.0.0",
    },
}


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def allocate_artifact_dir(out_root: Path, stamp: str) -> Path:
    """Return a non-existing artifact directory path for a timestamp stamp."""
    candidate = out_root / stamp
    if not candidate.exists():
        return candidate
    suffix = 1
    while True:
        candidate = out_root / f"{stamp}-{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def create_artifact_dir(out_root: Path, stamp: str) -> Path:
    """Atomically create and return a collision-safe artifact directory."""
    out_root.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        name = stamp if suffix == 0 else f"{stamp}-{suffix}"
        candidate = out_root / name
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            suffix += 1


def file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return None


def require_tool(name: str) -> str | None:
    return shutil.which(name)


def read_lower(path: Path) -> str:
    try:
        return path.read_text(errors="ignore").lower()
    except FileNotFoundError:
        return ""


def archive_entries(archive: Path) -> set[str]:
    """List archive members read-only via 7z."""
    if not archive.exists() or shutil.which("7z") is None:
        return set()
    proc = subprocess.run(
        ["7z", "l", "-ba", str(archive)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        return set()
    entries: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 6:
            entries.add(parts[-1].lstrip("/"))
    return entries


def safe_stage_member(member: str) -> None:
    path = Path(member)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe OTA83 member path: {member}")


def read_archive_member(archive: Path, member: str) -> bytes:
    safe_stage_member(member)
    proc = subprocess.run(
        ["7z", "e", "-so", str(archive), member],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"failed to read OTA83 member {member}: {proc.stderr.decode(errors='replace')}"
        )
    return proc.stdout


def ota83_stage_manifest(ota83_rootfs: Path) -> dict[str, Any]:
    entries = archive_entries(ota83_rootfs)
    available = {
        group: {name: rel in entries for name, rel in members.items()}
        for group, members in OTA83_STAGE_MEMBERS.items()
    }
    required_ready = (
        bool(ota83_rootfs.exists())
        and bool(shutil.which("7z"))
        and available["modules"].get("mlan", False)
        and available["modules"].get("sd8xxx", False)
        and available["modules"].get("bt8xxx", False)
        and available["firmware"].get("wlan_fw", False)
        and available["firmware"].get("bt_fw", False)
    )
    return {
        "archive": str(ota83_rootfs),
        "exists": ota83_rootfs.exists(),
        "inspection_tool": shutil.which("7z"),
        "stage_prefix": "/ota83-stage",
        "member_count": len(entries),
        **available,
        "ready": required_ready,
    }


def path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def path_separation_errors(rootfs: Path, image_dir: Path, out_root: Path) -> list[str]:
    resolved_rootfs = rootfs.resolve(strict=False)
    resolved_image_dir = image_dir.resolve(strict=False)
    resolved_out_root = out_root.resolve(strict=False)
    errors: list[str] = []
    for name, input_path in (
        ("rootfs", resolved_rootfs),
        ("image_dir", resolved_image_dir),
    ):
        if resolved_out_root == input_path or path_contains(
            input_path, resolved_out_root
        ):
            errors.append(
                f"out-root {resolved_out_root} must not be inside {name} {input_path}"
            )
        if path_contains(resolved_out_root, input_path):
            errors.append(
                f"out-root {resolved_out_root} must not contain {name} {input_path}"
            )
    return errors


def build_manifest(
    rootfs: Path,
    image_dir: Path,
    out_root: Path,
    ota83_rootfs: Path,
    stage_ota83_connectivity: bool,
) -> dict[str, Any]:
    required_rootfs = {rel: (rootfs / rel).exists() for rel in REQUIRED_ROOTFS_PATHS}
    base_images = {name: file_size(image_dir / name) for name in REQUIRED_BASE_IMAGES}
    safe_79 = True
    matched_79: list[str] = []
    text_79 = read_lower(image_dir / "79_IMAGE")
    for token in FORBIDDEN_79_TOKENS:
        if token in text_79:
            safe_79 = False
            matched_79.append(token)
    tools = {"cpio": require_tool("cpio"), "gzip": require_tool("gzip")}
    separation_errors = path_separation_errors(rootfs, image_dir, out_root)
    ota83_stage = ota83_stage_manifest(ota83_rootfs)
    ota83_stage_ready = (not stage_ota83_connectivity) or ota83_stage["ready"]
    return {
        "classification": "offline-host-no-nand-initramfs-build",
        "rootfs": str(rootfs),
        "base_image_dir": str(image_dir),
        "out_root": str(out_root),
        "ota83_rootfs": str(ota83_rootfs),
        "ota83_stage_requested": stage_ota83_connectivity,
        "ota83_stage_ready": ota83_stage_ready,
        "ota83_stage": ota83_stage,
        "path_separation_ok": not separation_errors,
        "path_separation_errors": separation_errors,
        "required_rootfs_paths": required_rootfs,
        "base_images": base_images,
        "base_79_image_safe": safe_79,
        "base_79_image_forbidden_matches": matched_79,
        "tools": tools,
        "ready": all(required_rootfs.values())
        and all(base_images[name] for name in REQUIRED_BASE_IMAGES)
        and safe_79
        and all(tools.values())
        and ota83_stage_ready
        and not separation_errors,
    }


def hardware_inventory_script() -> str:
    return r"""#!/bin/sh
# RAM-only hardware/library inventory for HK Invoke no-NAND probes.
# Writes only /tmp and reads /proc, /sys, and the initramfs filesystem.
# Use /bin/busybox applets explicitly because this minimal rootfs may not expose
# find/grep/sort/head/tail/cat/ls/ifconfig/route as standalone command names.

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/home/galois/bin
export PATH
bb=/bin/busybox
out=/tmp/no-nand-hardware-inventory.txt

section() {
  echo
  echo "## $*"
}
run() {
  echo
  echo "# $*"
  $bb sh -c "$*" 2>&1 || true
}

{
  section identity
  run "$bb date"
  run "$bb uname -a"
  run "$bb cat /proc/cmdline"
  run "$bb cat /proc/cpuinfo"

  section memory-and-address-space
  run "$bb cat /proc/meminfo"
  run "$bb cat /proc/iomem"
  run "$bb cat /proc/interrupts"

  section storage-map-read-only
  run "$bb cat /proc/mtd"
  run "$bb cat /proc/partitions"
  run "$bb mount"

  section device-nodes
  run "$bb ls -la /dev"
  run "$bb ls -la /dev/snd"
  run "$bb ls -la /dev/input"

  section usb-gadget
  run "$bb find /sys/class/android_usb -maxdepth 4 -type f -print -exec $bb cat {} \;"
  run "$bb find /sys/class/udc /sys/kernel/config/usb_gadget -maxdepth 4 -type f -print -exec $bb cat {} \;"

  section audio
  run "$bb cat /proc/asound/cards"
  run "$bb cat /proc/asound/pcm"
  run "$bb find /sys/class/sound -maxdepth 4 -type f -print -exec $bb cat {} \;"

  section network-and-sdio
  run "$bb ifconfig -a"
  run "$bb route -n"
  run "$bb find /sys/bus/sdio/devices -maxdepth 4 -type f -print -exec $bb cat {} \;"
  run "$bb find /sys/class/net -maxdepth 3 -type f -print -exec $bb cat {} \;"

  section modules-and-drivers
  run "$bb cat /proc/modules"
  run "$bb find /lib/modules -type f | $bb sort"

  section invoke-userland-libraries
  run "$bb find /home/galois -maxdepth 3 -type f | $bb sort | $bb head -400"
  run "$bb find /home/galois/lib /usr/lib /lib -maxdepth 2 -type f | $bb sort"
  run "$bb find /etc -maxdepth 3 -type f | $bb sort"

  section processes
  run "$bb ps"
} >"$out" 2>&1

$bb cat "$out" 2>/dev/null || true
"""


def audio_inventory_script() -> str:
    return r"""#!/bin/sh
# Read-only audio-focused inventory for HK Invoke no-NAND probes.
# Writes only /tmp and reads /proc, /sys, /dev, and rootfs configuration files.
# Use /bin/busybox applets explicitly because this minimal rootfs may not expose
# find/grep/sort/head/tail/cat/ls/ifconfig/route as standalone command names.

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/home/galois/bin
export PATH
bb=/bin/busybox
out=/tmp/no-nand-audio-inventory.txt

section() {
  echo
  echo "## $*"
}
run() {
  echo
  echo "# $*"
  $bb sh -c "$*" 2>&1 || true
}

{
  section identity
  run "$bb date"
  run "$bb uname -a"
  run "$bb cat /proc/cmdline"

  section kernel-audio-surfaces
  run "$bb ls -la /dev/snd"
  run "$bb ls -la /sys/class/sound"
  run "$bb cat /proc/asound/cards"
  run "$bb cat /proc/asound/pcm"
  run "$bb cat /proc/asound/devices"
  run "$bb find /proc/asound -maxdepth 3 -type f -print -exec $bb cat {} \;"
  run "$bb find /sys/class/sound -maxdepth 4 -type f -print -exec $bb cat {} \;"

  section codec-and-board-hints
  run "$bb dmesg | $bb grep -i -E 'alsa|asoc|audio|codec|i2s|pcm|snd|wm8904' | $bb tail -200"
  run "$bb find /sys/bus/platform/devices -maxdepth 3 -type f -print | $bb grep -i -E 'audio|codec|i2s|pcm|snd|wm8904' | $bb sort"
  run "$bb find /proc/device-tree -maxdepth 6 -type f -print | $bb grep -i -E 'audio|codec|i2s|pcm|snd|wm8904' | $bb sort"

  section userspace-audio-tools
  run "command -v aplay"
  run "command -v arecord"
  run "command -v amixer"
  run "command -v alsactl"
  run "command -v tinyplay"
  run "command -v tinycap"
  run "command -v tinymix"
  run "aplay -l"
  run "arecord -l"
  run "amixer controls"

  section audio-configuration-files
  run "$bb find /etc /usr/share /home/galois -maxdepth 4 -type f | $bb grep -i -E 'alsa|asound|audio|codec|sound|wm8904' | $bb sort"
  run "$bb cat /etc/asound.conf"
  run "$bb cat /home/galois/asound-product.conf"
} >"$out" 2>&1

$bb cat "$out" 2>/dev/null || true
"""


def network_inventory_script() -> str:
    return r"""#!/bin/sh
# Read-only network/USB/SDIO inventory for HK Invoke no-NAND probes.
# Writes only /tmp and reads /proc, /sys, and rootfs configuration files.
# Use /bin/busybox applets explicitly because this minimal rootfs may not expose
# find/grep/sort/head/tail/cat/ls/ifconfig/route as standalone command names.

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/home/galois/bin
export PATH
bb=/bin/busybox
out=/tmp/no-nand-network-inventory.txt

section() {
  echo
  echo "## $*"
}
run() {
  echo
  echo "# $*"
  $bb sh -c "$*" 2>&1 || true
}

{
  section identity
  run "$bb date"
  run "$bb uname -a"
  run "$bb cat /proc/cmdline"

  section kernel-network-state
  run "$bb cat /proc/net/dev"
  run "$bb cat /proc/net/route"
  run "$bb cat /proc/net/arp"
  run "$bb ifconfig -a"
  run "$bb route -n"

  section usb-gadget-surfaces
  run "$bb find /sys/class/android_usb -maxdepth 5 -type f -print -exec $bb cat {} \;"
  run "$bb find /sys/class/udc /sys/kernel/config/usb_gadget -maxdepth 5 -type f -print -exec $bb cat {} \;"

  section net-sysfs
  run "$bb find /sys/class/net -maxdepth 4 -type f -print -exec $bb cat {} \;"

  section sdio-wifi-bt-surfaces
  run "$bb find /sys/bus/sdio/devices -maxdepth 5 -type f -print -exec $bb cat {} \;"
  run "$bb dmesg | $bb grep -i -E 'sdio|sd8|sd88|wlan|wifi|80211|marvell|bt8|bluetooth' | $bb tail -200"

  section network-config-files
  run "$bb find /etc /home/galois -maxdepth 4 -type f | $bb grep -i -E 'wifi|wpa|dhcp|network|interfaces' | $bb sort"
  run "$bb cat /etc/resolv.conf"
  run "$bb cat /etc/tmpfs/resolv.conf"
  run "$bb cat /etc/network/interfaces"
} >"$out" 2>&1

$bb cat "$out" 2>/dev/null || true
"""


def no_nand_init() -> str:
    return r"""#!/bin/busybox sh
# HK Invoke no-NAND probe init.
# Classification: RAM-only when booted from USB-loaded initramfs.
# This script intentionally does not run /sbin/init, /etc/init.d/rcS, mount_part,
# /home/galois/run.sh, or any NAND/eMMC/SPI writer.

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/home/galois/bin
export PATH

log() {
  echo "[no-nand-init] $*" >/dev/console 2>/dev/null || true
  echo "[no-nand-init] $*"
}

log starting
bb=/bin/busybox

mount -t proc proc /proc || true
mount -t sysfs sysfs /sys || true
mount -t devtmpfs devtmpfs /dev || mount -t tmpfs tmpfs /dev || true
[ -c /dev/console ] || $bb mknod /dev/console c 5 1 || true
[ -c /dev/null ] || $bb mknod /dev/null c 1 3 || true
[ -c /dev/ptmx ] || $bb mknod /dev/ptmx c 5 2 || true
mkdir -p /dev/pts /dev/socket /tmp /var/run /etc/tmpfs /proc/bus/usb /home/galois_rwdata /data
mount -t devpts devpts /dev/pts || true
mount -t tmpfs tmpfs /tmp || true
mount -t tmpfs tmpfs /etc/tmpfs || true
mount -t tmpfs tmpfs /home/galois_rwdata || true
mount -t usbfs usb /proc/bus/usb || true
mkdir -p /home/galois_rwdata/adb /data/adb
: > /etc/tmpfs/resolv.conf 2>/dev/null || true
: > /etc/tmpfs/hosts 2>/dev/null || true

# Populate /dev and firmware hotplug without launching the stock service stack.
( umask 0; /sbin/ueventd & ) || true
/bin/busybox sleep 1
( umask 0; /sbin/ueventd -s ) || true
[ -e /proc/sys/kernel/hotplug ] && echo /etc/hotplug/wifi-fw.sh > /proc/sys/kernel/hotplug || true
$bb ifconfig lo up 2>/dev/null || true

# Bring up only the Android USB gadget functions needed for a host-visible shell.
# These sysfs writes configure the live USB gadget; they do not write storage.
if [ -d /sys/class/android_usb/android0 ]; then
  log configuring android_usb acm,adb
  echo 0 > /sys/class/android_usb/android0/enable 2>/dev/null || true
  echo 0d02 > /sys/class/android_usb/android0/idProduct 2>/dev/null || true
  [ -w /sys/class/android_usb/android0/idVendor ] && echo 1286 > /sys/class/android_usb/android0/idVendor 2>/dev/null || true
  echo "MRVL USB SDK" > /sys/class/android_usb/android0/iProduct 2>/dev/null || true
  echo no-nand-probe > /sys/class/android_usb/android0/iSerial 2>/dev/null || true
  [ -w /sys/devices/virtual/android_usb/android0/iSerial ] && echo no-nand-probe > /sys/devices/virtual/android_usb/android0/iSerial 2>/dev/null || true
  [ -d /sys/class/android_usb/android0/f_acm ] && echo 1 > /sys/class/android_usb/android0/f_acm/instances 2>/dev/null || true
  echo acm,adb > /sys/class/android_usb/android0/functions 2>/dev/null || echo acm > /sys/class/android_usb/android0/functions 2>/dev/null || true
  echo 1 > /sys/class/android_usb/android0/enable 2>/dev/null || true
  ( umask 0; /sbin/ueventd -s ) || true
else
  log android_usb sysfs not present yet
fi

# Primary host-visible shell: ACM serial /dev/ttyGS0. ADB is secondary.
(
  while true; do
    if [ -e /dev/ttyGS0 ]; then
      echo
      echo "HK Invoke no-NAND raw root shell on ttyGS0"
      echo "Stock rcS was not run; NAND-backed partitions were not mounted."
      echo
      /bin/busybox sh -i </dev/ttyGS0 >/dev/ttyGS0 2>&1
    fi
    /bin/busybox sleep 1
  done
) &

# Opportunistic ADB. Do not depend on this as the only access path.
(
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    [ -e /dev/android_adb ] && break
    [ -e /dev/android ] && break
    /bin/busybox sleep 1
  done
  if [ -x /sbin/adbd-root ]; then
    log starting adbd-root
    /sbin/adbd-root &
  fi
) &

cat > /tmp/NO_NAND_PROBE_READY <<'EOF'
No-NAND probe init reached userspace.
Stock rcS was not run. NAND/app/localstorage partitions were not mounted.
Inventory should be at /tmp/no-nand-hardware-inventory.txt.
Audio inventory should be at /tmp/no-nand-audio-inventory.txt.
Network inventory should be at /tmp/no-nand-network-inventory.txt.
Suggested retrieval after ADB/ttyGS0 appears:
  cat /tmp/no-nand-hardware-inventory.txt
  sh /no-nand-inventory.sh
  cat /tmp/no-nand-audio-inventory.txt
  sh /no-nand-audio-inventory.sh
  cat /tmp/no-nand-network-inventory.txt
  sh /no-nand-network-inventory.sh
Optional temporary Wi-Fi proof requires explicit Joe approval:
  HK_INVOKE_RAM_WIFI_APPROVED=yes HK_INVOKE_RAM_WIFI_SSID=<ssid> HK_INVOKE_RAM_WIFI_PSK=<psk> /no-nand-wifi.sh
  HK_INVOKE_RAM_WIFI_APPROVED=yes HK_INVOKE_RAM_WIFI_YOURSSID_PSK=<psk> /no-nand-wifi.sh
Optional temporary USB-network proof requires explicit Joe approval:
  HK_INVOKE_RAM_USBNET_APPROVED=yes /no-nand-usbnet.sh
Optional OTA83 SD8887/Bluetooth module-load proof requires explicit Joe approval
and an artifact generated with --stage-ota83-connectivity:
  HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED=yes /no-nand-ota83-module-load.sh
EOF
cat /tmp/NO_NAND_PROBE_READY >/dev/console 2>/dev/null || true
cat /tmp/NO_NAND_PROBE_READY

(
  /bin/sh /no-nand-inventory.sh >/tmp/no-nand-inventory.log 2>&1 || true
  echo done >/tmp/no-nand-inventory.done 2>/dev/null || true
  cat /tmp/no-nand-hardware-inventory.txt >/dev/console 2>/dev/null || true
  /bin/sh /no-nand-audio-inventory.sh >/tmp/no-nand-audio-inventory.log 2>&1 || true
  echo done >/tmp/no-nand-audio-inventory.done 2>/dev/null || true
  cat /tmp/no-nand-audio-inventory.txt >/dev/console 2>/dev/null || true
  /bin/sh /no-nand-network-inventory.sh >/tmp/no-nand-network-inventory.log 2>&1 || true
  echo done >/tmp/no-nand-network-inventory.done 2>/dev/null || true
  cat /tmp/no-nand-network-inventory.txt >/dev/console 2>/dev/null || true
) &

# Keep pid 1 alive for ADB/ttyGS0. Do not fall through to kernel panic.
while true; do
  sleep 30
  date >/tmp/no-nand-heartbeat 2>/dev/null || true
  ifconfig -a >/tmp/no-nand-ifconfig 2>/dev/null || true
done
"""


def usbnet_helper() -> str:
    return r"""#!/bin/sh
# Optional temporary USB-network proof for the no-NAND initramfs.
# DO NOT RUN unless Joe explicitly approves RAM-only USB gadget/network changes
# for this session. This writes only live sysfs/tmpfs/kernel network state.
# It must not run saveenv, mount NAND-backed partitions, or write device storage.
set -eu
: "${HK_INVOKE_RAM_USBNET_APPROVED:=}"
if [ "$HK_INVOKE_RAM_USBNET_APPROVED" != "yes" ]; then
  echo "Refusing: set HK_INVOKE_RAM_USBNET_APPROVED=yes only after explicit Joe approval." >&2
  exit 22
fi

log() {
  echo "[no-nand-usbnet] $*" >/dev/console 2>/dev/null || true
  echo "[no-nand-usbnet] $*"
}

gadget=/sys/class/android_usb/android0
if [ -d "$gadget" ]; then
  log "reconfiguring live android_usb gadget for USB networking plus ACM/ADB"
  echo 0 > "$gadget/enable" 2>/dev/null || true
  [ -w "$gadget/idVendor" ] && echo 1286 > "$gadget/idVendor" 2>/dev/null || true
  [ -w "$gadget/idProduct" ] && echo 0d02 > "$gadget/idProduct" 2>/dev/null || true
  [ -w "$gadget/iProduct" ] && echo "MRVL USB SDK" > "$gadget/iProduct" 2>/dev/null || true
  [ -w "$gadget/iSerial" ] && echo no-nand-usbnet > "$gadget/iSerial" 2>/dev/null || true
  [ -d "$gadget/f_acm" ] && echo 1 > "$gadget/f_acm/instances" 2>/dev/null || true
  configured=false
  for funcs in rndis,acm,adb ecm,acm,adb ncm,acm,adb rndis,acm ecm,acm ncm,acm acm,adb acm; do
    if echo "$funcs" > "$gadget/functions" 2>/dev/null; then
      log "selected android_usb functions=$funcs"
      configured=true
      break
    fi
  done
  echo 1 > "$gadget/enable" 2>/dev/null || true
  if [ "$configured" != "true" ]; then
    log "no USB-network gadget function accepted; ACM/ADB may be the only surface"
  fi
else
  log "android_usb sysfs not present; cannot reconfigure USB gadget"
fi

sleep 2
for iface in usb0 rndis0 ecm0 ncm0 eth0; do
  if [ -e "/sys/class/net/$iface" ]; then
    log "bringing up $iface with static RAM-only address 192.168.77.2/24"
    ifconfig "$iface" 192.168.77.2 netmask 255.255.255.0 up 2>/dev/null || true
    break
  fi
done
ifconfig -a || true
route -n || true
"""


def wifi_helper() -> str:
    return r"""#!/bin/sh
# Optional temporary Wi-Fi proof for the no-NAND initramfs.
# DO NOT RUN unless Joe explicitly approves RAM-only Wi-Fi association for this
# session. Pass disposable/home SSID and PSK via runtime env vars only.
# Do not copy credentials into this image, /home/galois, or /home/galois_rwdata.
set -eu
bb=/bin/busybox
if [ ! -x "$bb" ]; then
  bb=busybox
fi
: "${HK_INVOKE_RAM_WIFI_APPROVED:=}"
if [ "$HK_INVOKE_RAM_WIFI_APPROVED" != "yes" ]; then
  echo "Refusing: set HK_INVOKE_RAM_WIFI_APPROVED=yes only after explicit Joe approval." >&2
  exit 21
fi
: "${HK_INVOKE_RAM_WIFI_SSID:=}"
: "${HK_INVOKE_RAM_WIFI_PSK:=}"
: "${HK_INVOKE_RAM_WIFI_YOURSSID_PSK:=}"
escape_wpa_value() {
  printf '%s' "$1" | "$bb" sed 's/\\/\\\\/g; s/"/\\"/g'
}
profile_count=0
if [ -n "$HK_INVOKE_RAM_WIFI_SSID" ] || [ -n "$HK_INVOKE_RAM_WIFI_PSK" ]; then
  if [ -z "$HK_INVOKE_RAM_WIFI_SSID" ] || [ -z "$HK_INVOKE_RAM_WIFI_PSK" ]; then
    echo "Refusing: set both HK_INVOKE_RAM_WIFI_SSID and HK_INVOKE_RAM_WIFI_PSK, or set HK_INVOKE_RAM_WIFI_YOURSSID_PSK." >&2
    exit 23
  fi
  profile_count=$((profile_count + 1))
fi

if [ -n "$HK_INVOKE_RAM_WIFI_YOURSSID_PSK" ]; then
  profile_count=$((profile_count + 1))
fi


if [ "$profile_count" -eq 0 ]; then
  echo "Refusing: set HK_INVOKE_RAM_WIFI_SSID/HK_INVOKE_RAM_WIFI_PSK or runtime-only HK_INVOKE_RAM_WIFI_YOURSSID_PSK." >&2
  exit 23
fi

"$bb" mkdir -p /tmp/wifi
"$bb" cat >/tmp/wifi/wpa_supplicant.conf <<EOF
ctrl_interface=/tmp/wifi
update_config=0
EOF

if [ -n "$HK_INVOKE_RAM_WIFI_SSID" ]; then
  ssid=$(escape_wpa_value "$HK_INVOKE_RAM_WIFI_SSID")
  psk=$(escape_wpa_value "$HK_INVOKE_RAM_WIFI_PSK")
  "$bb" cat >>/tmp/wifi/wpa_supplicant.conf <<EOF
network={
    ssid="$ssid"
    psk="$psk"
    priority=30
}
EOF
fi

if [ -n "$HK_INVOKE_RAM_WIFI_YOURSSID_PSK" ]; then
  yourssid_psk=$(escape_wpa_value "$HK_INVOKE_RAM_WIFI_YOURSSID_PSK")
  "$bb" cat >>/tmp/wifi/wpa_supplicant.conf <<EOF
network={
    ssid="YOURSSID"
    psk="$yourssid_psk"
    priority=22
}
network={
    ssid="YOURSSID"
    psk="$yourssid_psk"
    priority=20
}
network={
    ssid="yourssid"
    psk="$yourssid_psk"
    priority=19
}
EOF
fi


# Load kernel-matching SD8801 modules without invoking stock scripts that may
# write MACs. If the OTA83 stage is present, expose its SD8887 firmware under
# both the OTA83 filename and the firmware name requested by the live 3.8.13-mrvl
# driver (`mrvl/sd8887_uapsta.bin`). These symlinks live in the initramfs RAM
# filesystem only.
stage=/ota83-stage
"$bb" mkdir -p /lib/firmware/mrvl
if [ -f "$stage/lib/firmware/mrvl/sd8887_wlan_a2_p78.bin" ]; then
  "$bb" ln -sf "$stage/lib/firmware/mrvl/sd8887_wlan_a2_p78.bin" /lib/firmware/mrvl/sd8887_wlan_a2_p78.bin 2>/dev/null || true
  "$bb" ln -sf "$stage/lib/firmware/mrvl/sd8887_wlan_a2_p78.bin" /lib/firmware/mrvl/sd8887_uapsta.bin 2>/dev/null || true
fi
if [ -f "$stage/lib/firmware/mrvl/txpwrlimit_cfg_8887.bin" ]; then
  "$bb" ln -sf "$stage/lib/firmware/mrvl/txpwrlimit_cfg_8887.bin" /lib/firmware/mrvl/txpwrlimit_cfg_8887.bin 2>/dev/null || true
fi
linux_ver=$($bb uname -r)
mlan_ko=/lib/modules/$linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/88mlan.ko
sd8801_ko=/lib/modules/$linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/sd8801.ko
if [ ! -d /sys/class/net/mlan0 ]; then
  "$bb" rmmod sd8801 2>/dev/null || true
  "$bb" rmmod 88mlan 2>/dev/null || true
  [ -f "$mlan_ko" ] && "$bb" insmod "$mlan_ko" 2>/dev/null || true
  [ -f "$sd8801_ko" ] && "$bb" insmod "$sd8801_ko" cal_data_cfg=mrvl/WlanCalData_sd8801.conf mac_addr=00:50:43:xx:xx:xx 2>/dev/null || true
fi
"$bb" sleep 3
"$bb" ifconfig -a || true
if [ ! -d /sys/class/net/mlan0 ]; then
  echo "Refusing: mlan0 did not appear after RAM-only SD8801 module load." >&2
  "$bb" dmesg | "$bb" tail -120 >&2 || true
  exit 26
fi
/bin/wpa_supplicant -B -Dnl80211 -imlan0 -c/tmp/wifi/wpa_supplicant.conf
"$bb" sleep 5
/bin/wpa_cli -p /tmp/wifi status || true
/sbin/udhcpc -i mlan0 -s /etc/udhcpc.script -A 5 -t 8 || true
"$bb" ifconfig mlan0 || true
"$bb" route -n || true
"""


def ota83_module_load_helper() -> str:
    return r"""#!/bin/sh
# Optional RAM-only OTA83 module-load proof for the no-NAND initramfs.
# DO NOT RUN unless Joe explicitly approves RAM-only SD8887/Bluetooth module
# loading for this session. This changes live kernel state in RAM only.
# It must not run Wi-Fi association, DHCP, stock scripts, saveenv, or storage writes.
set -eu
bb=/bin/busybox
if [ ! -x "$bb" ]; then
  bb=busybox
fi
: "${HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED:=}"
if [ "$HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED" != "yes" ]; then
  echo "Refusing: set HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED=yes only after explicit Joe approval." >&2
  exit 24
fi

log() {
  echo "[no-nand-ota83-module-load] $*" >/dev/console 2>/dev/null || true
  echo "[no-nand-ota83-module-load] $*"
}

stage=/ota83-stage
if [ ! -d "$stage" ]; then
  echo "Refusing: OTA83 stage directory is absent at $stage" >&2
  exit 25
fi

"$bb" mkdir -p /lib/firmware/mrvl /tmp/ota83-module-load
for fw in \
  sd8887_wlan_a2_p78.bin \
  sd8887_bt_a2.bin \
  sd8887_bt_a2_new.bin \
  WlanCalData_ext-LS9AD-20160725.conf \
  txpwrlimit_cfg_8887.bin
do
  if [ -f "$stage/lib/firmware/mrvl/$fw" ] && [ ! -e "/lib/firmware/mrvl/$fw" ]; then
    "$bb" ln -s "$stage/lib/firmware/mrvl/$fw" "/lib/firmware/mrvl/$fw" 2>/dev/null || true
  fi
done
if [ -f "$stage/lib/firmware/mrvl/sd8887_wlan_a2_p78.bin" ]; then
  "$bb" ln -sf "$stage/lib/firmware/mrvl/sd8887_wlan_a2_p78.bin" /lib/firmware/mrvl/sd8887_uapsta.bin 2>/dev/null || true
fi

{
  echo "## before"
  "$bb" uname -a
  "$bb" cat /proc/cmdline
  "$bb" cat /proc/modules || true
  "$bb" cat /proc/net/dev || true
  "$bb" find /sys/bus/sdio/devices -maxdepth 5 -type f -print 2>/dev/null || true

  echo
  echo "## approved kernel-matching SD8801 load with OTA83 firmware alias"
  linux_ver=$($bb uname -r)
  echo "live kernel=$linux_ver"
  echo "OTA83 staged modules mlan.ko, sd8xxx.ko, and bt8xxx.ko are retained as evidence only; do not insmod wlan_sd8887 or bt_sd8887 here unless the live kernel matches their 3.8.13-yocto-standard version magic."
  mlan_ko=/lib/modules/$linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/88mlan.ko
  sd8801_ko=/lib/modules/$linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/sd8801.ko
  if [ ! -d /sys/class/net/mlan0 ]; then
    log "rmmod stale SD8801 modules if present"
    "$bb" rmmod sd8801 2>/dev/null || true
    "$bb" rmmod 88mlan 2>/dev/null || true
    if [ -f "$mlan_ko" ]; then
      log "insmod kernel-matching 88mlan.ko"
      "$bb" insmod "$mlan_ko" || true
    fi
    if [ -f "$sd8801_ko" ]; then
      log "insmod kernel-matching sd8801.ko with OTA83 firmware alias available"
      "$bb" insmod "$sd8801_ko" cal_data_cfg=mrvl/WlanCalData_sd8801.conf mac_addr=00:50:43:xx:xx:xx || true
    fi
  fi
  "$bb" sleep 5

  echo
  echo "## after"
  "$bb" cat /proc/modules || true
  "$bb" dmesg | "$bb" tail -300 || true
  "$bb" cat /proc/net/dev || true
  "$bb" ifconfig -a || true
  "$bb" find /sys/class/net -maxdepth 4 -type f -print 2>/dev/null || true
} >/tmp/ota83-module-load/ota83-module-load.txt 2>&1

"$bb" cat /tmp/ota83-module-load/ota83-module-load.txt
"""


def stage_ota83_files(ota83_rootfs: Path, work_rootfs: Path) -> list[str]:
    copied: list[str] = []
    stage_root = work_rootfs / "ota83-stage"
    required_members = {
        OTA83_STAGE_MEMBERS["modules"]["mlan"],
        OTA83_STAGE_MEMBERS["modules"]["sd8xxx"],
        OTA83_STAGE_MEMBERS["modules"]["bt8xxx"],
        OTA83_STAGE_MEMBERS["firmware"]["wlan_fw"],
        OTA83_STAGE_MEMBERS["firmware"]["bt_fw"],
    }
    for members in OTA83_STAGE_MEMBERS.values():
        for member in members.values():
            safe_stage_member(member)
            dest = stage_root / member
            if dest.exists():
                raise FileExistsError(f"refusing to overwrite staged OTA83 member {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = read_archive_member(ota83_rootfs, member)
            except RuntimeError:
                if member in required_members:
                    raise
                continue
            dest.write_bytes(data)
            copied.append(member)
    (stage_root / "MANIFEST.json").write_text(
        json.dumps({"source": str(ota83_rootfs), "members": copied}, indent=2, sort_keys=True)
        + "\n"
    )
    return copied


def copy_rootfs(
    rootfs: Path,
    work_rootfs: Path,
    ota83_rootfs: Path,
    stage_ota83_connectivity: bool,
) -> list[str]:
    if work_rootfs.exists():
        raise FileExistsError(f"refusing to overwrite existing {work_rootfs}")
    shutil.copytree(rootfs, work_rootfs, symlinks=True)
    stock_init = work_rootfs / "init"
    if stock_init.exists():
        shutil.copy2(stock_init, work_rootfs / "init.stock")
    stock_init.write_text(no_nand_init())
    stock_init.chmod(0o755)
    helper = work_rootfs / "no-nand-wifi.sh"
    helper.write_text(wifi_helper())
    helper.chmod(0o755)
    usbnet = work_rootfs / "no-nand-usbnet.sh"
    usbnet.write_text(usbnet_helper())
    usbnet.chmod(0o755)
    inventory = work_rootfs / "no-nand-inventory.sh"
    inventory.write_text(hardware_inventory_script())
    inventory.chmod(0o755)
    audio_inventory = work_rootfs / "no-nand-audio-inventory.sh"
    audio_inventory.write_text(audio_inventory_script())
    audio_inventory.chmod(0o755)
    network_inventory = work_rootfs / "no-nand-network-inventory.sh"
    network_inventory.write_text(network_inventory_script())
    network_inventory.chmod(0o755)
    ota83_helper = work_rootfs / "no-nand-ota83-module-load.sh"
    ota83_helper.write_text(ota83_module_load_helper())
    ota83_helper.chmod(0o755)
    (work_rootfs / "NO_NAND_PROBE.txt").write_text(
        "No-NAND probe image: custom /init bypasses stock rcS and avoids NAND mounts.\n"
    )
    if stage_ota83_connectivity:
        return stage_ota83_files(ota83_rootfs, work_rootfs)
    return []


def cpio_paths(root: Path) -> list[str]:
    paths = ["."]
    for path in sorted(root.rglob("*")):
        paths.append("./" + path.relative_to(root).as_posix())
    return paths


def pack_newc_gzip(work_rootfs: Path, out_image: Path) -> None:
    paths = "\n".join(cpio_paths(work_rootfs)) + "\n"
    proc = subprocess.run(
        ["cpio", "-o", "-H", "newc"],
        input=paths.encode(),
        cwd=work_rootfs,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode(errors="replace"))
        raise SystemExit(proc.returncode)
    out_image.write_bytes(gzip.compress(proc.stdout, compresslevel=9, mtime=0))


def create_serving_image_dir(
    base_image_dir: Path, out_dir: Path, custom_82: Path
) -> Path:
    serve_dir = out_dir / "image-dir"
    serve_dir.mkdir()
    for name in REQUIRED_BASE_IMAGES:
        target = base_image_dir / name
        if name == "82_IMAGE":
            continue
        (serve_dir / name).symlink_to(target)
    (serve_dir / "82_IMAGE").symlink_to(custom_82)
    return serve_dir


def uboot_commands(custom_82: Path) -> str:
    initrd_size = custom_82.stat().st_size
    return f"""# Classification: RAM-only when run from the generated image-dir.
# Preconditions: use scripts/hk-invoke/ram_boot_console.sh <this artifact>/image-dir.
# No saveenv. No NAND/eMMC/SPI write/erase/protect commands.
usbload 0x81 0x0c400000
usbload 0x82 0x08000000
set bootargs console=ttyS0,115200 debug init=/init root=/dev/ram mtdparts=mv_nand:128K(block0)ro,1M(prebootloader)ro,4M(TZ)ro,4M(TZ-B)ro,512K(postbootloader)ro,512K(postbootloader-B)ro,8M(kernel)ro,105M(rootfs)ro,117120K(cache)ro,15M(recovery)ro,512K(fts)ro,2M(factory_store)ro,1M@255M(bbt)ro initrd=0x08000000,{initrd_size}
bootm 0x0c400000
"""


def write_manifest(
    out_dir: Path,
    manifest: dict[str, Any],
    custom_82: Path,
    serve_dir: Path,
    staged_ota83_members: list[str],
) -> None:
    manifest = dict(manifest)
    manifest.update(
        {
            "artifact_dir": str(out_dir),
            "custom_82_image": str(custom_82),
            "custom_82_size": custom_82.stat().st_size,
            "serving_image_dir": str(serve_dir),
            "bootargs_init": "/init (custom no-NAND init in rebuilt 82_IMAGE)",
            "live_classification": "RAM-only device boot; host-local artifact only",
            "staged_ota83_members": staged_ota83_members,
        }
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (out_dir / "uboot-ram-commands.txt").write_text(uboot_commands(custom_82))
    (out_dir / "README.md").write_text(
        f"""# HK Invoke no-NAND initramfs artifact

Classification: offline host artifact. The generated `image-dir/` is intended
for a future RAM-only service-mode boot and does not require or perform device
storage writes.

Use only after a fresh service-mode prompt is acquired and check-only passes:

```bash
scripts/hk-invoke/ram_boot_console.sh --check-only {serve_dir}
scripts/hk-invoke/ram_boot_console.sh {serve_dir}
```

At `MV88DE3100|>`, run the commands in `uboot-ram-commands.txt`. Expected host
surfaces after `bootm`: Android ADB if `adb` is installed, and/or a macOS
`/dev/cu.*` USB modem from `/dev/ttyGS0`.

Do not run `saveenv`, `run upgrade`, NAND/eMMC/SPI erase/write/protect commands,
or stock full init as a shortcut.

If this artifact was generated with `--stage-ota83-connectivity`, it contains
an `/ota83-stage` directory plus `/no-nand-ota83-module-load.sh`. That helper is
inert until the live shell sets `HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED=yes`;
it must be treated as RAM-only kernel-state escalation, not read-only inventory.
"""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, default=DEFAULT_ROOTFS)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--ota83-rootfs", type=Path, default=DEFAULT_OTA83_ROOTFS)
    parser.add_argument(
        "--stage-ota83-connectivity",
        action="store_true",
        help="stage OTA83 SD8887/Bluetooth/audio candidate files under /ota83-stage in the generated RAM image",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate prerequisites and print JSON; do not write artifacts",
    )
    args = parser.parse_args()

    rootfs = args.rootfs.expanduser()
    image_dir = args.image_dir.expanduser()
    out_root = args.out_root.expanduser()
    ota83_rootfs = args.ota83_rootfs.expanduser()
    manifest = build_manifest(
        rootfs,
        image_dir,
        out_root,
        ota83_rootfs,
        args.stage_ota83_connectivity,
    )
    if args.check:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0 if manifest["ready"] else 1
    if not manifest["ready"]:
        print(json.dumps(manifest, indent=2, sort_keys=True), file=sys.stderr)
        return 2

    out_dir = create_artifact_dir(out_root, utc_stamp())
    work_rootfs = out_dir / "rootfs-work"
    custom_82 = out_dir / "82_IMAGE.no-nand"
    staged_ota83_members = copy_rootfs(
        rootfs,
        work_rootfs,
        ota83_rootfs,
        args.stage_ota83_connectivity,
    )
    pack_newc_gzip(work_rootfs, custom_82)
    serve_dir = create_serving_image_dir(image_dir, out_dir, custom_82)
    write_manifest(out_dir, manifest, custom_82, serve_dir, staged_ota83_members)
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
