# Reviving the Harman Kardon Invoke — an Open-Source Speaker

> Turning a discontinued Cortana speaker back into a great, fully-controllable, **open-source** WiFi +
> Bluetooth speaker — booting custom Linux on the original hardware, **no soldering, no brick risk.**

![status](https://img.shields.io/badge/status-WiFi%20%2B%20internet%20up-success)
![safety](https://img.shields.io/badge/bring--up-RAM--only%20%C2%B7%20no%20NAND%20writes-blue)
![license](https://img.shields.io/badge/license-MIT-green)

## Why this exists
The Harman Kardon Invoke (2017) is a genuinely *excellent* speaker — a 7-mic far-field array, a 6-driver
amp, and a lovely rotary volume ring. Then Microsoft wound down Cortana: the **2021 OTA stripped the
smarts — and even Wi-Fi** — leaving thousands of these as glorified Bluetooth pucks or e-waste.

This project reclaims the hardware. Boot open Linux on it, restore **WiFi + Spotify/AirPlay** streaming,
**Bluetooth**, the **physical knob / buttons / LED ring**, and make it **Home-Assistant**-friendly — an
open-source version of the machine it once was.

## What it is (and isn't)
- **It is:** documentation + tooling to bring up open Linux on the Invoke and turn it into a usable open
  speaker, standing on the community's reverse-engineering.
- **It is not:** a flash-it-blindly tool. Everything here defaults to **RAM-only, no-NAND, no-brick** —
  the dangerous (persistent) steps are deliberately *separate and gated*.

## The device (myth-busted)
Despite "Cortana," the Invoke is **not** a Windows/Intel device — that's a persistent myth (Cortana ran
as a *Linux* client + a cloud service; the "Windows" requirement was only the setup phone). It's:

| | |
|---|---|
| **SoC** | Marvell **88DE3006** ("Berlin" BG2CDP), dual ARM Cortex-A7 — *same family as Chromecast / Google Home Mini* |
| **WiFi/BT** | Marvell 88W8887 / SD8801 (SDIO) |
| **Audio** | Wolfson/Cirrus **WM8904** codec → 6-driver amp |
| **OS** | Linux **3.8.13** (Yocto/Poky 2.1) |

## Status
- ✅ RAM-boot custom Linux over USB (no NAND writes)
- ✅ WiFi associated + internet proven (DHCP + ping)
- 🔜 Audio out (ALSA / WM8904) → AirPlay → Spotify Connect
- 🔜 Bluetooth A2DP sink · 🔜 rotary/buttons/LED ring · 🔜 Home Assistant `media_player`

## Quick start — wire your own Invoke
👉 **[docs/wire-a-new-invoke-runbook.md](docs/wire-a-new-invoke-runbook.md)** — the repeatable,
multi-checked procedure: enter service mode → RAM-boot → extract firmware → associate WiFi → validate.
**The one gotcha that costs everyone an evening:** use the **108 MB StockRoot image, *not* the 69 MB
OTA2** — the OTA2 is the firmware build that *deliberately removed Wi-Fi*.

## Architecture & roadmap
👉 **[docs/opensource-speaker-architecture.md](docs/opensource-speaker-architecture.md)** — the full
open-source stack (shairport-sync / librespot for streaming, BlueZ + bluez-alsa for Bluetooth, a
single-writer `hk-mcud` daemon for the rotary/LED, squeezelite + Music Assistant for Home Assistant), a
phased build plan, and an honest cross-compile story for the vintage glibc 2.23 / ARMv7 toolchain.

## Deep dive
👉 **[docs/firmware-bringup-research-2026-06-22.md](docs/firmware-bringup-research-2026-06-22.md)** — the
boot chain, where to get firmware, the Wi-Fi root-cause, and the dead-ends to avoid.

## ⚠️ Safety — the hard NEVERs
- **Never flash `99_IMAGE`** — confirmed brick (no U-Boot, needs hardware recovery).
- **Never base on the `12.2134.0` final OTA** — it removes Wi-Fi.
- **No `l2nand` / no NAND writes** for bring-up — stay RAM-only via `hk_usb_boot`.
- Only your **own** device; personal use; **no warranty** — you are responsible for your hardware.

## Credits
This stands on the shoulders of the community's reverse-engineering — above all
**[coggy9/HKHacking](https://github.com/coggy9/HKHacking)**, the canonical Invoke RE (flashing toolkit,
firmware archive, and the Wi-Fi-blocker discovery). Also
[tchebb/chromecast-tools](https://github.com/tchebb/chromecast-tools) (same SoC family) and
[Courk's Google Home Mini deep-dive](https://courk.cc/running-custom-code-google-home-mini-part1).

## License
**MIT** for the documentation + tooling in this repository (see `LICENSE`). This project distributes **no
proprietary firmware** — the Invoke firmware is Harman/Marvell property; we link to the community
archives. Contributions + other-Invoke results welcome.
