---
last_verified: 2026-06-22
category: architecture
---

# HK Invoke — Durability + "Runs Live, Better" Design (de-risked persistence)

> Posture upgrade: the prior stance ("defer persistence because brick-risk", backlog INV-040)
> is replaced by **"de-risk persistence with fallbacks and a recovery escape-hatch, then do it."**
> This document designs the device running LIVE as a durable speaker across power-cycles, with rich
> fallbacks and a never-brick guarantee, **without crossing any operator hard rail.**
>
> Grounds: [[firmware-bringup-research-2026-06-22]], [[opensource-speaker-architecture]],
> [[architecture]], [[current-state]], [[operator-runbook]], [[wire-a-new-invoke-runbook]];
> Google-Home-Mini secure-boot analysis (Courk, same Marvell Berlin SoC); HKHacking Discussions #3/#5/#7/#11.
>
> **LENS = "runs live" OPS DESIGN.** This is the *operations* companion to two sibling docs that own
> the mechanism and recovery:
> - **[[persistence-and-recovery-design-2026-06-22]]** — canonical authority for the **persistence
>   mechanism + tiers + brick-risk** (grounded in the device's OWN extracted `rcS`: the stock OS
>   already mounts `localstorage`/`app` YAFFS read-write on NAND below the bright line; recommended
>   durable layer = T1a write-to-`localstorage` / T1b overlay-upperdir-on-data).
> - **[[recovery-escape-hatch-stack-2026-06-23]]** — canonical authority for the **recovery ladder,
>   the NAND golden-dump gate, and the partition bright-line.**
>
> This doc does NOT redesign the write path or the recovery ladder — it designs how the device *runs
> live as a durable speaker* (auto-start, supervision, health endpoint, remote management, graceful
> degradation, HA wiring) layered on top of those two, and what "runs live, better" concretely looks
> like at each persistence tier. Where the docs touch, the persistence doc owns mechanism and the
> escape-hatch doc owns recovery; this doc points at them rather than restating.

---

## 0. Why durable persistence is now safe (the recovery anchor — see escape-hatch doc for the proof)

The full proof lives in [[recovery-escape-hatch-stack-2026-06-23]] §0–§2. The load-bearing facts this
ops design depends on:

1. **The unbrickable anchor is the Marvell BootROM (iROM / mask ROM), not anything on NAND** — and it
   is reached by a hardware button sequence (hold reset + power → 4× mic-mute → yellow ring →
   `1286:8174`). The escape-hatch doc proves from `scripts/hk-invoke/hk_usb_boot.c` (`send_stage1` at
   :603) that this iROM-USB path **empirically accepts the unsigned `bcm_erom.bin.usb`** and serves
   U-Boot into RAM — i.e. unlike the *locked* Google Home Mini (same SoC), the Invoke's iROM-USB
   download path is **not signature-enforced.** This is the always-available escape hatch and it reads
   nothing from / writes nothing to NAND.
2. **The anchor is destroyed only by writing the bootloader-class partitions** (`block0`,
   `prebootloader`). That is exactly the 99_IMAGE brick ("stuck in loop, no U-Boot output," and even
   USB-loader mode did not return). A *rootfs/cache* corruption leaves iROM-USB fully working.
3. **The bright line therefore is: every persistence write stays strictly inside the device's own
   already-writable data partitions (`localstorage`/`app` YAFFS, which stock `rcS` mounts RW on every
   boot) or — if a writable-rootfs illusion is needed — an overlay upperdir on those, and NEVER
   touches `block0`/`prebootloader`/`TZ*`/`postbootloader*`/`kernel`/`recovery`/`bbt`/`fts`/
   `factory_store`.** Keep those frozen and the escape hatch is always there. (Exact partition choice
   + tier risk are owned by [[persistence-and-recovery-design-2026-06-22]].)

**Net for this ops design:** because the recovery anchor is below every write we would ever make, the
device can run durably across power-cycles AND be un-brickable at the same time — *provided* the
[[recovery-escape-hatch-stack-2026-06-23]] golden-dump gate is satisfied and the persistence mechanism
is the [[persistence-and-recovery-design-2026-06-22]] T1a/T1b data-partition layer (rootfs frozen).
This ops doc assumes that mechanism and designs the live-running behavior on top of it; it deliberately
does not re-design the write path.

---

## 1. "Runs live, better" defined per persistence tier

