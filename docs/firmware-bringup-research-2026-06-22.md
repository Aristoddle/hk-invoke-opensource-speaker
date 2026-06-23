---
last_verified: 2026-06-22
category: research
---

# HK Invoke — Firmware & Low-Level Bring-Up Research (multi-source web research)

> Exhaustive multi-source research to get the device "rolling." Resolves the device identity, finds all
> obtainable firmware, and **root-causes the Wi-Fi/USB-net wall**. Source: workflow w8nk7uqec.

## TL;DR — the highest-value finding
Your "Wi-Fi/DHCP/USB-net not up" on the RAM-only (82_IMAGE) boot is the **exact** blocker HKHacking
Discussion #5 documents: in the U-Boot-ramdisk boot **only `lo` comes up** — the device runs
`/etc/init.d/rcS` but **NOT `/etc/init.d/rc.sysinit`**, and the entire Wi-Fi module-load + firmware +
association chain lives in `rc.sysinit`. dmesg shows `mmc0: new high speed SDIO card` → the **88W8887
SDIO enumerates fine**; the gap is **driver-load + firmware-blob + userspace-init, not hardware**.
**Fix = replicate what `rc.sysinit` skips** (steps in §5).

## 1. Device truth — CONFIRMED (HIGH, 6/6 briefs converge)
Marvell-Berlin / Yocto-Linux, **not** Intel/Windows (that's a myth — Cortana ran as a Linux client +
cloud service; the Windows requirement was the *setup phone*, never the speaker; no variant, not reflashed).
- **Compute module:** Libre Wireless **LS9ADAC11DBT** (FCC `2ADBM-LS9ADAC11DBT`) — Google-Home-Mini-class.
- **SoC:** Marvell **88DE3006** = ARMADA 1500 Mini Plus = **BG2CDP "Galois"/Berlin**, dual Cortex-A7 ~1.2GHz ARMv7. Same DPU as Chromecast v1/v2/Audio + Home Mini.
- **Wi-Fi/BT:** Marvell Avastar **88W8887** (=SD8887), SDIO `02df:9135` — matches your `bt8xxx.ko`/`bt_sd8887`/`btmrvl`.
- **Audio:** **WM8904** codec + BSP-only `snd-soc-berlin` machine driver; 7-mic array, 6-driver amp.
- **Kernel/build:** Linux **3.8.13**, Yocto/Poky **2.1 "Krogoth"**, glibc 2.23, `arm-poky-linux-gnueabi`.
- **NAND** 256MB (mtd16), rootfs mtd11 ~169MB. **FCC `APIHKCT500A`, model 6132A.**

## 2. Firmware/artifact set — DOWNLOAD NOW (Harman upstream rots; mirror locally)
- **StockRoot `83_IMAGE`** (107.9MB) — **THE base for custom use**: pre-rooted, network-ADB on, OTA blocked. Last Cortana-era build *with* adb-over-network AND complete network userspace. → `github.com/coggy9/HKHacking/releases/download/StockRoot/83_IMAGE`
- **HarmanFlash `Harman.Kardon.INVOKE.Flashing.zip`** (263MB) — working `hk_usb_boot`/`usb_boot`, `l2nand`, `Mrvl_WinUSB`, `run.sh`, the 70/81/82/83/99 image set. Run `run.sh` as root on **Linux**. → `…/releases/download/HarmanFlash/Harman.Kardon.INVOKE.Flashing.zip`
- **Invoke-kernel.tar** (~520MB, archive.org/details/invoke-kernel) — the **BSP**: out-of-tree `mlan`/`sd8887` + `bt8xxx` drivers, `snd-soc-berlin`, Berlin DTS, `request_firmware()` calls. *Resolves exact module + blob names.*
- **Invoke Source Disclosure.tar** (110MB, archive.org/details/HK-Invoke-source-disclosure) — userland ONLY (stock `rc.sysinit`/`rcS`, wpa_supplicant, U-Boot env). No drivers/firmware here.
- **FinalOTA 12.2134.0** — ⚠️ **ships a wifi-blocker binary that kills Wi-Fi**. Only mine it for the BT blob; do NOT base on it.
- Boot-image tooling for the BG2CDP family: `github.com/tchebb/chromecast-tools` (cc-make/mangle-bootimg). Boot chain Rosetta Stone: Nest `berlin_tools/bootloader/bootloader.c`.

## 3. Canonical community RE project (your upstream/sibling)
**`github.com/coggy9/HKHacking`** — README `Devices/Invoke/Readme.md`; Discussions **#3** (image layout/MTD/hk_usb_boot), **#5** (the Wi-Fi blocker = your wall), **#7** (rooted/ADB/mcu-interface), **#8** (Yocto toolchain), **#11** (final OTA); Issue **#1** (service-port USB ID). Sister deep-dive: Courk's "Running Custom Code on a Google Home Mini" (same SoC; documents the secure-boot wall).

