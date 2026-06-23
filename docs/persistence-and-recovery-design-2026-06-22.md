---
last_verified: 2026-06-22
category: architecture
---

# HK Invoke — Durable Persistence & Never-Brick Recovery Design

> GOAL: make the revived Invoke run LIVE as a speaker across power-cycles (today it is
> RAM/tmpfs-only and loses everything on reboot), with rich fallbacks and a recovery
> escape-hatch so it can NEVER brick. Posture upgrade: from "defer persistence (brick-risk)"
> to "de-risk persistence with fallbacks, THEN do it."
> Lens: persistence mechanisms + risk. Device facts:
> [[firmware-bringup-research-2026-06-22]] · [[current-state]] · [[wire-a-new-invoke-runbook]].

## TL;DR — the finding that changes the persistence calculus

**The stock firmware already persists data to NAND on every boot, BELOW the bright line, using
filesystems the device itself maintains.** From the device's own extracted `/etc/init.d/rcS`
(recovery-baseline `rootfs-82`):

```
/bin/mount_part --toolbox app          /home/galois         # YAFFS, writable
/bin/mount_part --toolbox localstorage /home/galois_rwdata  # YAFFS, writable
/bin/mount -o remount,rw /home/galois_rwdata
echo 2 > /sys/module/yaffs/parameters/yaffs_auto_checkpoint  # YAFFS on NAND, RW
/bin/mount -o remount,ro /            # rootfs is squashfs, READ-ONLY
```

So the cheapest durable persistence is **NOT** a custom flash and **NOT** a rootfs rewrite — it is
writing our daemons + config + boot-glue into the **already-writable `localstorage`/`app` YAFFS
partitions** and arranging the stock boot to launch them. That is exactly what the stock firmware
does to its own data every day. It never touches the bootloader, kernel, or rootfs partitions.

This collapses the false dilemma "persistence requires a brick-risky NAND/rootfs flash." Tiers P0–T1
below give full reboot-survival with **zero writes to any boot-critical partition** and full
reversibility.

---

## Boot chain & the bright line (verified)

Boot order on the Marvell Berlin BG2CDP, confirmed from Courk's Home-Mini RE (same SoC) + the
Chromecast bootloader dump + the device's own MTD map:

```
[1] Mask ROM (silicon BootROM)  ── immutable, in-chip, CANNOT be written  ── ULTIMATE floor
      │  reads block0 for DDR + NAND-ECC config; honors bootStrap bit (RA_Gbl_bootStrap)
      ▼
[2] block0        (mtd0, 128K)   ── DDR/ECC config the BootROM consumes      ┐
[3] prebootloader (mtd1, 1M)     ── first NAND-resident loader               │  BRIGHT LINE
[4] TZ / TZ-B     (mtd2/3, 4M ea)── TrustZone secure world  (A/B redundant)  │  (NEVER WRITE
[5] postbootloader/-B (mtd4/5)   ── loads + cryptographically VERIFIES kernel│   for bring-up)
                                    (A/B redundant)                          ┘
      ▼
[6] kernel   (mtd6, 8M)          ── secure-boot verified by postbootloader
[7] rootfs   (mtd7, 105M)        ── squashfs, read-only, NO signature check (coggy9 confirmed)
[8] cache    (mtd8, 117M)        ── unused by boot; large; candidate scratch
[9] recovery (mtd9, 15M)         ── recovery image (green/recovery boot)
[10] fts/factory_store/bbt (mtd10/11/12) ── version table, factory, bad-block table
[11] mv_nand (mtd13, 256M)       ── whole-chip aggregate (DO NOT write as a blob)
```

Verified MTD map from the live device (session `20260623T000041Z`):

| mtd | size | name | role | write class |
|---|---|---|---|---|
| 0 | 128K | block0 | BootROM DDR/ECC config | **BRIGHT LINE — never** |
| 1 | 1M | prebootloader | first NAND loader | **BRIGHT LINE — never** |
| 2 | 4M | TZ | TrustZone | **BRIGHT LINE — never** |
| 3 | 4M | TZ-B | TrustZone (B copy) | **BRIGHT LINE — never** |
| 4 | 512K | postbootloader | kernel verifier | **BRIGHT LINE — never** |
| 5 | 512K | postbootloader-B | kernel verifier (B) | **BRIGHT LINE — never** |
| 6 | 8M | kernel | secure-boot-verified | very high risk |
| 7 | 105M | rootfs | squashfs RO (no sig check) | high risk |
| 8 | 117M | cache | unused-by-boot scratch | moderate |
| 9 | 15M | recovery | recovery boot image | moderate (it IS the safety net) |
| 11 | 2M | factory_store | partition version table | high (mount_part depends on it) |

