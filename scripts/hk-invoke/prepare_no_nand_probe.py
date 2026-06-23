#!/usr/bin/env python3
"""Prepare a no-NAND HK Invoke RAM/Linux probe plan.

This is an offline host-side planner. It inspects already-extracted recovery
artifacts and writes a plan directory under ~/.local/state/hk-invoke by default.
It does not open USB, send U-Boot commands, modify OTA images, or write device
storage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

HOME = Path.home()
DEFAULT_ROOTFS = (
    HOME
    / ".local/state/hk-invoke/recovery-baselines/20260619T012008/extracted/rootfs-82"
)
DEFAULT_IMAGE_DIR = Path("/tmp/hk-invoke-ota2-work-current")
DEFAULT_OTA83_ROOTFS = (
    HOME / ".local/state/hk-invoke/ota83-extracts/20260619T012626/06_rootfs.bin"
)
DEFAULT_OUT_ROOT = HOME / ".local/state/hk-invoke/no-nand-probes"

REQUIRED_ROOTFS_PATHS = [
    "bin/sh",
    "bin/wpa_supplicant",
    "bin/wpa_cli",
    "bin/wpa_passphrase",
    "sbin/udhcpc",
    "etc/udhcpc.script",
    "etc/hotplug/wifi-fw.sh",
    "etc/inittab",
]
OPTIONAL_ROOTFS_PATHS = [
    "sbin/adbd-root",
    "home/galois/wifi_conf_2x2.sh",
    "home/galois/wifi_join.sh",
    "home/galois/wifi_action.sh",
    "home/galois/eth_up.sh",
]
REQUIRED_IMAGES = ["81_IMAGE", "82_IMAGE"]


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def rel_exists(root: Path, rel: str) -> bool:
    return (root / rel).exists()


def file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return None


def find_wifi_modules(rootfs: Path) -> list[str]:
    modules_root = rootfs / "lib/modules"
    if not modules_root.exists():
        return []
    matches: list[str] = []
    for path in modules_root.rglob("*.ko"):
        lower = path.as_posix().lower()
        if any(token in lower for token in ("wlan", "mlan", "sd8", "wifi", "wireless")):
            matches.append(path.relative_to(rootfs).as_posix())
    return sorted(matches)


def archive_entries(archive: Path) -> list[str]:
    """List archive members read-only via 7z when available."""
    if not archive.exists() or shutil.which("7z") is None:
        return []
    proc = subprocess.run(
        ["7z", "l", "-ba", str(archive)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        return []
    entries: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 6:
            entries.append(parts[-1].lstrip("/"))
    return entries


def archive_member_text(archive: Path, member: str, limit: int = 200_000) -> str:
    """Read one text member from an archive via 7z without extracting it."""
    if not archive.exists() or shutil.which("7z") is None:
        return ""
    proc = subprocess.run(
        ["7z", "e", "-so", str(archive), member],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout[:limit]


def ota83_evidence(ota83_rootfs: Path) -> dict[str, Any]:
    entries = archive_entries(ota83_rootfs)
    entry_set = set(entries)

    module_targets = {
        "mlan": "lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/wlan_sd8887/mlan.ko",
        "sd8xxx": "lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/wlan_sd8887/sd8xxx.ko",
        "bt8xxx": "lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/bt_sd8887/bt8xxx.ko",
        "btmrvl": "lib/modules/3.8.13-yocto-standard/kernel/drivers/bluetooth/btmrvl.ko",
        "ehci_platform": "lib/modules/3.8.13-yocto-standard/kernel/drivers/usb/host/ehci-platform.ko",
    }
    firmware_targets = {
        "wlan_fw": "lib/firmware/mrvl/sd8887_wlan_a2_p78.bin",
        "bt_fw": "lib/firmware/mrvl/sd8887_bt_a2.bin",
        "bt_fw_new": "lib/firmware/mrvl/sd8887_bt_a2_new.bin",
        "cal_ls9ad": "lib/firmware/mrvl/WlanCalData_ext-LS9AD-20160725.conf",
        "tx_power": "lib/firmware/mrvl/txpwrlimit_cfg_8887.bin",
    }
    audio_targets = {
        "asound_conf": "etc/asound.conf",
        "asound_product": "etc/asound-product.conf",
        "aplay": "usr/bin/aplay",
        "arecord": "usr/bin/arecord",
        "alsa_init": "usr/bin/alsa-init.sh",
        "unmute_audio": "sbin/unmute_audio.sh",
        "libasound": "usr/lib/libasound.so.2.0.0",
        "tinyalsa": "system/lib/libtinyalsa.so",
    }
    modules_alias = archive_member_text(
        ota83_rootfs, "lib/modules/3.8.13-yocto-standard/modules.alias"
    )
    modules_builtin = archive_member_text(
        ota83_rootfs, "lib/modules/3.8.13-yocto-standard/modules.builtin"
    )
    asound_product = archive_member_text(ota83_rootfs, "etc/asound-product.conf")
    return {
        "archive": str(ota83_rootfs),
        "exists": ota83_rootfs.exists(),
        "inspection_tool": shutil.which("7z"),
        "entry_count": len(entries),
        "modules": {name: path in entry_set for name, path in module_targets.items()},
        "firmware": {name: path in entry_set for name, path in firmware_targets.items()},
        "audio_userspace": {name: path in entry_set for name, path in audio_targets.items()},
        "sdio_aliases": {
            "sd8xxx_02DF_9135": "sdio:c*v02DFd9135* sd8xxx" in modules_alias,
            "bt8xxx_02DF_9136": "sdio:c*v02DFd9136* bt8xxx" in modules_alias,
        },
        "alsa_builtins": {
            "snd_soc_berlin": "snd-soc-berlin" in modules_builtin,
            "snd_soc_wm8904": "snd-soc-wm8904" in modules_builtin,
            "snd_pcm": "kernel/sound/core/snd-pcm.ko" in modules_builtin,
        },
        "asound_product_clues": {
            "mentions_pcm_dsp": "pcm.dsp" in asound_product,
            "mentions_mic_reco": "mic_reco" in asound_product,
            "mentions_s32_le": "S32_LE" in asound_product,
            "mentions_48000": "48000" in asound_product,
        },
    }


def init_template(wifi_modules: list[str]) -> str:
    del wifi_modules
    return """#!/bin/sh
