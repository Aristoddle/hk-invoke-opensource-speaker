---
last_verified: 2026-06-22
category: runbook
---

# Wire a New HK Invoke — Repeatable Bring-Up Runbook

> PROVEN on Invoke #1 (2026-06-22): RAM-only boot → Wi-Fi association → DHCP on `mlan0` → pinged
> `1.1.1.1` + `example.com`. The device appears on the router as **"MARVELL SEMICONDUCTOR, INC."**
> (Marvell OUI `00:50:43`), at a DHCP address on your LAN. **Entirely RAM-only — no NAND writes, no flash, reboot wipes it.**
> Device facts + the firmware research: [[firmware-bringup-research-2026-06-22]] / memory
> `reference_hk_invoke_device_firmware`. Exact commands live in `scripts/hk-invoke/*` + the proven
> session under `~/.local/state/hk-invoke/sessions/<ts>-wifi-association-*`.

## The two things that actually unblocked it (don't skip)
1. **Use the StockRoot image, NOT the OTA2.** The lane was first building from the OTA2 `83_IMAGE`
   (**69 MB, the final 12.2134.0 = the build that DELIBERATELY REMOVED Wi-Fi**). Switching to the
   **StockRoot `83_IMAGE` (107.9 MB / `107,934,810` bytes, Cortana-era, Wi-Fi-working)** was the fix.
   **Size is the fast discriminator: 69 MB = blocker, 108 MB = working.**
   Source: `github.com/coggy9/HKHacking` releases → `StockRoot/83_IMAGE` (verify the byte count).
2. **Hand the association the PSK in the secret-safe env format + approve the live gate** — the lane
   waits on this and won't attempt to join without it.

## Prereqs (host = the Mac the device is USB-connected to)
- The verified StockRoot `83_IMAGE` (staged on the host; re-download + byte-verify for a fresh host).
- The Harman flashing toolkit (`hk_usb_boot`, used in **device-mode = NAND-SAFE**) + the project's
  `scripts/hk-invoke/*` (`build_no_nand_initramfs.py`, `parse_ota83.py`, `ram_boot_console.sh`).
- The firmware blobs from the StockRoot rootfs: `binwalk -e 83_IMAGE` → mount squashfs → copy
  `lib/firmware/mrvl/*.bin` (the `sd8801`/`sd8887` WLAN + BT blobs) + read its `/etc/init.d/rc.sysinit`.

## Procedure (RAM-only — never writes the device)
1. **Enter service/loader mode:** hold reset (pinhole) while applying power → tap mic-mute **4× within
   5 s** → LED ring **YELLOW**. USB-connect.
2. **RAM-boot** the `82_IMAGE` (U-Boot ramdisk) via `hk_usb_boot` (device-mode download; nothing touches
   NAND). U-Boot console is tunneled over the USB service port.
3. **Stage the StockRoot firmware blobs** into the RAM rootfs `/lib/firmware/mrvl/` (from the host
   binwalk extraction above) so `request_firmware()` succeeds.
4. **Replicate what `rc.sysinit` does** (the RAM boot only runs `rcS`, which skips Wi-Fi init):
   `insmod mlan.ko` then `insmod sd8801.ko` (the device's firmware alias) → `ip link set mlan0 up`.
5. **Associate (secret-safe):** write the PSK to a transient, mode-600, **uncommitted** env file
   (`HK_INVOKE_RAM_WIFI_APPROVED=yes`, `HK_INVOKE_RAM_WIFI_<SSID-UPPER>_PSK='<psk>'`), signal the lane
   `wifi env ready`; it runs `wpa_supplicant` (reads env, never prints PSK, serial echo off) → `udhcpc`.
6. **Validate:** `mlan0` has a DHCP address; `ping 1.1.1.1` and `ping example.com` succeed; the router's
   client list shows a new **MARVELL SEMICONDUCTOR** device (OUI `00:50:43`).

## Hard NEVERs (brick / Wi-Fi-kill guards)
- **Never flash `99_IMAGE`** — confirmed bricks (stuck loop, no U-Boot, hardware recovery needed).
- **Never base on the `12.2134.0` final OTA** (the 69 MB image) — it removes Wi-Fi.
- **No `l2nand` / no NAND writes** for bring-up — stay RAM-only via `hk_usb_boot` device-mode.
- **PSK never** committed / logged / put in a notification — transient 600 env file only.

## What's next (after network, per Invoke)
- `adb connect <ip>:5555` for a root shell; bring up audio (`snd-soc-wm8904` + Berlin machine driver →
  `aplay`/`music-test`); reverse the `mcu-interface` for LEDs/buttons.
- **Persistence (survive reboot) is a SEPARATE, gated phase** — it requires NAND writes and is the only
  brick-risk step; do it deliberately, never as part of bring-up.

## Second-Invoke checklist (TL;DR)
☐ host has byte-verified StockRoot 108 MB `83_IMAGE` (NOT the 69 MB OTA2) ☐ blobs binwalk-extracted ☐
device in yellow service mode + USB ☐ RAM-boot 82_IMAGE (no NAND) ☐ blobs in `/lib/firmware/mrvl/` ☐
PSK in transient 600 env ☐ `insmod mlan + sd8801` → `ip link up` → `wpa_supplicant` → `udhcpc` ☐ validate
DHCP + ping ☐ confirm Marvell device on the router.