**The bright line = mtd0–mtd5 (block0, prebootloader, TZ/TZ-B, postbootloader/-B).** These are the
operator's non-negotiable never-touch zone, and they are *also* the difference between
"recoverable" and "the 99_IMAGE permanent brick." The 99 brick ("stuck in a loop, no U-Boot
output, hardware tools needed") is the signature of a **corrupted block0/prebootloader** — i.e. it
crossed the bright line. Everything at mtd6 and above is below the brick threshold *as long as a
working block0+prebootloader survives to fall back to USB* (see Recovery Escape-Hatch).

### Secure-boot boundary (why some tiers are off-limits)
- **block0 → prebootloader → TZ → postbootloader → kernel are a cryptographically verified chain.**
  postbootloader "loads and verifies the Kernel image" (Courk). A custom unsigned **kernel** (mtd6)
  will be rejected by secure boot — so Tier (d-kernel) is not just risky, it likely won't boot, and
  the attempt path is the highest brick exposure. **Do not pursue custom kernel.**
- **rootfs (mtd7) squashfs has NO signature check** (coggy9 rebuilt + reflashed it and it booted).
  So a custom rootfs is *technically* bootable — but it is a large NAND write below the kernel,
  which is why it sits at Tier (c), gated, not Tier (a/b).

---

## The recovery escape-hatch (why "never brick" is achievable)

Three independent recovery layers, deepest first. As long as we never write mtd0–mtd5, layers 2–3
remain intact and the device is always recoverable to the known-good RAM-boot state.

1. **Mask-ROM USB (silicon floor, unconditional-ish).** The BootROM honors the `bootStrap` bit /
   the internal mute-equivalent button: "Pushing it at boot time will force the bootloader to boot
   from the USB port" (Courk). The Home-Mini/Invoke present as `BG2CD S/N:12345678A` over USB at
   this stage. **Caveat:** mask-ROM USB on Berlin is *secure-boot gated* — "only signed code can
   theoretically be executed." So the mask-ROM path is a recovery *surface* but not a free
   custom-code path; it is the layer that makes a corrupted-NAND device still enumerate.
2. **Bootloader-level USB fallback (the working path today).** From the Chromecast bootloader dump:
   `if (ret != 0 && Bootmode == BOOTMODE_NORMAL) { "Booting from NAND failed, booting from USB" }`
   plus `if (button_status) "Detected button press -- booting from USB"`. This is the
   `hk_usb_boot` / service-loader (`1286:8174`, "BG2CD S/N:12345678A", `Marvell U-boot ... MV88DE3006`)
   path Joe already uses. **It depends on a working prebootloader on NAND** — which is exactly why
   we never write mtd0–mtd5: keeping them pristine keeps this escape-hatch alive.
3. **On-NAND recovery partition (mtd9, 15M).** A dedicated recovery image + green/recovery boot
   mode ("holding BT and Mic buttons while starting"). Stock-firmware-grade fallback that the
   bootloader selects without host tethering.

**Proven recovery loop we already have:** power-cycle → yellow service mode → `hk_usb_boot` RAM-boots
the 82 U-Boot ramdisk → `usbload` kernel+initrd into RAM → `bootm` → known-good RAM rootfs on WiFi.
This is fully reproducible (sessions `20260621T190459Z` boot-console, `…190904Z` serial inventory)
and writes nothing. **Any tier below must preserve this exact loop as its rollback.**

---

## Persistence tiers — safest → riskiest, each with brick-risk + reversibility

Notation per tier: **WRITES** (what lands on NAND) · **BOOTLOADER?** (does it cross the bright line) ·
**REVERSIBLE?** (how to undo) · **BRICK RISK** · **REVERSIBILITY PROOF** (what to capture to prove undo).

### Tier P0 — Host-orchestrated USB-boot on every power-cycle  ·  ZERO NAND write
The host (Mac, or a dedicated tethered Raspberry Pi / always-on mini-PC) re-runs the proven
RAM-boot + Wi-Fi + audio bring-up automatically whenever the device powers on. The device stays
RAM-only forever; "persistence" lives entirely on the host.

- **WRITES:** none. Device NAND is never touched.
- **BOOTLOADER?:** no. Uses only the bootloader-level USB fallback already in use.
- **REVERSIBLE?:** trivially — unplug the host; the device reverts to stock normal-boot.
- **BRICK RISK:** **ZERO.** Identical write-profile to today's manual bring-up.
- **PROOF:** every boot writes a session artifact under `~/.local/state/hk-invoke/sessions/`; the
  device's `/proc/mtd` shows all partitions `ro`; no `l2nand`/`nand write`/`saveenv` in the command
  file (the wrapper already refuses NAND-writing 79_IMAGE / blocks persistent commands before USB OUT).
- **AUTOMATION TO BUILD (in-bounds, do now):**
  1. **Trigger:** udev/launchd rule watching for the service-loader USB id `1286:8174` (or the
     post-boot ACM `1286:` ttyGS0). On appearance → fire the bring-up.
  2. **Sequencer:** a `hk-invoke-autoboot` service that runs `ram_boot_console.sh <artifact>` →
     feeds `uboot-ram-commands.txt` line-by-line (the bulk-paste-times-out lesson) → on the ACM
     shell, stages firmware blobs, `insmod` SD8801 + alias, `wpa_supplicant` + `udhcpc`, then audio
     module-load and the player stack.
  3. **Power-on detection without host poke:** the device only enters *yellow service mode* via the
     reset+4×mic ritual, so true unattended power-cycle survival needs either (a) the device left in
     a state where normal-boot is acceptable + host only re-arms on demand, or (b) a hardware aid
     that asserts the boot-from-USB strap/button at power-on (Tier P0+).
- **VERDICT:** **IN BOUNDS. Ship first.** This is the durability win with literally zero brick
  exposure. It is "tethered persistence," and for a fixed speaker on a shelf next to a $35 Pi that
  is a perfectly good product posture. It also de-risks every higher tier by being the rollback.

#### Tier P0+ — hardware-assisted unattended USB boot (still zero NAND write)
Add a tiny tethered helper (Pi Pico / Pi GPIO) that, on power-on, drives the internal
boot-from-USB button / `bootStrap` line so the device enters USB-download without the manual
reset+mic ritual, then the host streams the RAM image. Requires opening the case to tap the button
pad (per Courk, the USB-boot button "is not accessible without cracking the case open").

- **WRITES:** none to NAND. **BOOTLOADER?:** no. **REVERSIBLE?:** yes — remove the wire.
- **BRICK RISK:** ~zero electrically (a GPIO tap on an existing button); the only risk is physical
  (soldering). **VERDICT: IN BOUNDS** if Joe wants unattended power-cycle survival without any flash.
  This is the highest-durability **zero-NAND-write** option and the recommended target if tethering
  is acceptable.

### Tier T1 — Writable DATA overlay on the device's OWN writable partitions  ·  data-only NAND write
Reuse exactly what the stock firmware reuses: the **`localstorage` (→`/home/galois_rwdata`) and
`app` (→`/home/galois`) YAFFS partitions** that rcS already mounts read-write. Put our daemons,
configs, blobs, and a launch script there. The kernel + rootfs + bootloader are untouched.

- **WRITES:** files into the existing `localstorage`/`app` YAFFS partitions — i.e. data partitions
  the stock OS writes to on every boot. **No partition is created, erased, reformatted, or moved.**
- **BOOTLOADER?:** **no** — nowhere near mtd0–mtd5; nowhere near kernel(mtd6)/rootfs(mtd7).
- **REVERSIBLE?:** **yes, fully.** `rm` the files we added (or `flash_erase`/reformat just that one
  data partition, which is non-boot). Worst case: the recovery partition + service-loader rebuild it.
- **BRICK RISK:** **VERY LOW.** Equivalent to the device saving its own settings — power-loss during
  a YAFFS write at worst corrupts *that data partition* (recoverable by reformat or recovery boot),
  never the boot chain. YAFFS is log-structured + has the auto-checkpoint the stock OS enables.
- **PROOF OF REVERSIBILITY:**
  - **Before:** dump the data partition first (`nanddump`/`dd` of mtdblockN for `localstorage`) to a
    host file → restorable byte-image. (Read-only; safe.)
  - **Invariant:** `/proc/mtd` shows mtd0–mtd7 unchanged size/`ro`; only the data mtd shows writes.
  - **After:** restore by writing the saved image back to the *same data* partition, or delete files.
  - **Independence:** even a total loss of that data partition leaves a bootable stock system.
- **TWO sub-variants:**
  - **T1a — direct files in the existing YAFFS data partition.** Stock boot already runs rcS, which
    can be made (via the next layer or via the existing `localstorage` autostart hooks the stock OS
    honors) to launch `/home/galois_rwdata/hk-speaker/start.sh`. **Lowest risk; preferred.**
  - **T1b — overlayfs upperdir on the data partition over the squashfs rootfs.** Mount an
    overlay (`lowerdir=squashfs-ro`, `upperdir`+`workdir` on the YAFFS data partition) so the whole
    rootfs *appears* writable while the squashfs stays byte-for-byte intact. This is the canonical
    embedded "read-only squashfs + writable overlay" pattern. Risk identical to T1a (overlay
    upperdir is just files in the data partition); the only addition is an overlay mount in early
    userspace. **Reversible** by not mounting the overlay; squashfs is never modified.
- **DEPENDENCY / OPEN QUESTION:** T1 needs the **stock normal-boot to actually run and to launch our
  start script**, OR it is combined with Tier P0 (host RAM-boot) where the start script lives on the
  data partition and the RAM init sources it. The cleanest in-bounds combo is **P0 + T1**: host
  RAM-boots a known-good kernel/rootfs (zero NAND write), and that RAM rootfs reads its persistent
  config/daemons/blobs from the on-NAND `localstorage` data partition (small, reversible data write).
  That gives "config + media survive reboot" with the bootloader/kernel/rootfs entirely pristine.
- **VERDICT:** **IN BOUNDS, gated by a one-time data-partition backup.** This is the recommended way
  to make *state* durable. Pair with P0/P0+ for the runtime.

### Tier T2 — Custom rootfs written to the rootfs partition (mtd7), bootloader + recovery intact
Rebuild the squashfs (it has **no signature check**) with our daemons baked in and flash *only*
mtd7 via the CRC-validated `l2nand` path, leaving mtd0–mtd6, mtd8, mtd9 untouched. coggy9 did
exactly this (custom startup audio) and it booted + persisted.

- **WRITES:** the whole rootfs partition (mtd7, 105M) is erased + rewritten.
- **BOOTLOADER?:** **no** — mtd7 is below the verified kernel; the bright line is not crossed.
  **BUT** it requires the NAND-writer (`l2nand`), which the project currently forbids for bring-up.
- **REVERSIBLE?:** **yes, in principle** — reflash the stock StockRoot rootfs back to mtd7. Requires a
  byte-verified stock `83_IMAGE` rootfs slice on hand + a successful write.
- **BRICK RISK:** **MODERATE.** Not from crossing the bright line (it doesn't), but from the write
  itself: power-loss / USB-flake / wrong-slice mid-`l2nand` corrupts the rootfs partition. A
  corrupted *rootfs* (not bootloader) is still recoverable via the service-loader RAM-boot +
  reflash, so it's "soft-brick → recoverable," **provided mtd0–mtd5 stay pristine.** The real danger
  is operator error reaching for the wrong partition/image (the 99_IMAGE class of mistake).
- **PROOF OF REVERSIBILITY:**
  - Pre-write: `nanddump` mtd7 (current rootfs) AND confirm a byte-verified stock rootfs image on host.
  - Post-write: boot; if it fails, RAM-boot via service-loader (escape-hatch layer 2) and reflash the
    saved mtd7 image. Capture both the dump and the reflash transcript.
  - Hard rule: target mtd7 **by name via `mount_part`'s version table / the partition spec**, never a
    raw offset, never `mv_nand` (mtd13) as a blob.
- **VERDICT:** **OUT OF BOUNDS for now** (it needs an `l2nand` NAND write, which the rails forbid for
  bring-up). Re-evaluate ONLY after P0+T1 are proven AND a full recovery dry-run (deliberately
  RAM-recover from a "bad rootfs" simulation) has succeeded. Even then, prefer the recovery
  partition (mtd9) as a self-test target before mtd7.

### Tier T3 — Custom kernel (mtd6) and/or A/B repartition  ·  near the forbidden zone
Writing a custom kernel, or restructuring partitions, or touching TZ/postbootloader to add A/B
rootfs logic.

- **WRITES:** mtd6 (kernel) and/or repartition affecting bootloader-adjacent areas.
- **BOOTLOADER?:** kernel(mtd6) is **secure-boot verified by postbootloader** — an unsigned kernel
  is **rejected**, so it likely won't boot AND the attempt is the highest brick exposure. Any
  repartition or TZ/postbootloader edit **crosses the bright line.**
- **REVERSIBLE?:** kernel reflash *might* be (if signed stock kernel is reflashable), but the
  secure-boot rejection makes custom kernels a dead end without the signing keys. Bright-line edits
  are the **99_IMAGE-class permanent brick** — not reliably reversible without hardware tools.
- **BRICK RISK:** **HIGH → CATASTROPHIC.**
- **VERDICT:** **FORBIDDEN.** No custom kernel, no repartition, no TZ/bootloader writes. The kernel
  we need is the stock secure-boot-verified one (RAM-booted in P0, or in-place in T1/T2).

### Tier T4 — Full custom flash (mv_nand blob / re-laying the whole chip)
Writing mtd13 (`mv_nand`, the 256M aggregate) or a full multi-partition image including
block0/prebootloader.

- **WRITES:** everything, including the bright line. **BOOTLOADER?:** **yes, by definition.**
- **REVERSIBLE?:** **no** — this is exactly the 99_IMAGE failure mode. **BRICK RISK: CATASTROPHIC.**
- **VERDICT:** **HARD FORBIDDEN.** Never.

---

## Recommendation (given never-brick + never-touch-bootloader rails)

**In-bounds, ship in this order:**

1. **Tier P0 (zero NAND write) — build the host-orchestrated autoboot now.** udev/launchd trigger on
   `1286:8174` → `hk-invoke-autoboot` sequencer that replays the proven RAM-boot + Wi-Fi + audio
   bring-up. Durability with literally zero brick exposure, and it becomes the rollback for every
   higher tier. (Companion to the existing `host-audio-fallback` graceful-degradation path.)
2. **Tier P0+ (zero NAND write) — if unattended power-cycle survival is wanted**, add the GPIO/Pico
   helper that asserts the internal boot-from-USB button at power-on. Highest durability with no
   flash. Requires opening the case (button pad tap) but no NAND write.
3. **Tier T1 (data-only, reversible NAND write) — make STATE durable** by writing config/daemons/
   blobs to the device's own already-writable `localstorage`/`app` YAFFS partitions (T1a preferred;
   T1b overlay if a writable-rootfs illusion is needed). **Gate:** a one-time read-only `nanddump`
   backup of that data partition first, so reversibility is byte-proven. Combine as **P0 + T1**: RAM
   runtime + on-NAND persistent state, bootloader/kernel/rootfs pristine.

**Out of bounds under the current rails (revisit only after the above are proven + a recovery
dry-run passes):**
- **Tier T2** (custom rootfs to mtd7) — moderate, reversible-in-principle, but needs an `l2nand`
  NAND write the rails currently forbid. Earliest candidate to *graduate* once recovery is rehearsed.

**Forbidden, full stop:**
- **Tier T3** (custom/unsigned kernel — secure-boot rejects it anyway; repartition; TZ/postbootloader).
- **Tier T4** (full flash / mv_nand blob / any block0/prebootloader write) — the permanent-brick zone.

### Why this satisfies "durable + richer fallbacks + runs live, better" without crossing a rail
- **Durable:** P0/P0+ give full runtime survival across power-cycles; T1 gives config/media survival —
  both without touching kernel, rootfs, or bootloader.
- **Richer fallbacks:** three independent recovery layers (mask-ROM USB → bootloader USB fallback →
  on-NAND recovery partition) PLUS the host autoboot as a software fallback PLUS the existing
  `host-audio-fallback` so the speaker degrades to "host plays over Bluetooth" if device bring-up
  fails.
- **Never bricks:** the only writes performed (P0: none; T1: data partition only) cannot corrupt the
  boot chain; mtd0–mtd5 stay pristine, so the bootloader-USB and mask-ROM recovery surfaces always
  remain. The known-good RAM-boot loop is the universal rollback.

---

## Concrete next builds (all in-bounds)

1. **`hk-invoke-autoboot` service (P0):** launchd agent + udev-equivalent USB watcher keyed on
   `1286:8174`; on appearance, run `ram_boot_console.sh` against the latest verified
   `no-nand-initramfs` artifact, then the post-boot bring-up. Writes session artifacts; never sends a
   NAND/env write command (reuse the existing wrapper's refusal of 79_IMAGE + persistent commands).
2. **Read-only data-partition backup helper (T1 gate):** `no_nand_serial_inventory.py`-style helper
   that, from the RAM shell, `nanddump`s the `localstorage`/`app` mtdblock to the host, byte-verifies,
   and records a restore command. **Read-only on the device; pure safety capture.** This is the
   prerequisite that makes T1 reversibility *proven*, not asserted.
3. **`localstorage` persistence layout + start.sh (T1a):** define
   `/home/galois_rwdata/hk-speaker/{bin,etc,firmware,start.sh}`; the RAM init (P0) sources start.sh
   after mounting the data partition. One small, reversible data write; bootloader/kernel/rootfs
   untouched.
4. **Recovery dry-run rehearsal (graduation gate for T2):** deliberately simulate a "bad rootfs"
   *in RAM only*, confirm the service-loader RAM-boot recovers it end-to-end, and capture the
   transcript. Only after this passes do we even discuss writing mtd7.
5. **`hk-invoke recover` escape-hatch doc + one-button script:** wrap the proven
   power-cycle → yellow → `hk_usb_boot` → RAM-boot loop into a single operator command so recovery is
   trivial under stress. (Crisis-recovery posture: the recovery path must be the easiest path.)

## Hard NEVERs (carried forward, unchanged)
- Never flash `99_IMAGE`; never base on `12.2134.0` OTA2 (removes Wi-Fi); never write
  mtd0–mtd5 (block0/prebootloader/TZ/TZ-B/postbootloader/-B); never write mtd13 (`mv_nand`) as a blob;
  never custom/unsigned kernel; never `saveenv`/`l2nand`/`nand erase`/`nand write` for bring-up.
- PSK and secrets: transient mode-600 env only; never committed, logged, or notified.

## Sources
- Device truth (this repo): `docs/firmware-bringup-research-2026-06-22.md`,
  `docs/current-state.md`, live MTD map session `~/.local/state/hk-invoke/sessions/<session>…`,
  extracted stock `rootfs-82/etc/init.d/rcS` + `rc.sysinit` + `bin/mount_part` strings.
- coggy9/HKHacking — Discussion #3 (image layout / l2nand / `hk_usb_boot` / squashfs no-sig),
  Discussion #11 (final OTA), README (brick warnings): https://github.com/coggy9/HKHacking
- Marvell Berlin boot chain & USB recovery: Chromecast Marvell bootloader dump
  (https://pastebin.com/3c1BUieq — bootStrap bit, NAND-fail→USB fallback, button-forced USB boot);
  Courk, "Running Custom Code on a Google Home Mini" (https://courk.cc/running-custom-code-google-home-mini-part1
  — block0 BootROM role, secure-boot verified chain, internal USB-boot button, signed-code-only at mask ROM);
  Chromecast force-USB-boot recovery (https://xdaforums.com/t/howto-force-chromecast-to-boot-from-usb-possible-brick-recovery-method.2438715/).
- Persistence patterns: squashfs+overlayfs on embedded NAND
  (https://magazine.odroid.com/article/using-squashfs-as-a-read-only-root-file-system/,
  https://medium.com/@akashsainisaini/how-overlayfs-and-squashfs-power-embedded-linux-storage-75273028ef20,
  http://souktha.github.io/misc/squashfs-ubi/); U-Boot bootcount/altbootcmd failsafe
  (https://docs.u-boot.org/en/latest/api/bootcount.html — relevant only if a future tier ever
  touched env, which the rails forbid; documented for completeness).