# No-NAND HK Invoke read-only probe init template.
# Classification: RAM-only if booted from a throwaway initramfs work copy.
# This template must not mount NAND partitions read-write, must not store Wi-Fi
# secrets on the device, and must not auto-load Wi-Fi/Bluetooth/audio modules.

set -eu

mount -t proc proc /proc || true
mount -t sysfs sysfs /sys || true
mount -t devtmpfs devtmpfs /dev || mount -t tmpfs tmpfs /dev || true
mount -t tmpfs tmpfs /tmp || true
mkdir -p /tmp/wifi /var/run /etc/tmpfs /dev/pts
mount -t devpts devpts /dev/pts || true

# Firmware hotplug helper is recorded for later module-load tests, but this
# read-only template intentionally does not insmod anything automatically.
echo /etc/hotplug/wifi-fw.sh > /proc/sys/kernel/hotplug || true

/bin/busybox ifconfig -a || true
cat <<'MSG'
NO-NAND read-only probe init is up.
Evidence commands before any module/service escalation:
  uname -a
  cat /proc/cmdline
  mount
  cat /proc/mtd
  ls -la /dev /dev/snd /sys/class/sound
  cat /proc/asound/cards /proc/asound/pcm 2>/dev/null || true
  cat /proc/net/dev
  cat /proc/modules
  /bin/busybox ps

Do not run Wi-Fi/audio stock scripts here. Module loading is a separate
RAM-only escalation and requires explicit Joe approval.
MSG

exec /bin/sh
"""


def module_load_template(manifest: dict[str, Any]) -> str:
    rootfs_modules = "\n".join(f"# rootfs-82 candidate: /{m}" for m in manifest["wifi_modules"])
    if not rootfs_modules:
        rootfs_modules = "# no rootfs-82 Wi-Fi modules found by planner"
    return f"""#!/bin/sh
# HK Invoke RAM-only module-load probe template.
# DO NOT RUN unless Joe explicitly approves RAM-only module loading for this
# session. This changes live kernel state in RAM but must not write device
# storage, Wi-Fi credentials, NAND/eMMC/SPI, or U-Boot env.

set -eu
: "${{HK_INVOKE_RAM_MODULE_LOAD_APPROVED:=}}"
if [ "$HK_INVOKE_RAM_MODULE_LOAD_APPROVED" != "yes" ]; then
  echo "Refusing: set HK_INVOKE_RAM_MODULE_LOAD_APPROVED=yes only after explicit Joe approval." >&2
  exit 20
