---
last_verified: 2026-06-23
category: architecture
---

# HK Invoke — Recovery / Escape-Hatch Stack (the belt-and-suspenders that makes persistence safe)

> LENS: recovery, not bring-up. This is the fallback ladder that has to exist **before** the
> first byte is ever written to NAND. The deliverable is a design where even a *bad* persistence
> write is undoable. Device facts: [[firmware-bringup-research-2026-06-22]]. Bring-up:
> [[wire-a-new-invoke-runbook]]. Durability target: persistent boot across power-cycles WITHOUT
> crossing the operator brick-lines (never 99_IMAGE, never base on OTA2, never block WiFi, never
> unrecoverable NAND/bootloader writes for bring-up).

## 0. The single most important finding (read this first)

**The Invoke's unbrickable anchor is the Marvell BootROM (iROM / mask ROM), not anything on NAND —
BUT that anchor is only unbrickable while `block0` + the bootloader partitions stay unwritten.**

Two facts, both load-bearing, both verified this session:

1. **`hk_usb_boot` talks to the silicon mask ROM, before NAND is read.** The repo's own
   `scripts/hk-invoke/hk_usb_boot.c` (line 2-3, `send_stage1()` at :603) shows the host first sees
   the device as **iROM subclass `ff`, VID:PID `1286:8174`**, sends `bcm_erom.bin.usb` (the early
   ROM / DDR-init stage) to it, the device **re-enumerates to a "normal" subclass**, and *then* the
   host serves U-Boot into RAM. The iROM is in ROM (silicon); it runs before `block0`/prebootloader.
   This is why RAM-boot is inherently NAND-safe: nothing on NAND is required to enter recovery, and
   nothing on NAND is written by recovery. Unlike Courk's locked Google Home Mini (same SoC, where
   "only signed code can theoretically be executed" over USB — courk.cc), the Invoke's iROM-USB path
   **empirically accepts the unsigned `bcm_erom.bin.usb`** (proven by every RAM-boot session in
   `~/.local/state/hk-invoke/sessions/`). The Invoke's secure-boot is **not enforced on the
   iROM-USB-download path** (consistent with HKHacking's finding that the 83 rootfs squashfs has no
   signature check). This is the escape hatch.