## 4. Boot/flash primitives (no-NAND-safe path)
- **Enter flashing mode:** hold reset (pinhole) while powering on → tap mic-mute **4× within 5s** → LED ring **YELLOW** = flashing mode (your "service-loader").
- **Two USB-boot modes (don't conflate):** (A) **BootROM device-mode download** = what `hk_usb_boot` uses, **NAND-SAFE**, Marvell USB `1286:8001` (service port `1286:8174`); (B) bootloader host-mode reads a boot image off a USB stick (`BOOTMODE_BOOTUSB`).
- **U-Boot console** is tunneled **over the USB service port** (not a clean TTL header). `mload` = load image into RAM (no-NAND); `l2nand` = the ONLY NAND-writer (CRC-validated) — **avoid for RAM-only**. Your observed "NAND-write safety filter" = the documented constrained-recovery loader, expected.
- **Image inventory:** 81=kernel uImage · **82=U-Boot RAM-disk dev image (small → `mload`-able = the no-NAND vehicle)** · 83=full system (too big for RAM, NAND-only) · **99=BRICKS THE INVOKE — never flash**.
- **Security:** the 83 rootfs squashfs has **no signature check** (coggy9 rebuilt+reflashed it, booted) → you can freely modify+reflash the rootfs. Bootloader/kernel may be secure-boot (per Home Mini) — stay on the rootfs layer.

## 5. THE FIX — Wi-Fi on the no-NAND RAM boot (replicate `rc.sysinit`)
1. **Populate `/lib/firmware/mrvl/`** with the device's OWN blobs (NOT upstream linux-firmware — out-of-tree `mlan` is version-picky; mismatch = silent association failure): `binwalk -e StockRoot/83_IMAGE` → mount the squashfs → copy `lib/firmware/mrvl/*.bin` (WLAN blob + the **separate** BT blob `sd8887_bt_a2_new.bin`).
2. **Read the device's own `/etc/init.d/rcS` + `rc.sysinit`** (from the same mount) — the authoritative insmod/mount/wpa sequence. Mount `/lsync` from `/dev/mtdblock11` if the stack needs it.
3. **Load the out-of-tree Marvell SDIO stack in order:** `insmod mlan.ko` then `insmod sd8887.ko` (a.k.a. `sd8xxx.ko`) with `drv_mode=` (Bit0=STA) + `fw_name=mrvl/sd8887_uapsta.bin`. **Confirm exact names** by grepping `invoke-kernel.tar` driver Makefiles + `request_firmware()`.
4. `ip link set wlan0 up` → `wpa_supplicant -i wlan0 -c <conf>` → `udhcpc -i wlan0` (+ CRDA regdomain as rc.sysinit sets).
5. **BT:** `insmod bt8xxx.ko` + `sd8887_bt*.bin` → `hciconfig hci0 up`.

**USB-net fallback (likely faster):** dmesg shows a USB gadget controller → if `CONFIG_USB_GADGET`/`g_ether` is in the kernel (check `invoke-kernel.tar` `.config`), `modprobe g_ether` → `usb0` → `udhcpc -i usb0`. If host-only, use a USB-Ethernet dongle (`asix`/`cdc_ether`).

## 6. Flash decision — VERIFIED SAFETY (2026-06-22, primary sources)
**RECOMMENDATION: do NOT flash. Use the no-NAND host-extraction path (§5).** Independent verification
(HKHacking README + Discussion #3 + search) shows a NAND flash carries REAL brick risk that is **NOT
trivially reversible**: flashing a wrong/incompatible image — esp. **`99_IMAGE`, CONFIRMED brick** ("the
unit appeared to be stuck in a loop with no U-Boot output, likely requiring hardware tools for recovery")
— needs hardware intervention. Even a *correct* flash is a NAND write exposed to power-loss / wrong-image
/ USB-flake failure. **My earlier "one reversible NAND write" framing OVERSTATED the safety — retracted.**

**The safe path needs NO flash at all.** `binwalk` runs on the **HOST** against the downloaded `83_IMAGE`
("A full dump of the system can be obtained by running binwalk on `83_IMAGE`" — coggy9 README) → extract
the firmware blobs + `rc.sysinit` → feed into the existing RAM boot (§5). **Zero device write = zero brick
risk.** WiFi works because StockRoot **11.1842.0** predates the **2021-03-07 final OTA that removed WiFi +
Cortana** (CONFIRMED) — NEVER flash or base on that final OTA (`12.2134.0`).

Flashing is a **last resort only** — if the no-NAND bring-up fully exhausts AND you accept the brick risk;
even then: exact StockRoot `83_IMAGE` only, stable power, driven through `hk_usb_boot` (NAND-safe
device-mode), never hand-run `l2nand`, and never `99_IMAGE`.

## 7. Audio + custom userspace (after network)
- Load `snd-soc-wm8904` + the BSP Berlin machine driver (from `invoke-kernel.tar` `sound/soc/berlin/`); match WM8904 I2C addr + MCLK/FLL clocking to the BSP DTS; `aplay -l` should show the card. Stock `music-test` binary plays to the speaker (patch out its volume-to-max spam). LEDs/buttons via the `mcu-interface` serial protocol (reverse from logcat).
- **Toolchain:** glibc 2.23 → Poky/Yocto **2.1 "Krogoth"** SDK (2.4/2.5 too new). Quick shell: static-musl busybox. Build kernel modules against `invoke-kernel.tar`, not generic headers.

## 8. Dead-ends to avoid
Never flash **99_IMAGE** (bricks). Don't base on **12.2134.0** (wifi-blocker). Not `kwboot`/`WtpDownload`/
A3700-utils (Armada/Kirkwood, not Berlin). Source-Disclosure.tar is userland-only. Ignore any
"Qualcomm/Atheros Wi-Fi" (a single hallucinated FCC-PDF read) or "Intel/Windows-IoT" source.

## 9. Honest open gaps
No confirmed physical TTL-UART pad pinout/voltage (USB service-port console is the working route; ttyS0 is
115200 8N1 at 1.8/3.3V if pads found — board photos `InvokeMainBoard1/2.png` + FCC internal photos are the
leads). JTAG suspected-not-confirmed. RAM-booting a full *custom* rootfs unsolved (83 won't `mload` — repackage
via cpio into legacy uImage ramdisk). OTA signature format not RE'd (use the U-Boot/USB reflash path which
bypasses OTA signing). Verify on-device: RAM (`/proc/meminfo`), MTD (`/proc/mtd`), gadget-vs-host USB.