fi

mkdir -p /tmp/module-probe
log=/tmp/module-probe/module-load.log
exec >$log 2>&1
set -x

uname -a
cat /proc/cmdline
cat /proc/modules || true

# Observed live SDIO IDs from first no-NAND inventory:
#   02DF:9135, 02DF:9136, 02DF:9137
# rootfs-82 candidates seen by this planner:
{rootfs_modules}

# Conservative rootfs-82 Wi-Fi probe. Do not run wpa_supplicant or DHCP here.
[ -f /lib/modules/linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/88mlan.ko ] && \
  insmod /lib/modules/linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/88mlan.ko || true
[ -f /lib/modules/linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/sd8801.ko ] && \
  insmod /lib/modules/linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/sd8801.ko cal_data_cfg=mrvl/WlanCalData_sd8801.conf mac_addr=00:50:43:xx:xx:xx || true

cat /proc/modules || true
dmesg | tail -200 || true
cat /proc/net/dev || true

# OTA83 evidence from host-side audit says the better-matching staged artifact
# should provide mlan.ko + sd8xxx.ko for 02DF:9135 and bt8xxx.ko for 02DF:9136,
# plus sd8887 firmware/calibration. Those files are not expected in the current
# rootfs-82 no-NAND image; stage them into a future artifact before using this
# section.
if [ -f /lib/modules/wlan_sd8887/mlan.ko ] && [ -f /lib/modules/wlan_sd8887/sd8xxx.ko ]; then
  insmod /lib/modules/wlan_sd8887/mlan.ko || true
  insmod /lib/modules/wlan_sd8887/sd8xxx.ko cal_data_cfg=mrvl/WlanCalData_ext-LS9AD-20160725.conf txpwrlimit_cfg=mrvl/txpwrlimit_cfg_8887.bin cfg80211_wext=0xf auto_ds=2 fw_serial=1 sta_name=wlan uap_name=p2p fw_name=mrvl/sd8887_wlan_a2_p78.bin ps_mode=2 max_sta_bss=1 max_uap_bss=1 drvdbg=0x7 antenna_div=1 module_rev=22 || true
fi

cat /proc/modules || true
dmesg | tail -300 || true
cat /proc/net/dev || true
"""


def wifi_template() -> str:
    return r"""#!/bin/sh
# Temporary no-persistence Wi-Fi proof commands for the HK Invoke RAM shell.
# DO NOT RUN unless Joe explicitly approves RAM-only Wi-Fi association for this
# session. Pass SSID/PSK values via runtime env vars only. Named home targets
# are YOURSSID/YOURSSID/yourssid aliases only; do not paste credentials into logs or artifacts.

set -eu
: "${HK_INVOKE_RAM_WIFI_APPROVED:=}"
if [ "$HK_INVOKE_RAM_WIFI_APPROVED" != "yes" ]; then
  echo "Refusing: set HK_INVOKE_RAM_WIFI_APPROVED=yes only after explicit Joe approval." >&2
  exit 21
fi
: "${HK_INVOKE_RAM_WIFI_SSID:=}"
: "${HK_INVOKE_RAM_WIFI_PSK:=}"
: "${HK_INVOKE_RAM_WIFI_YOURSSID_PSK:=}"
escape_wpa_value() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
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

mkdir -p /tmp/wifi /etc/tmpfs
cat > /tmp/wifi/wpa_supplicant.conf <<EOF
ctrl_interface=/tmp/wifi
update_config=0
EOF

if [ -n "$HK_INVOKE_RAM_WIFI_SSID" ]; then
  ssid=$(escape_wpa_value "$HK_INVOKE_RAM_WIFI_SSID")
  psk=$(escape_wpa_value "$HK_INVOKE_RAM_WIFI_PSK")
  cat >>/tmp/wifi/wpa_supplicant.conf <<EOF
network={
    ssid="$ssid"
    psk="$psk"
    priority=30
}
EOF
fi

if [ -n "$HK_INVOKE_RAM_WIFI_YOURSSID_PSK" ]; then
  yourssid_psk=$(escape_wpa_value "$HK_INVOKE_RAM_WIFI_YOURSSID_PSK")
  cat >>/tmp/wifi/wpa_supplicant.conf <<EOF
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