2. **The 99_IMAGE brick proves the anchor is NOT absolute — it can be destroyed by writing the wrong
   partitions.** coggy9's 99_IMAGE flash left the unit "stuck in a loop with no U-Boot output … and
   wouldn't respond to either normal startup or the alternate bootloader mode" — i.e. **even the
   USB-loader mode did not come back** (HKHacking Discussion #3). A pure rootfs corruption would still
   leave iROM-USB working; the only way to kill iROM-USB is to corrupt what the BootROM itself reads
   on the way to bringing up DDR/USB — `block0` (DDR/ECC config the BootROM consumes) and/or
   `prebootloader`. 99_IMAGE was big/wrong enough that `l2nand` overran into those.

**The bright line falls out of this directly:** the recovery stack is unbrickable **iff** every
persistence write stays strictly inside `rootfs`/`cache`/`fts` and **never** touches
`block0`, `prebootloader`, `TZ`, `TZ-B`, `postbootloader`, `postbootloader-B`, `recovery`, `bbt`,
or `factory_store`. Keep those frozen and the iROM escape hatch is always there. Touch them and you
are in coggy9-99_IMAGE territory.

## 1. The NAND partition map (the terrain — verified)

Canonical layout from the device's own `/proc/mtd` (current-state.md) and the bootargs `mtdparts` the
project already passes read-only (`build_no_nand_initramfs.py:992`). 256 MiB NAND:

| # | Partition          | Size      | Role                                              | Recovery class       |
|---|--------------------|-----------|---------------------------------------------------|----------------------|
| 0 | `block0`           | 128 K     | BootROM-read DDR/ECC config                       | **NEVER WRITE** (RED)|
| 1 | `prebootloader`    | 1 M       | low-level HW init (post-BootROM)                  | **NEVER WRITE** (RED)|
| 2 | `TZ`               | 4 M       | TrustZone secure world                            | **NEVER WRITE** (RED)|
| 3 | `TZ-B`             | 4 M       | TrustZone backup (A/B)                            | **NEVER WRITE** (RED)|
| 4 | `postbootloader`   | 512 K     | U-Boot; loads+verifies kernel                     | **NEVER WRITE** (RED)|
| 5 | `postbootloader-B` | 512 K     | U-Boot backup (A/B)                               | **NEVER WRITE** (RED)|
| 6 | `kernel`           | 8 M       | Linux 3.8.13-mrvl uImage                          | freeze for bring-up  |
| 7 | `rootfs`           | 105 M     | squashfs system (no sig check)                    | **persistence target** (AMBER) |
| 8 | `cache`            | ~114 M*   | scratch/cache                                     | safe-ish overlay home (GREEN)  |
| 9 | `recovery`         | 15 M      | recovery slot                                     | **NEVER WRITE** — keep as a fallback (RED) |
|10 | `fts`              | 512 K     | factory/test settings                             | NEVER WRITE (RED)    |
|11 | `factory_store`    | 2 M       | factory data (MACs, calibration)                  | **NEVER WRITE** (RED)|
|12 | `bbt`              | 1 M @255M | bad-block table                                   | **NEVER WRITE** (RED)|
|   | `mv_nand`          | 256 M     | whole-device aggregate (= all of the above)       | dump target          |

\* `cache` shows as `117120K` in the bootargs string; treat the printed `/proc/mtd` size as truth on
the live device.

Note the A/B redundancy on TZ and postbootloader — the SoC was designed with bootloader
fail-over. We are **not** going to exploit that for writes (too close to the red line); we note it
because it means the resilient boot stages already have a built-in spare. Our job is to never need it.

## 2. The escape-hatch ladder (ranked: lowest-level + most reliable first)

Ranking criterion = "how early in the boot chain does it intervene, and how few preconditions does it
need." Lower number = harder to defeat = more reliable.

### Hatch 1 — iROM / BootROM USB download (the floor; mask-ROM-level)
- **What:** silicon BootROM enters USB download mode as `1286:8174` subclass `ff`; accepts
  `bcm_erom.bin.usb` then a host-served U-Boot+ramdisk into RAM. **Reads nothing from NAND, writes
  nothing to NAND.**
- **Trigger:** hold reset (pinhole) while applying power → tap mic-mute 4× within 5 s → LED ring
  YELLOW → USB connect. (`hk_usb_boot auto <image-dir>` drives it.)
- **Reliability:** highest. Survives a wiped/corrupt `rootfs`, `kernel`, even `postbootloader`/U-Boot,
  because the BootROM itself runs first and does not depend on them.
- **Defeated only by:** corruption of `block0`/`prebootloader` (what the BootROM consumes to bring up
  DDR + USB). That is exactly the 99_IMAGE failure. **Therefore protecting `block0`/`prebootloader` is
  protecting Hatch 1 itself.**
- **Status:** PROVEN repeatedly this session. This is the always-available escape hatch the whole
  durability plan rests on.

### Hatch 2 — RAM-only re-boot of the golden RAM image (operational floor; today's normal mode)
- **What:** the exact path the device runs today — `hk_usb_boot` → custom 82 ramdisk → RAM-only
  StockRoot userspace + WiFi. If a *persistent* boot ever misbehaves, you power-cycle, ignore NAND,
  and RAM-boot the known-good image. This is the "it never bricks because the good config lives on the
  host" property — non-persistent, but always recoverable.
- **Reliability:** as high as Hatch 1 (it *is* Hatch 1 plus a known-good payload). It is the rollback
  for any persistence attempt: persistence broke boot? → unplug → RAM-boot → device is back, full WiFi.
- **Precondition:** the host has the byte-verified golden RAM artifact (the generated
  `image-dir/` + `uboot-ram-commands.txt`). Keep it under version control / `recovery-baselines/`.

### Hatch 3 — U-Boot console over the USB service port (mid-level; the surgical tool)
- **What:** once Hatch 1 has the device at `MV88DE3100|>`, the U-Boot console is tunneled over USB
  (interrupt endpoint, `hk_usb_boot.c:704`). From here you can `nand read` any partition into RAM and
  `md.b`-dump it (the dump path, §3) — and, in the gated persistence phase, `nand write` **only**
  `rootfs`. The native console (`ram_boot_console.sh`) and `hk_usb_boot` **both** refuse the NAND-write
  command classes by default (`build_no_nand_initramfs.py:51` FORBIDDEN list; `ram_boot_console.sh:47`
  regex), so this hatch is read-only until a write is *explicitly* unlocked.
- **Reliability:** high, but depends on a working U-Boot — which depends on `postbootloader` — so it is
  *below* Hatch 1/2 (those don't need on-NAND U-Boot at all). For dump/restore it's the workhorse.

### Hatch 4 — `recovery` MTD partition (15 M) as a never-written fallback slot
- **What:** the SoC layout includes a dedicated 15 M `recovery` partition. We treat it as a **frozen,
  never-written golden recovery slot.** If StockRoot ships a recovery payload there, preserve it; if
  not, it remains a reserved escape slot we deliberately do not consume.
- **Reliability:** medium and currently *unproven* (HKHacking found "no separate recovery mode … as
  functional"). **Do not rely on it as a live recovery path.** Its value is negative: by never writing
  it, we keep one more on-NAND fallback intact and keep the brick surface small. Promote it to a real
  hatch only after dumping it and confirming what it holds.

### Hatch 5 — serial console as the lowest-level diagnostic (not a recovery *write* path)
- **What:** the USB ACM root shell (`/dev/cu.usbmodemno_nand_probe_*`) on a RAM boot, and (if TTL-UART
  pads are ever found: `ttyS0` 115200 8N1 @ 1.8/3.3 V — board photos are the lead, §9 of the research)
  the raw kernel console. This is for *seeing* what happened (dmesg, `/proc/mtd`, boot args), not for
  recovering by itself.
- **Reliability:** as a visibility tool, highest. As a recovery actuator, it just gives you a shell
  from which to run Hatch 3 operations.

### Hatch 6 — JTAG / hardware NAND tools (the true last resort; the brick floor)
- **What:** if `block0`/`prebootloader` are corrupted and Hatch 1 is dead (the 99_IMAGE state),
  recovery needs physical tools: JTAG (suspected-not-confirmed on this board) or desoldering /
  external NAND programmer to re-write `block0`+`prebootloader` from a golden dump.
- **Reliability:** this is the floor that makes a golden whole-NAND dump worth having even in the
  worst case — but it requires hardware skill + the dump. **The entire point of Hatches 1-5 + the
  bright line is to never reach Hatch 6.**

**Ladder summary:** 1 iROM-USB (silicon) > 2 RAM-boot golden image (silicon + host payload) > 3 U-Boot
console (needs postbootloader) > 4 recovery partition (unproven; keep frozen) > 5 serial (visibility) >
6 JTAG/hardware (last resort, needs the golden dump). Persistence is safe because hatches 1+2 are below
every NAND write we would ever make.

## 3. STEP ZERO — full NAND golden dump BEFORE any write (the non-negotiable precondition)

No persistence write happens until a verified, whole-device NAND backup exists on the host. This is the
single gate. Two dump methods; do BOTH if possible (independent verification).

### Method A — on-device `nanddump` from the RAM-booted root shell (preferred: gets OOB+ECC right)
Run from the live RAM-only root shell (the device already reaches `uid=0(root)` over USB ACM). Dump
**each** partition AND the whole aggregate, with out-of-band + raw so a future restore is bit-exact:

```sh
# per-partition, with OOB so bad-block/ECC metadata is preserved (BG2CDP NAND needs this):
for i in 0 1 2 3 4 5 6 7 8 9 10 11 12; do
  nanddump -f /tmp/nand/mtd${i}.bin /dev/mtd${i}            # data
  nanddump --oob -f /tmp/nand/mtd${i}.oob.bin /dev/mtd${i} # OOB/ECC (separate file)
done
# whole-device aggregate as a cross-check:
nanddump -f /tmp/nand/mv_nand.full.bin /dev/mtd13   # mv_nand aggregate index per /proc/mtd
cat /proc/mtd > /tmp/nand/proc_mtd.txt              # record the exact map at dump time
md5sum /tmp/nand/*.bin > /tmp/nand/MD5SUMS          # on-device integrity
```
Then pull `/tmp/nand/` to the host over the USB-net / ADB channel (NOT NAND — RAM/tmpfs only) and
verify the md5sums match on the host. If `nanddump`/`nandwrite` aren't in the minimal RAM rootfs,
stage the BusyBox `mtd-utils` applets into the initramfs (same mechanism `build_no_nand_initramfs.py`
already uses to stage modules), per the current-state.md note that helpers must call `/bin/busybox`.

**OOB matters here:** the OpenWrt forum case shows that restoring NAND *without* matching ECC/OOB
handling throws `Out of ecc_threshold` errors and can corrupt — so we capture OOB now to make a future
restore safe, even though we hope never to restore.

### Method B — U-Boot `nand read` → `md.b` serial dump (host-only, no on-device tools needed)
This is the cybergibbons method, adapted from SPI (`sf read`) to NAND (`nand read`). It works even from
a bare U-Boot with no Linux userspace, so it's the fallback dumper if the RAM rootfs is too minimal:

```
# at MV88DE3100|>  — read a partition into RAM, then dump RAM over serial (READ-ONLY, never writes NAND)
nand read 0x08000000 <offset> <size>     # offset/size from the mtdparts table in §1
md.b 0x08000000 <size>                   # streams hex over the USB-tunneled console
```
Capture the console log on the host (the project's serial helper logs every transcript under
`~/.local/state/hk-invoke/sessions/`), then reconstruct the binary with **`gmbnomis/uboot-mdb-dump`**:
```
python3 uboot_mdb_to_image.py < mtd7.cap > mtd7.bin   # validated: it checks address+ascii consistency
```
Slow (md.b over 115200 is ~hours for 105 MB) but requires nothing on the device. Use it for the small
critical partitions as an independent check of Method A, and as the *only* dumper if userspace is dead.
For speed, prefer Method A; if a TFTP path is ever wired, `nand read` → `tftpput` is the fast version.

### Where the golden image lives + how it's verified
- Store under `~/.local/state/hk-invoke/recovery-baselines/<ts>/nand-golden/` (the existing baseline
  tree; `preserve_baseline.sh` already SHA-256s everything it captures — extend it to ingest the dump).
- **Verify three ways:** (a) on-device md5 == host md5; (b) Method A whole-aggregate == concatenation of
  per-partition dumps at the right offsets; (c) `binwalk` the `rootfs` dump and confirm it mounts as
  squashfs and matches the known StockRoot 83 contents. Only a triple-verified dump unlocks §4.
- Keep at least the RED-class partitions (`block0`, `prebootloader`, `TZ*`, `postbootloader*`,
  `factory_store`, `bbt`, `fts`, `recovery`) dumped and checksummed **forever** — these are the
  Hatch-6 restore source if the worst happens. `factory_store` is per-unit (MACs/calibration) — its
  dump is irreplaceable, treat it like a private key.

## 4. The safe persistence design (what we actually write, and why it can't brick)

With Step Zero done, durability is achieved by writing the **smallest possible** NAND footprint, as far
from the bright line as possible, with rollback proven first.

### Strategy: overlay-on-cache, rootfs frozen (lowest risk) — the recommended default
- Keep the **squashfs `rootfs` exactly as StockRoot shipped it** (frozen, read-only — it already is).
- Mount a **read-write overlayfs upperdir on the `cache` partition** (GREEN class — it's scratch space
  by design). All durable config/state (WiFi creds, the open-source-speaker daemons' config) lives in
  the overlay. squashfs lower + cache upper = a writable system that survives reboot, **without ever
  rewriting rootfs or any RED partition.**
- A bad overlay can never brick: if the overlay is garbage, you wipe `cache` (or RAM-boot, Hatch 2) and
  you're back to pristine frozen rootfs. `cache` is not in the boot chain.
- This is the standard embedded pattern (squashfs+overlay) and it sidesteps the OpenWrt
  `Out of ecc_threshold` restore hazard entirely, because we're not doing block-exact rootfs rewrites.

### If a real rootfs change is ever needed (higher risk — gated, A/B-style discipline)
- Only via `hk_usb_boot`/U-Boot `nand write` to **`rootfs` only**, never via raw `l2nand` (which can
  overrun partition boundaries — that's how 99_IMAGE killed the bootloaders).
- Pre-flight: confirm the new rootfs image size ≤ 105 M; CRC it; confirm the write command's
  offset+length stays inside the rootfs partition (md it back and diff).
- **Rollback-first rule:** before the write, prove you can restore the *current* rootfs from the golden
  dump via the same `nand write rootfs` path on a throwaway test. Never make a write you haven't proven
  you can reverse.
- Even here, the bootloaders/TZ/block0/recovery stay frozen, so Hatch 1+2 remain intact — a bad rootfs
  write is recovered by RAM-boot then re-writing rootfs from golden.

### Boot path for persistence
- The frozen `kernel` partition + frozen U-Boot already boot the squashfs rootfs from NAND on a normal
  power-on (that's how the device booted pre-RAM-experiments). We are **not** modifying the boot chain;
  we're letting the existing, untouched chain boot the existing, untouched rootfs, and layering our
  durability in `cache` overlay. Minimal change = minimal brick surface.

## 5. Restore procedures (every write has a documented undo)

| If this breaks…                  | Recover with…                                                        | Hatch |
|----------------------------------|----------------------------------------------------------------------|-------|
| Bad overlay config in `cache`    | wipe `cache` (or just RAM-boot golden), reboot                       | 2 / 3 |
| Bad `rootfs` write               | RAM-boot (Hatch 2) → U-Boot `nand write rootfs` from golden dump     | 2→3   |
| `kernel` somehow corrupted       | RAM-boot (Hatch 2) → `nand write kernel` from golden dump            | 2→3   |
| U-Boot/`postbootloader` corrupt  | iROM-USB still works → RAM-boot → restore postbootloader from golden | 1→3   |
| `block0`/`prebootloader` corrupt | iROM may be dead → JTAG/external NAND programmer + golden dump       | 6     |

The first four rows are software-only and fully covered by the golden dump + RAM-boot. The last row is
the one we engineer to never reach (never write RED). Restore command shape mirrors the dump:
`nand erase <part>` then `nandwrite`/U-Boot `nand write` the golden `.bin` **with matching OOB/ECC**
(per the OpenWrt lesson) — for UBI-formatted partitions use `ubiformat`, but the Invoke's rootfs is
squashfs (not UBI), so straight `nand write` of the squashfs image is correct.

## 6. What to build next (concrete, source-owned, all read-only first)

1. **`nand_golden_dump.py`** (new) — drives Method A from the RAM root shell: stages `mtd-utils`
   busybox applets, dumps all partitions + OOB + aggregate, pulls to host over the existing non-NAND
   channel, triple-verifies, writes into `recovery-baselines/<ts>/nand-golden/`. Default = read-only;
   no write commands exist in it. This is the gate for everything else.
2. **`nand_uboot_dump.sh`** (new) — Method B fallback: emits the `nand read`+`md.b` command list for a
   given partition, captures via the existing serial helper, runs `uboot_mdb_to_image.py`. Vendored
   `uboot-mdb-dump` (it's a single MIT-ish Python file) under `scripts/hk-invoke/vendor/`.
3. **Extend `preserve_baseline.sh`** to ingest `/tmp/nand/` dumps and checksum them alongside the
   host-side image baselines it already captures.
4. **`overlay_on_cache.sh`** (new, GATED) — sets up the squashfs+overlay-on-cache persistence; inert
   until `HK_INVOKE_PERSIST_APPROVED=yes` + a verified golden dump is present; touches `cache` only.
5. **Keep the FORBIDDEN guards** in `build_no_nand_initramfs.py` and `ram_boot_console.sh` exactly as
   they are; the persistence scripts must go through their own explicit, separately-approved path and
   must hard-refuse any offset/length that lands outside `rootfs`/`cache`.

## 7. Honest open gaps / risks (eyes open)

- **`recovery` partition contents unknown** — must be dumped+inspected before it can be promoted from
  "frozen reserve" to a real hatch. Until then it is NEVER-WRITE, value = brick-surface reduction only.
- **OOB/ECC layout of this specific NAND not yet captured** — Method A `--oob` will reveal it; a restore
  is only proven safe once a round-trip (dump → erase a *non-critical* test region → restore → verify)
  has been done. Do that round-trip on `cache`, never on a RED partition.
- **`block0` semantics not fully RE'd** — we treat it as "the thing the BootROM needs, therefore sacred."
  That conservative stance is what keeps Hatch 1 alive; do not relax it without hardware-recovery
  capability in hand.
- **JTAG unconfirmed** — Hatch 6 may in practice require NAND desolder + external programmer. This makes
  the golden dump (esp. RED partitions + per-unit `factory_store`) the difference between "recoverable
  with effort" and "dead" in the worst case. Dump early, dump verified, keep forever.
- **md.b dump speed** — Method B is hours for the big partitions; rely on Method A for `rootfs`, use B
  only for the small RED partitions and as an independent check.

## 8. Cross-references
- Boot chain / iROM proof: `scripts/hk-invoke/hk_usb_boot.c` (`send_stage1` :603, iROM subclass `ff`).
- Partition map source: `scripts/hk-invoke/build_no_nand_initramfs.py:992` (read-only `mtdparts`),
  `docs/current-state.md` (`/proc/mtd`).
- Write-guards already in place: `build_no_nand_initramfs.py:51` (FORBIDDEN_79_TOKENS),
  `ram_boot_console.sh:47` (forbidden regex).
- Existing host-side backup: `scripts/hk-invoke/preserve_baseline.sh`.
- Device facts + image set: `docs/firmware-bringup-research-2026-06-22.md`.
- External: gmbnomis/uboot-mdb-dump (md.b → binary); cybergibbons "Recovering Firmware Through U-boot"
  (nand read → md.b); OpenWrt nanddump/nandwrite + ECC-threshold caveat; HKHacking Discussion #3
  (MTD layout, hk_usb_boot = BootROM USB mode, 99_IMAGE brick took out USB-loader too); courk.cc
  Google Home Mini (same SoC secure-boot reference — Invoke iROM-USB is NOT signature-locked).