The question "what does runs-live-better look like at each tier" has concrete answers per tier. Each
tier is **independently shippable** and **strictly additive** — you can stop at any tier and have a
durable, useful speaker. **Tier vocabulary here follows the canonical
[[persistence-and-recovery-design-2026-06-22]]** (P0 = host auto-boot; T1a/T1b = data-partition
persistence; T2 = custom rootfs; T3/T4 = near/over the forbidden zone). The mechanism column points at
that doc; this doc owns only the "runs live" experience column.

| Tier (persistence doc vocab) | Persistence mechanism (authority: persistence doc) | NAND writes? | "Runs live" experience | Brick risk |
|---|---|---|---|---|
| **P0 — Host-orchestrated auto-boot** | None (RAM, host-served every power-cycle) | **No** | Power-cycle is *transparent*: a Mac/host daemon detects the device in service mode and auto-replays the proven RAM-boot + Wi-Fi + audio bring-up. Speaker is back in ~60–90 s, unattended. | **Zero** |
| **P0+ — Host auto-boot + on-device watchdog/health** | None (RAM) | **No** | Same as P0, plus on-device service supervision (auto-restart crashed audio daemons), a health endpoint, graceful Wi-Fi-drop recovery. Survives software crashes; still needs host for power-cycle. | **Zero** |
| **T1a/T1b — Standalone: frozen rootfs + data-partition persistence** | Write daemons/config to the device's OWN already-RW `localstorage`/`app` YAFFS (T1a), or overlay-upperdir on it over the squashfs rootfs (T1b) — persistence doc default | **Yes — device's own writable data partitions only** (the same ones stock `rcS` writes; below the bright line) | Device self-boots the stock NAND rootfs (untouched boot chain), the stock init seam auto-starts the open-source stack, durable config (Wi-Fi creds, daemon config) lives in the data partition so it survives reboot. **No host, no tether.** | **Very low** (bad data = restore the data dump or RAM-boot; rootfs/boot untouched) |
| **T2 — Custom rootfs (optional, gated)** | `nand write` to **`rootfs`/mtd7 only**, bootloader+recovery intact, rollback-proven first (persistence doc T2) | **Yes — `rootfs` partition only** | Fully baked appliance: the open-source stack lives in rootfs itself (no init-seam shim), boots straight into the speaker. Marginal polish over T1. | **Low, bounded** (rootfs-only; golden dump + RAM-boot escape-hatch make every write reversible) |