wpa_supplicant -B -i mlan0 -c /tmp/wifi/wpa_supplicant.conf
sleep 5
wpa_cli -p /tmp/wifi status || true
udhcpc -i mlan0 -s /etc/udhcpc.script -A 5 -t 8 || true
/bin/busybox ifconfig mlan0 || true
/bin/busybox route -n || true
cat /etc/resolv.conf 2>/dev/null || true
ping -c 3 1.1.1.1 || true
"""


def next_no_nand_artifact_template() -> str:
    return """# HK Invoke next no-NAND artifact source

This file intentionally contains host-side commands only. It is not a U-Boot
command file and must not be pasted at `MV88DE3100|>`.

Generate a fresh throwaway no-NAND initramfs artifact before the next RAM-only
boot attempt:

```bash
artifact=$(make no-nand-initramfs | tail -1)
scripts/hk-invoke/ram_boot_console.sh --check-only "$artifact/image-dir"
scripts/hk-invoke/ram_boot_console.sh "$artifact/image-dir"
```

Only after the guarded listener is waiting and Joe deliberately places the
device into service/yellow mode, feed commands from the generated artifact:

```text
$artifact/uboot-ram-commands.txt
```

Feed those commands one line at a time. Do not point `ram_boot_console.sh` at
the stock OTA2 image directory for no-NAND work.
"""


def build_manifest(rootfs: Path, image_dir: Path, ota83_rootfs: Path) -> dict[str, Any]:
    required = {rel: rel_exists(rootfs, rel) for rel in REQUIRED_ROOTFS_PATHS}
    optional = {rel: rel_exists(rootfs, rel) for rel in OPTIONAL_ROOTFS_PATHS}
    images = {
        name: file_size(image_dir / name)
        for name in REQUIRED_IMAGES + ["79_IMAGE", "83_IMAGE", "99_IMAGE"]
    }
    wifi_modules = find_wifi_modules(rootfs)
    ota83 = ota83_evidence(ota83_rootfs)
    ota83_evidence_ready = (
        bool(ota83["exists"])
        and bool(ota83["inspection_tool"])
        and bool(ota83["modules"]["sd8xxx"])
        and bool(ota83["modules"]["bt8xxx"])
        and bool(ota83["firmware"]["wlan_fw"])
        and bool(ota83["audio_userspace"]["aplay"])
        and bool(ota83["alsa_builtins"]["snd_soc_berlin"])
        and bool(ota83["sdio_aliases"]["sd8xxx_02DF_9135"])
        and bool(ota83["sdio_aliases"]["bt8xxx_02DF_9136"])
    )
    return {
        "classification": "offline-host-plan",
        "rootfs": str(rootfs),
        "image_dir": str(image_dir),
        "ota83_rootfs": str(ota83_rootfs),
        "required_rootfs_paths": required,
        "optional_rootfs_paths": optional,
        "images": images,
        "wifi_modules": wifi_modules,
        "ota83_evidence": ota83,
        "ota83_evidence_ready": ota83_evidence_ready,
        "live_sdio_ids_seen": ["02DF:9135", "02DF:9136", "02DF:9137"],
        "recommended_next_artifact": "OTA83 module/firmware-aware RAM artifact, then explicit RAM-only module-load test",
        "no_nand_command_source": "Run make no-nand-initramfs and use only the generated artifact/image-dir plus generated artifact/uboot-ram-commands.txt.",
        "ready_for_no_nand_plan": all(required.values())
        and all(images[name] for name in REQUIRED_IMAGES)
        and ota83_evidence_ready,
        "persistent_write_commands_forbidden": [
            "saveenv",
            "l2nand",
            "tftp2nand",
            "usb2nand",
            "nanderase",
            "nand write",
            "nand erase",
            "mmc write",
            "erase",
            "protect",
            "run upgrade",
        ],
    }


def plan_markdown(manifest: dict[str, Any]) -> str:
    required_missing = [
        k for k, v in manifest["required_rootfs_paths"].items() if not v
    ]
    image_missing = [
        k for k, v in manifest["images"].items() if k in REQUIRED_IMAGES and not v
    ]
    wifi_modules = manifest["wifi_modules"]
    ota83 = manifest.get("ota83_evidence", {})
    return f"""# HK Invoke no-NAND native-connectivity probe plan

Generated: {utc_stamp()}

## Classification

Offline host-side planning artifact. The generated commands/templates are for a
future **RAM-only** boot from throwaway initramfs work copies. They must not be
used to write NAND/eMMC/SPI/env state.

## Readiness summary

- Rootfs: `{manifest["rootfs"]}`
- Base OTA2 image dir, used only for source-image readiness checks:
  `{manifest["image_dir"]}`
