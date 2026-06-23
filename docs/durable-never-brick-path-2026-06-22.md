---
last_verified: 2026-06-22
category: architecture
---

# HK Invoke — Durable, Never-Brick Persistence Path (RECOMMENDATION)

> GOAL upgrade: from "defer persistence because brick-risk" to "de-risk persistence with fallbacks,
> then do it." Make the speaker run LIVE across power-cycles with rich fallbacks and a host-independent
> recovery escape hatch so it can NEVER brick. Grounded in: device's own evidence
> ([[current-state]]), firmware research ([[firmware-bringup-research-2026-06-22]]),
> coggy9/HKHacking, and the identical-SoC Google Home Mini reverse-engineering (courk.cc).

## 0. The decisive context you already earned

Persistence is now the ONLY gap. The live-speaker question is already answered in RAM:
on 2026-06-23 session IDs the device, RAM-booted via `hk_usb_boot`, loaded `88mlan.ko`+`sd8801.ko`,
firmware went `WLAN FW is active`, `mlan0` **associated to `YOURSSID`**, took DHCP `192.168.1.50`,
and **reached the internet via ICMP to 1.1.1.1 / example.com** (`~/.local/state/hk-invoke/sessions/
20260623T012011Z-wifi-association-yourssid/` + `…T012151Z-wifi-connectivity-probe/`). It "runs live"
today — it just forgets everything on unplug because every writable mount is RAM/tmpfs and NAND is
never written.

## 1. The never-brick proof (why NAND persistence is safe here)

There are exactly two brick-lines on this SoC, and the durable path crosses **neither**.

### 1a. The hardware recovery anchor is immutable mask ROM — and you have already used it
The Marvell Berlin BG2CDP boots from an on-die **BootROM in mask ROM** (physically unwritable). The
flashing-mode sequence (hold reset + power + 4× mic-mute → yellow ring) drops into the BootROM's
**device-mode USB download path**, which is what `hk_usb_boot` (and the stock `run.sh`/`l2nand`) drives.
This path exists *before* and *independent of* NAND contents:

- **Operator-proven, twice:** the U-Boot service-loader (`1286:8174`, banner
  `Marvell U-boot Version 0.1 for MV88DE3006`, S/N `BG2CD S/N:12345678A`) comes up on a
  reset-held power cycle even from a custom RAM-boot state, and the operator successfully ran
  `l2nand 83` → `Congratulations! u2nand succeed!` and recovered to OTA2 (`current-state.md` §Recovery facts).
- **Sibling-SoC confirmed:** Google Home Mini (same ARMADA 1500 Mini Plus / Berlin) has a documented
  USB-boot button that forces the bootloader to boot from USB at power-on (courk.cc Part 1). The
  community `juniperchow/unbrick-google-home-mini` project exists *because the device is recoverable* via
  this path.

**Conclusion:** as long as `block0`, `pre-bootloader`, and the bootloaders are intact, the device can be
re-imaged over USB from a golden dump with zero hardware tools. The BootROM USB path is the escape hatch
that makes every reversible NAND write recoverable.