**Recommendation: ship P0 → P0+ → T1 in order, then STOP at T1 unless there's a concrete reason for
T2.** P0/P0+ are NAND-write-free. **T1 (frozen rootfs + the device's own writable data partition)
delivers the full standalone appliance — "boots straight into the speaker on every power-up, no host" —
at very low risk, because the boot chain + rootfs stay byte-identical to stock and durable state lives
only where the stock OS itself writes.** T2 (rewriting rootfs) is rarely worth its incremental risk
over T1; treat it as optional polish, gated behind the golden-dump + rollback proof.

---

## 2. Tiers P0 & P0+ — "runs live" with ZERO NAND writes (do these first)

These make the *current* RAM-only device feel like a durable appliance, today, with no new risk.

### 2.1 P0 — Tethered auto-boot (power-cycle becomes transparent)

The brick-safety of RAM boot is its whole point; P0 removes its only UX cost (manual re-bring-up after
power loss) by automating the host side. New host daemon: **`hk-invoke-autopilotd`** (Mac/host, runs
under launchd; no device writes).

State machine (all states map to existing `make` gates so nothing new is trusted):

```
        ┌──────────────────────────────────────────────────────────┐
        │  hk-invoke-autopilotd  (host, launchd KeepAlive)          │
        └──────────────────────────────────────────────────────────┘
 poll every 5s:  no-nand-readiness  →  classify surface
   │
   ├─ normal_baseline_visible   → IDLE (stock BT baseline up; do nothing)
   │
   ├─ hold_no_custom_boot       → ALERT operator (no surface; needs physical service-mode), back off
   │
   └─ arm_ram_listener_allowed  → SERVICE MODE DETECTED → run bring-up sequence:
            1. artifact=$(make no-nand-initramfs-ota83 | tail -1)        # fresh, verified
            2. ram_boot_console.sh --check-only "$artifact/image-dir"    # MUST pass (NAND-cmd scan)
            3. ram_boot_console.sh feeds uboot-ram-commands.txt linewise # RAM only
            4. wait for /dev/cu.usbmodem*nand_probe* ACM shell
            5. over ACM: stage sd8887_uapsta.bin alias → insmod 88mlan+sd8801 → mlan0 up
            6. wpa_supplicant (PSK from transient 600 env) → udhcpc → verify DHCP+ping
            7. (P0 audio) insmod snd order → speaker-test proof → start audio daemons
            8. mark LIVE; publish health; stop re-arming
```

What makes the power-cycle "transparent": the operator just plugs power back in and (if the device is
in service mode) the host replays the entire proven path unattended in ~60–90 s. The operator's only
manual act is the physical yellow/service-mode sequence *if* the device cold-boots to stock instead of
service mode — which the standalone T1 tier later removes entirely.

**Hard-rail compliance:** every step is an existing, reviewed, RAM-only script; `--check-only` rejects
any NAND/eMMC/SPI/env write before USB OUT; PSK stays in a transient mode-600 env (never logged). The
daemon is a host orchestrator of *already-approved* primitives — it does not invent a new device write.
The *one* policy change required: pre-authorize the daemon to run the **already-individually-approved**
RAM-only module-load + Wi-Fi association steps unattended (a standing approval for the proven sequence,
scoped to RAM-only, revocable by stopping the launchd job). This is the only "autonomy" delta and it
crosses no rail.

### 2.2 P0+ — On-device supervision, health endpoint, graceful degradation (still RAM-only)

Added to the RAM rootfs initramfs (no NAND): a tiny init that supervises the audio/network stack so the
device self-heals software crashes between power-cycles.

- **Service supervisor (`hk-superviserd`, ~static busybox/musl or a 30-line `sh` respawn loop):**
  respawns `shairport-sync`, `librespot`, `bluealsa`/`bluealsa-aplay`, `squeezelite`, and `hk-mcud` if
  any exits. Backoff to avoid crash-loops; cap restarts/min; emit each restart to the health log.
  *Why not systemd:* Linux 3.8.13 / this rootfs is SysV-init; a respawn loop or busybox `init`
  `respawn` lines in inittab is the right-sized primitive (no systemd dependency graph).
- **Hardware watchdog (auto-recovery from total hang):** the Berlin family exposes a watchdog; if
  `/dev/watchdog` (char 10:130) is present, a userspace feeder writes it every <60 s. If the system
  fully hangs, the SoC resets — and (in P0/P0+) the host autopilot re-boots it. **Caveat to verify:**
  the watchdog driver must be in the kernel/DTB; if absent, fall back to a host-side liveness probe
  that power-cycles via a smart plug (optional hardware). The watchdog timeout must be > the longest
  legitimate audio stall, or it self-reboots during normal use — set conservatively (e.g. 60 s) and
  only after confirming the audio daemons feed it or a dedicated feeder thread does.
- **Health endpoint (`hk-healthd`, ~50-line HTTP on `:8901`, LAN-only, bound to `mlan0`):**
  `GET /healthz` → JSON `{wifi, ip, alsa_cards, daemons:{shairport,librespot,bluealsa,squeezelite,mcud},
  uptime, restarts, watchdog}`. This is the single source of truth for "is the speaker live" — both the
  host autopilot and Home Assistant poll it. No secrets; read-only; LAN-only.
- **Graceful Wi-Fi-drop recovery:** `wpa_supplicant` reconnect is automatic, but add a watchdog on
  `mlan0`: if no DHCP lease / no default route for N seconds, re-run `udhcpc`; if association is lost,
  `wpa_cli reconnect`; if the SDIO driver wedges (rare), `rmmod`/`insmod sd8801` + re-associate. Each
  recovery step is RAM-only and idempotent. Audio daemons that died because the network vanished are
  respawned by `hk-superviserd` once `mlan0` is back.
- **Remote management over Wi-Fi (primary) + serial (backup):** bring up **dropbear** (already present
  in StockRoot per Discussion #7) on `mlan0` for `ssh` management; if Wi-Fi is fully down, the USB ACM
  serial root shell (`/dev/cu.usbmodem*`) remains the out-of-band console. Two independent management
  planes = you can always reach the device to fix it.

**Degradation ladder (graceful, never-dead):**
`full stack (Wi-Fi + BT + audio)` → `Wi-Fi lost → BT-A2DP-only (SDIO still up) + keep retrying Wi-Fi`
→ `network fully lost → local audio only + serial console for repair` → `audio driver wedged →
supervisor reloads snd modules` → `total hang → watchdog reset → host autopilot re-boots (P0)`.
At no rung does the device become unrecoverable.

---

## 3. Tier T1 — standalone "runs live" (frozen rootfs + data-partition persistence)

> **Persistence mechanism is owned by [[persistence-and-recovery-design-2026-06-22]]** (T1a: write
> daemons/config to the device's OWN already-RW `localstorage`/`app` YAFFS partitions — the ones stock
> `rcS` mounts read-write on every boot, below the bright line; T1b: overlayfs upperdir on that data
> partition over the squashfs rootfs for a writable-rootfs illusion. squashfs rootfs stays frozen;
> a bad data layer is recovered by restoring the data dump or RAM-booting). This section designs only
> the *runs-live ops behavior* layered on that mechanism.

T1 boots the **stock NAND rootfs normally** (untouched boot chain → squashfs rootfs → root + dropbear
per Discussion #7), then auto-starts the open-source speaker stack from the durable data partition:

- **Durable payload + config live in the writable data partition** (not rootfs): the open-source
  binaries (`shairport-sync`, `librespot`, `bluealsa`, `squeezelite`, `hk-mcud`, `hk-superviserd`,
  `hk-healthd`), their config, the `sd8887_uapsta.bin` firmware alias, the Wi-Fi creds (mode-600, on
  the data partition never in rootfs), and a `runs-live.sh` orchestrator. Because they live on
  `localstorage`/`app` YAFFS, all of this survives power-cycle.
- **Auto-start init seam (smallest blast radius):** the stock OS already runs `rcS`/`rc.sysinit` and
  already mounts the data partition — so the smallest hook is to have the stock init *source/exec* a
  `runs-live.sh` from the data partition (the stock firmware uses `localstorage` autostart hooks of
  this shape; confirm the exact seam by reading the device's own `rcS`/`rc.sysinit`). T1b's overlay
  lets the entry be a single overlay file (e.g. `S99-runs-live`) over the merged view **without
  rewriting the read-only squashfs lower layer.** Either way the rootfs stays byte-identical to stock.
- **`runs-live.sh`** = the on-device, self-triggered version of P0's bring-up sequence 5–8: load the
  kernel-matching SD8801 modules + firmware alias, bring up `mlan0`, associate Wi-Fi, then start the
  supervised audio stack (§2.2) + the health endpoint.

**Result:** power-cycle → device cold-boots stock rootfs → mounts the data partition → init seam execs
`runs-live.sh` → live open-source speaker, **no host, no tether, no rootfs rewrite.** This is the
durability target: it removes the tether entirely while keeping rootfs + boot chain byte-identical to
stock (so the escape hatch and the simplest recovery — restore the data dump / RAM-boot — are always
available), and it writes only where the stock OS itself writes.

### 3.1 Optional T2 — bake the stack into rootfs (gated, rarely worth it)

If you ever want to remove even the init-seam shim and bake the stack directly into rootfs: that is a
`nand write` to the **`rootfs`/mtd7 partition only**, via the gated T2 path in
[[persistence-and-recovery-design-2026-06-22]] (golden dump + rollback-proven-first; never `l2nand`'s
boundary-overrunning whole-image write; bootloader/TZ/kernel/recovery partitions never touched). The
marginal benefit over T1 is small — **default to stopping at T1.**

---

## 4. The never-brick escape-hatch — see the canonical doc

The recovery ladder, the **mandatory golden-NAND-dump gate** (the precondition for *any* write,
including T1's data-partition writes), the partition bright-line, the restore procedures, and the
recovery rehearsal are fully designed in **[[recovery-escape-hatch-stack-2026-06-23]]** (the canonical
recovery authority — do not restate it here). The one-line summary this ops doc relies on:

> **Escape hatch:** iROM-USB (silicon, button sequence → `1286:8174`) is below every write we make and
> accepts the unsigned RAM-boot payload, so a corrupted data partition / rootfs is always recoverable
> by RAM-booting the golden image and (if needed) re-writing from the verified golden dump. The whole
> design stays unbrickable **iff** the bootloader-class partitions are never written.

**Gate for this ops design:** the standalone T1 tier must not begin until the escape-hatch doc's golden
dump is captured + triple-verified and its recovery rehearsal has passed. Tiers P0/P0+ need none of
that (zero NAND writes).

---

## 5. Home Assistant wiring hook (the live-ops integration)

The HA integration is the same at every tier — it targets the **health endpoint + the audio stack**,
not the persistence mechanism, so it works whether the device is tethered (P0/P0+) or standalone (T1/T2):

- **Media player:** `squeezelite -o music -s <Music-Assistant-host>:3483` runs in the supervised stack;
  Music Assistant + HA `slimproto` surface it as `media_player.hk_invoke` (per the architecture doc —
  best fit, pure C, no LMS VM).
- **Liveness/diagnostics:** HA polls `http://<invoke-ip>:8901/healthz` (a RESTful sensor or command-line
  sensor) → `binary_sensor.hk_invoke_online` + attributes (Wi-Fi RSSI, daemon states, restarts,
  watchdog). This gives HA automations a real "is the speaker up" signal and powers the host
  autopilot's re-arm decision.
- **Controls bridge:** `hk-mcud` (single-writer volume/LED daemon) publishes encoder/button events and
  subscribes LED-ring state over **MQTT**, so HA sees inputs and can drive the ring — the device becomes
  a first-class HA entity, not just an audio sink.
- **Wake/Assist (later):** Wyoming/Assist stays off-device (host/server) per the security model; the
  Invoke is a thin mic/speaker endpoint via the PCM bridge. No change to persistence design.

---

## 6. Build order (each step independently valuable; risk strictly non-decreasing)

| Step | Tier | NAND write | Gate to start | Deliverable |
|---|---|---|---|---|
| 1 | P0 | none | proven RAM bring-up (done) | `hk-invoke-autopilotd` host daemon; power-cycle transparent |
| 2 | P0+ | none | P0 live | `hk-superviserd` + `hk-healthd` + Wi-Fi-drop recovery + dropbear; self-heals crashes |
| 3 | P0+ | none | P0+ live | watchdog feeder (if `/dev/watchdog` present) + HA `/healthz` sensor + squeezelite/MA |
| 4 | recovery | read-only | P0+ live | golden NAND dump per [[recovery-escape-hatch-stack-2026-06-23]] (mtd-utils in initramfs, `nanddump --oob`, triple-verified) + **recovery rehearsal passes** |
| 5 | T1 | data partition only | step 4 passed + Joe approves exact op | persistence per [[persistence-and-recovery-design-2026-06-22]] T1a/T1b + `runs-live.sh` + single init-seam entry; standalone self-boot, no tether |
| 6 | T2 (optional) | `rootfs` only | step 4 passed + Joe approves exact op | optional: bake stack into rootfs via persistence-doc T2 gated `nand write rootfs` (rollback-proven-first); rarely needed over T1 |

Steps 1–4 cross **zero device storage writes** and already deliver a durable, self-healing,
HA-integrated, remotely-manageable speaker whose power-cycle is transparent. Step 5 removes the tether
entirely by writing only the device's own already-writable data partition (rootfs + boot chain stay
byte-identical to stock). Step 6 is optional polish. Both are gated behind the *proven* golden-dump +
recovery drill and per-operation operator approval — exactly the "de-risk, then do it" posture.

---

## 7. Hard-rail compliance check (the whole point)

| Operator hard rail | How this design honors it |
|---|---|
| Never flash 99_IMAGE | Never a write target anywhere in this design; only the stock NAND rootfs (frozen) and the device's own writable data partition are involved |
| Never base on OTA2/final-OTA | The frozen stock rootfs is StockRoot-lineage (Wi-Fi-working); OTA2 kept only as BT-baseline recovery, never the live image |
| Never block Wi-Fi | The design's entire value depends on Wi-Fi staying up; the frozen rootfs is the Wi-Fi-working build; recovery restores it |
| No unrecoverable NAND/bootloader writes for bring-up | P0/P0+ = zero writes; T1 = device's own already-RW data partition only (not in boot chain); optional T2 = `rootfs` only, gated; bootloader/TZ/kernel/recovery/`block0`/`prebootloader` partitions **never** written (the escape-hatch bright-line) |
| Persistence only after dump/restore proof + exact approval (INV-040) | Step 5/6 gate on the golden dump + recovery rehearsal *before* any write; each requires Joe's approval of the exact operation |
| PSK never committed/logged | P0/P0+ read PSK from a transient mode-600 RAM env; T1/T2 store it mode-600 on the data partition, never in rootfs, never logged |

**Conclusion:** "durable + richer fallbacks + runs live, better" is achievable with **zero new
brick-risk through tier P0+** and **bounded, reversible, recoverable risk for the standalone tiers** —
without crossing a single operator rail. The deferral of persistence is correctly upgraded: the path is
de-risked (this ops doc + the [[persistence-and-recovery-design-2026-06-22]] mechanism +
the [[recovery-escape-hatch-stack-2026-06-23]] golden-dump escape hatch), and the work is sequenced so
value lands early and risk only appears behind a proven recovery drill and explicit approval.