- Required rootfs paths missing: {required_missing or "none"}
- Required images missing: {image_missing or "none"}
- rootfs-82 Wi-Fi module candidates: {wifi_modules or "none found"}
- Live SDIO IDs seen earlier: {", ".join(manifest.get("live_sdio_ids_seen", []))}
- OTA83 archive exists: `{ota83.get("exists")}`; entries listed: `{ota83.get("entry_count")}`
- OTA83 SDIO aliases: `{ota83.get("sdio_aliases")}`
- OTA83 ALSA built-ins: `{ota83.get("alsa_builtins")}`
- OTA83 audio userspace: `{ota83.get("audio_userspace")}`
- OTA83 evidence ready: `{manifest.get("ota83_evidence_ready")}`
- Recommended next artifact: `{manifest.get("recommended_next_artifact")}`
- No-NAND command source: `{manifest.get("no_nand_command_source")}`
- Ready for no-NAND plan: `{manifest["ready_for_no_nand_plan"]}`

## Go/no-go for custom boot

Do **not** attempt custom boot unless the host sees Marvell USB `1286:8174` and
`ram_boot_console.sh --check-only` passes. If neither Bluetooth nor Marvell USB
is visible, the correct next step is physical recovery/normal boot verification,
not custom boot.

## Intended live sequence

1. Preserve baseline: `make state` and note Bluetooth vs service USB.
2. Start guarded bridge only after Joe confirms service-mode intent.
3. Run `make no-nand-initramfs`; boot only that generated artifact's
   `<artifact>/image-dir` and `<artifact>/uboot-ram-commands.txt` for
   host-visible ACM/ADB where possible. This planning artifact deliberately does
   not emit its own U-Boot command file.
4. Capture read-only inventory first: `/proc/cmdline`, `/proc/mtd`, `mount`,
   `/dev/snd`, `/proc/asound/*`, `/proc/net/dev`, `ps`, and module lists.
5. Stop. Do not load modules yet. Preserve transcript and host probe artifact.
6. If Joe explicitly approves RAM-only kernel-state changes, use
   `ram-module-load-template.sh`; prefer a future OTA83-staged artifact for
   SD8887/ALSA work because rootfs-82 lacks ALSA modules.
7. Only if Joe explicitly approves RAM-only Wi-Fi association, use
   `wifi-tmp-template.sh` with `HK_INVOKE_RAM_WIFI_APPROVED=yes`, either runtime-only
   `HK_INVOKE_RAM_WIFI_SSID=<ssid>`/`HK_INVOKE_RAM_WIFI_PSK=<psk>` or the named
   `HK_INVOKE_RAM_WIFI_YOURSSID_PSK=<psk>` credential, and DHCP on a newly
   proven interface.
8. Power-cycle normally and verify Bluetooth baseline after the experiment.

## Explicit non-goals for this stage

- No `saveenv`.
- No NAND/eMMC/SPI erase/write/protect commands.
- No writing Wi-Fi credentials to `/home/galois`, `/home/galois_rwdata`, or any
  mounted NAND-backed path.
- No stock full init as a shortcut to ADB until persistent-mount behavior is
  accepted explicitly.
"""


def write_plan(out_dir: Path, manifest: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    wifi_modules = manifest["wifi_modules"]
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (out_dir / "PLAN.md").write_text(plan_markdown(manifest))
    (out_dir / "init-no-nand-template.sh").write_text(init_template(wifi_modules))
    (out_dir / "wifi-tmp-template.sh").write_text(wifi_template())
    module_template = out_dir / "ram-module-load-template.sh"
    module_template.write_text(module_load_template(manifest))
    module_template.chmod(0o755)
    (out_dir / "next-no-nand-artifact.md").write_text(next_no_nand_artifact_template())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, default=DEFAULT_ROOTFS)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--ota83-rootfs", type=Path, default=DEFAULT_OTA83_ROOTFS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate prerequisites and print JSON; do not write plan files",
    )
    args = parser.parse_args()

    manifest = build_manifest(args.rootfs.expanduser(), args.image_dir, args.ota83_rootfs.expanduser())
    if args.check:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0 if manifest["ready_for_no_nand_plan"] else 1

    out_dir = args.out_root.expanduser() / utc_stamp()
    write_plan(out_dir, manifest)
    print(out_dir)
    return 0 if manifest["ready_for_no_nand_plan"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