### 1b. The two true brick-lines (the rails already forbid both)
1. **Bootloader chain** — `block0` (BootROM's DDR + NAND-ECC config), `pre-bootloader`,
   `post-bootloader`/`-B`, `TZ`/`TZ-B`. Corrupting `block0` is the *only* path the community treats as
   "needs hardware tools" (the confirmed 99_IMAGE brick was a stuck loop "with no U-Boot output").
   **The durable path never writes any of these.**
2. **Secure-boot mismatch** — `post-bootloader` cryptographically verifies the kernel; rootfs has
   dm-verity-class protection on the Mini. **We never re-sign or replace bootloader/kernel.** We persist
   only at the **rootfs/data layer**, which coggy9 proved has **no signature check** (he rebuilt and
   reflashed the squashfs and it booted).

**Never-brick invariant:** *every durable write targets a data/rootfs-layer partition only; the
bootloader chain stays byte-identical to the golden dump; and the BootROM USB-download recovery survives
any rootfs/data corruption.* Cross either 1a's intactness or 1b's boundary and the guarantee is void —
so the GO/NO-GO gates (§4) enforce exactly those two.

## 2. MTD partition map (the durability target surface)

Live device `/proc/mtd` (current-state.md, the authoritative map for THIS unit):
`block0, prebootloader, TZ, TZ-B, postbootloader, postbootloader-B, kernel, rootfs, cache, recovery,
fts, factory_store, bbt, mv_nand`. (HKHacking Disc.#3 shows an older labelling — `app`/`localstorage`/
`BDlocalstorage` were the empty data partitions; this unit reports `cache`/`fts`/`factory_store`. INV-011
must reconcile names against the on-device dump before any write.) 128KB erase blocks; bad blocks seen on
`mtdblock11`(rootfs) and `mtdblock16`(mv_nand) — UBI/UBIFS handles these, raw mtd writes must not.

| Partition | Class | Durable-write policy |
|---|---|---|
| `block0`, `prebootloader`, `post*`, `TZ*` | **BOOTLOADER (brick-line)** | **NEVER WRITE.** Golden-dump verified, byte-frozen. |
| `kernel` | secure-boot-verified | Never write (no re-sign). |
| `rootfs` (mtd11) | data-layer, no sig check | Read-only base; overlay lower. Modify only via full golden-restore. |
| `cache` / `fts` / `factory_store` | **data-layer, expendable** | **The persistence target** — host the writable overlay/UBI volume here. |
| `recovery` | recovery slot | Leave stock as a second escape hatch; do not repurpose initially. |
| `bbt` / `mv_nand` | bad-block table / whole-NAND alias | Never write directly. |

## 3. The decision — recommended path

**ADOPT: Path B — reversible NAND data-overlay (overlayfs upper on a dedicated data partition), gated
behind a verified golden NAND dump and a host-independent RAM-recovery escape hatch. Keep Path A
(tethered USB auto-boot) as the always-available fallback, NOT the primary.**

### Why B over A
- **A — tethered USB auto-boot (Pi Zero 2 W as always-on `hk_usb_boot` feeder):** strictly zero NAND
  write → zero brick risk by construction, and it reuses the *already-proven* RAM-boot+WiFi flow verbatim.
  BUT it makes durability depend on a second always-on host glued to the speaker (power, SD-card rot,
  another thing to maintain), and it never actually makes the *Invoke* durable — it makes the *pair*
  durable. Good as a fallback / bring-up rig; weak as "the speaker is durable." Operator's stated want is
  "runs live, better" as a speaker — A doesn't deliver that standalone.
- **B — NAND data-overlay:** the speaker becomes genuinely self-contained and survives a bare
  power-cycle with no host. Because the write is confined to an expendable data partition with the
  bootloader frozen and a golden dump on file, it is **fully reversible** and inherits the §1 never-brick
  proof. This is the path that satisfies "durable NOW + never-brick."

### Shape of B (rootfs stays read-only; only the overlay persists)
1. Keep the **stock squashfs rootfs read-only** (its dm-verity-class integrity stays intact → still
   bootloader-verifiable, still golden-restorable).
2. Create a **UBI volume on the `cache`/`fts`/`factory_store` data partition** (UBI for NAND wear-level
   + bad-block management — required given the observed bad blocks), formatted UBIFS.
3. Mount **overlayfs** with the read-only rootfs as `lower` and the UBIFS volume as `upper`+`work`. All
   the open-source stack (librespot/shairport-sync/bluez-alsa/squeezelite/`hk-mcud`, the
   `sd8887_uapsta.bin` firmware alias, wpa_supplicant conf, the module-load init that replaces the
   skipped `rc.sysinit`) lands in the upper layer and **persists across reboots**.
4. Persist the **boot selection** by writing a *minimal* U-Boot env (`bootcmd`/`bootargs`) — **only if**
   the env partition is separable from the bootloader image and a golden env is dumped first; otherwise
   keep boot RAM-fed (Path A) and persist only userspace. (Env-write is its own gated step, §4 gate G5.)

### Rich fallback ladder (graceful degradation, never a dead speaker)
1. **L0 Durable (target):** NAND overlay boots standalone, WiFi/audio auto-start. No host.
2. **L1 Recovery boot:** overlay corrupt/incompatible → boot ignores overlay (`overlayfs` fails safe to
   read-only lower; add a U-Boot/initramfs flag `noverlay` to force-skip). Speaker still boots stock-clean.
3. **L2 RAM escape hatch (host-independent of NAND):** the proven `hk_usb_boot` → `ram_boot_console.sh`
   flow re-images RAM from the golden artifact with **zero NAND dependency**. This is the
   "can-never-brick-itself" floor — it works even if every writable partition is wiped.
4. **L3 Golden restore:** re-write the data/rootfs layer over USB from the verified golden dump
   (bootloader untouched) → back to known-good.
5. **L4 Host fallback (already built):** `make host-audio-fallback` plays through the Mac/BT baseline so
   "a speaker" exists even with the unit fully down.

## 4. GO/NO-GO gates — ALL must pass before ANY NAND write

These encode the §1 never-brick invariant as testable predicates. A write is permitted **iff every gate
is GREEN** and the operator gives the per-step approval named in §5.

- **G1 — Golden NAND dump captured + verified.** A full read-only dump of every MTD partition (esp.
  `block0` + all bootloader/TZ partitions) exists, hashed, stored under
  `~/.local/state/hk-invoke/recovery-baselines/<ts>/golden-nand/`, and a second independent dump matches
  the first hash. *Predicate: `sha256(dump_A) == sha256(dump_B)` per partition.* (Read-only `nanddump`/
  mtd read over the proven RAM shell; no write.)
- **G2 — RAM-recovery escape hatch re-confirmed working.** A throwaway `hk_usb_boot` RAM boot reaches
  the root shell + WiFi from scratch on THIS unit *today* (re-run the proven 2026-06-23 flow). *Predicate:
  `wpa_state=COMPLETED` + ICMP success from a fresh RAM boot.* This proves L2 is live before risking NAND.
- **G3 — Bootloader untouched plan.** The exact write command list is reviewed and contains **zero**
  writes to `block0/prebootloader/post*/TZ*/kernel/bbt/mv_nand`; targets only the data partition. *Predicate:
  static scan of the command file passes the existing NAND-write refusal filter (`ram_boot_console.sh`
  already blocks `saveenv/l2nand/nanderase/nand write/nand erase`; extend to allow ONLY the named data-vol
  write).*
- **G4 — Golden restore rehearsed (dry).** The restore path (write golden dump back over USB) is
  scripted and dry-run validated against the dump, so L3 is known-good *before* it's needed.
- **G5 (only if persisting boot env) — env partition separability proven + golden env dumped.** If `env`
  is not cleanly separable from the bootloader image, **G5 fails → keep boot RAM-fed, persist userspace
  only.** Default to NO env write.

If any gate is RED: **do not write NAND.** Fall back to Path A (tethered USB auto-boot) for durability
until the failed gate is satisfied.

## 5. Operator-present approval per NAND-write step

Persistent NAND/eMMC/SPI/env writes remain out of scope without explicit per-operation approval
(current-state.md "Forbidden without explicit approval"). The durable path adds these *new* gated steps,
each needing its own operator-present "go" line on the exact command:

1. **Read-only golden dump** (G1) — *no approval beyond normal RAM-shell session* (read-only).
2. **Create UBI/UBIFS volume on the data partition** — **operator-present approval** (first NAND write;
   data-layer only). Reversible via G4 restore.
3. **Write the overlay upper contents** — **operator-present approval** (data-layer write).
4. **Persist boot selection / U-Boot env** (G5) — **separate, explicit, operator-present approval**; this
   is the closest-to-bootloader step and stays last and optional.

`l2nand`, `nanderase`, `nand write/erase`, `saveenv`, `tftp2nand`, `run upgrade` against any bootloader/
kernel partition stay **permanently forbidden** regardless of approval — they have no role in the data-
overlay path.

## 6. Build order (turns the live-RAM proof into durable)

1. **INV-011 (now):** dump `/proc/mtd` + a read-only golden NAND dump on-device; reconcile partition
   names; confirm which data partition (`cache`/`fts`/`factory_store`) is free + its size. → satisfies G1.
2. **Re-run the proven WiFi RAM boot** to confirm L2/G2 on the current physical unit.
3. **Harvest the persistence payload** (P-1/P0 from architecture.md): sound `.ko` + ALSA userspace +
   `sd8887_uapsta.bin` alias + module-load init that replaces skipped `rc.sysinit` + wpa conf.
4. **Stage the overlay image on the HOST**, dry-validate (G3/G4), then — gated — create the UBI volume
   and write the overlay (steps 5.2/5.3).
5. **Add `noverlay` fail-safe** to the boot path (L1) and wire auto-start of the audio/WiFi stack.
6. **Soak:** 10 power-cycles; assert WiFi+audio auto-recover each time and the bootloader dump still
   hashes identical. Then INV-040 "persistence plan" closes.

## 7. One-line decision

**Persist at the data/rootfs layer via a UBI/UBIFS overlay on an expendable data partition, with the
bootloader chain frozen against a verified golden dump and the `hk_usb_boot` RAM boot as a
host-independent recovery floor — gated behind G1–G5 and per-write operator approval. This delivers a
self-contained durable speaker that can be restored to known-good over USB with zero hardware tools, so
it can never brick.** Keep the Pi-Zero tethered USB auto-boot as the fallback rig, not the product.

## Sources
- Operator device evidence: `docs/current-state.md` (RAM WiFi+internet proof, `/proc/mtd`, recovery facts).
- `docs/firmware-bringup-research-2026-06-22.md`, `docs/opensource-speaker-architecture.md` (this repo).
- coggy9/HKHacking: Invoke `Readme.md`, Discussion #3 (MTD layout, mload vs l2nand, binwalk full dump),
  Discussion #5 (rc.sysinit vs rcS, bad blocks, no NAND dump taken before mods).
- courk.cc "Running Custom Code on a Google Home Mini" Part 1/2 (same Berlin SoC: chain of trust,
  block0 = BootROM DDR+ECC config, postbootloader verifies kernel, secure boot boundary).
- juniperchow/unbrick-google-home-mini (sibling-SoC recoverability evidence).
