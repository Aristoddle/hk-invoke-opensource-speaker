---
last_verified: 2026-06-22
category: architecture
---

# HK Invoke → Open-Source Speaker — Architecture & Phased Build Plan

> Goal: rebuild the Invoke as an open-source speaker — WiFi/Spotify + AirPlay, Bluetooth A2DP sink,
> rotary/buttons/LED ring, Home-Assistant-wireable. Source: 7-agent research (workflow wyrgr6gqp),
> grounded in the device's OWN on-disk config. Device facts: [[firmware-bringup-research-2026-06-22]] /
> Current state: on WiFi (RAM-only boot), StockRoot rootfs.

## The two findings that change everything
1. **The shared audio mixer ALREADY EXISTS** (read off the device's own `asound-product.conf`): the
   foundation is *recover, not author*.
2. **The "kernel/glibc too old for modern software" panic was an arithmetic error.** 3.8.13 > Rust's
   3.2 floor and glibc 2.23 > 2.17 — the device CLEARS the bar. Reuse prebuilt `.ko` for the foundation;
   static-musl for Rust (librespot); Yocto-2.1-Krogoth glibc-2.23 SDK for C daemons.

## Audio data-flow (the spine — everything routes here)
```
librespot(Spotify) ─┐   shairport-sync(AirPlay) ─┐   bluealsa-aplay(BT) ─┐   squeezelite(HA) ─┐
                    └──────────► ALSA PCM "music"/"system" ◄──────────────┘                   │
                                        │   (per-stream softvol: system/music/timer/voice/call)│
                                        ▼                                                       │
              *** STOCK SHARED dmix  (volmix_*, ipc_key 1024)  — REUSE, do not author ***  ◄────┘
                                        │
                                        ▼
              pcm.dsp = hw:1  S32_LE / 48000 / 2ch   (Marvell Berlin audio-out DSP — NOT the codec, NOT 16-bit)
                                        │ I2S
                                        ▼
              WM8904 codec ─► 6-driver amp (likely Line-Out/OUT2; wm8904 has no "Speaker" control) ─► speakers
   snd-aloop "Loopback" = card 0  (mic/voice path; needed if anything opens "default")
```
**Players open a high-level PCM ("music"/"system"), never raw `hw` — the stock dmix shares the single codec for free.**

## Control plane (separate from audio — single-writer is mandatory)
`keypad/LED MCU ⇄ 6-byte serial frames ⇄ mcu-interface link`. Inbound `[4 key][which][state]` = rotary
ticks + button taps; outbound `[3 LED][subcmd][args]` = ring animations. A **single-writer `hk-mcud`
daemon owns volume**: encoder tick → one canonical volume var → writes BOTH the softvol ALSA control AND
the LED frame (ring always matches reality). Buttons → player transport (MPD/MPRIS/librespot). **Why
single-writer:** a stock volume-authority loop fights the encoder to max (the `music-test` volume-spam
bug is direct evidence) — you must stop the stock writer first.

## Open-source stack (per pillar, with feasibility)
| Pillar | Component | Feasibility on k3.8.13/glibc2.23/ARMv7 |
|---|---|---|
| **Audio** | Berlin DSP + `snd-soc-wm8904` + `snd-aloop` `.ko` + ALSA userspace + `music-test` | **REUSE prebuilt from StockRoot 83** (vermagic-locked; don't rebuild). |
| **WiFi (easy)** | **shairport-sync** (AirPlay-1 "classic", `--with-tinysvcmdns`) | **EASIEST WIN** — plain C, builds clean on Krogoth. (Do NOT build AirPlay-2 — drags ffmpeg/libsodium.) |
| **WiFi (Spotify)** | **librespot** 0.6.x | Feasible — build from source vs Yocto-2.23 sysroot OR static-musl. NEVER a prebuilt raspotify (needs glibc 2.31 → dies). Risk: dmix hwparams. |
| **Bluetooth** | BlueZ 5.43 (stock) + **bluez-alsa v1.2.0** (A2DP sink, SBC) | Feasible with the OLD stack only. NO hciattach (88W8887 SDIO). `--pcm-buffer-time=170000` cures crackle. |
| **Controls** | custom **`hk-mcud`** (static-musl) | Track A: logcat-tap → amixer (1 hr). Track B: clean daemon. Protocol RE'd from 4 log lines → recover on-device. |
| **Home Assistant** | **squeezelite** + **Music Assistant** + HA `slimproto` | BEST fit — squeezelite is pure C, codecs dlopen'd at runtime. MA emulates the server (no LMS VM). Controls bridge via MQTT. |

## Cross-compile answer (the hard part, resolved)
- **C daemons** (shairport-sync, bluez-alsa, squeezelite): build the **Yocto 2.1 "Krogoth" SDK** yourself
  (`bitbake -c populate_sdk` for `cortexa7hf-neon`; **no prebuilt ARM .sh exists**; needs Ubuntu 14.04). ABI-exact glibc 2.23.
- **librespot (Rust):** `armv7-unknown-linux-musleabihf` + `-C target-feature=+crt-static` → fully static, zero libc dep. (Or the Krogoth gnu sysroot.)
- **BSP sound `.ko`:** do NOT cross-compile — reuse the prebuilt StockRoot ones (vermagic-locked to `3.8.13-mrvl`).
- **Never** use static-musl for the BlueZ/glib/dbus graph (glibc/NSS-coupled) — match the Krogoth sysroot there.

## Phased build plan (from the current state: on WiFi, RAM-only)
- **P-1 (HOST, do NOW — unblocks everything):** `parse_ota83` the **StockRoot** 83 → `unsquashfs` its `06_rootfs.bin` → harvest the sound `.ko` (+ load order), ALSA userspace, `asound-product.conf`, and **`/etc/init.d/startup/S*-audio`** (the authoritative module-load + amixer init — ends all guessing).
- **P0 (audio out):** stage `.ko` + ALSA userspace into the RAM initramfs; `insmod` in stock order (DSP → wm8904 → snd-aloop); `cat /proc/asound/cards` shows card0 Loopback + card1 Berlin. **PROOF:** `speaker-test -D hw:1 -c2 -r48000 -F S32_LE`, then `aplay -D music a.wav & aplay -D system b.wav &` (2 players mixing = shared dmix proven). If silent: unmute the OUT2/Line path.
- **P1 (WiFi streaming — headline):** **shairport-sync first** (AirPlay = fastest external win, phone→speaker, zero Rust risk) → then **librespot** (Spotify Connect; lock the dmix format).
- **P2 (Bluetooth A2DP sink):** confirm `hci0` (no hciattach), bluez-alsa v1.2.0, pair NoInputNoOutput, `bluealsa-aplay --pcm-buffer-time=170000`.
- **P3 (physical controls):** on-device recover the mcu-interface (`/proc/<pid>/fd` + `logcat -s mcu-interface` while turning the knob) → ship Track-A logcat-tap→amixer, then `hk-mcud` single-writer.
- **P4 (Home Assistant):** `squeezelite -o music -s <MA>:3483` + Music Assistant add-on + HA slimproto = `media_player.hk_invoke`; `hk-mcud` publishes inputs / subscribes LED over MQTT.

## Quickest win
**Two concurrent `aplay` through the stock dmix to the speaker (P0)** — zero cross-compile, reachable now from the RAM-only WiFi state. Then **shairport-sync** (AirPlay) for the first phone→speaker streaming demo.

## Hardest risks (eyes open)
Building the Krogoth SDK (no prebuilt; Ubuntu-14.04; newer SDKs *fault at load* — confirmed) · librespot-through-dmix hwparams mismatch (fallback: MPD-as-sole-ALSA-owner) · the mcu-interface protocol is RE'd from 4 log lines (recover on-device) · the stock volume-authority fight (stop it before hk-mcud) · which amp output is live (OUT1 vs OUT2) · WiFi+BT SDIO contention (the "Spotify+BT at once" combo) · everything RAM-only/ephemeral until the gated NAND-persistence phase.
