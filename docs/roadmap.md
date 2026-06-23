# Roadmap

## Roadmap posture

This is a hardware recovery project with product-shaped milestones. Each milestone must leave the device in a safer or better-understood state than before. The roadmap is intentionally gated: a later milestone cannot start until the prior rollback evidence exists.

## Milestones

### M0 — Recovered baseline

**Status:** complete.

**Outcome:** Invoke is de-bricked with official OTA2 and works as a Bluetooth speaker.

**Evidence:**

- Bluetooth device: `HK Invoke_C114D2`.
- Audio output visible in macOS.
- Official OTA2 U-Boot success line: `Congratulations! u2nand succeed!`.
- Recovery artifacts preserved under `~/.local/state/hk-invoke/recovery-baselines/20260619T012008/`.

**Gate:** `make state` confirms the baseline.

### M1 — Normal-boot host observation

**Status:** complete for normal host-observation scope; latest baseline
visibility needs physical re-verification after the RAM-only experiment.

**Outcome:** determine whether normal boot exposes USB, ADB, Ethernet-over-USB, mDNS, ARP, or router-visible IP services without reset/yellow mode.

**Acceptance criteria:**

- `scripts/hk-invoke/normal_boot_probe.sh` output captured.
- Any candidate IP is probed only with TCP connect checks, not exploit attempts.
- Bluetooth baseline still works after probe.

**Evidence:**

- `~/.local/state/hk-invoke/sessions/<session>/`
  showed Bluetooth/audio baseline alive, no Marvell service-loader USB in normal
  boot, no ADB, no Invoke-specific ARP candidate, and no Invoke-specific mDNS
  service.
- `~/.local/state/hk-invoke/sessions/<session>/`
  later saw no Bluetooth/audio match and no Marvell service-loader USB. Until a
  normal baseline or service-loader state is physically restored, custom boot is
  not the next safe action.

**Joe action:** plug micro-USB during normal boot only if asked; do not hold reset.

### M2 — RAM-boot debug shell

**Status:** shell achieved; rollback check pending. Service-mode reset,
U-Boot access, generated no-NAND `81_IMAGE`/`82_IMAGE` transfer, `bootm`, and a
USB ACM root shell are proven. Re-checking the normal Bluetooth baseline after
the latest RAM-only boot remains the open rollback gate.

**Outcome:** boot a temporary debug/root shell from RAM using service mode, with no persistent writes.

**Acceptance criteria:**

- `scripts/hk-invoke/ram_boot_console.sh` safety scan passes before Joe touches the device.
- No served `79_IMAGE` contains NAND-writing commands.
- Native console outgoing safety self-test passes and blocks known
  NAND/eMMC/SPI/env write commands before USB OUT.
- Generated no-NAND artifact is used instead of the stock OTA2 image directory.
- `81_IMAGE` and `82_IMAGE` both transfer successfully, then `bootm` runs.
- Session transcript and before/after state artifacts are written under
  `~/.local/state/hk-invoke/sessions/<timestamp>/`.
- Shell access achieved or failure mode documented.
- Power cycle returns to normal Bluetooth behavior.

**Joe action:** perform reset/Mic-Off sequence only after the listener is armed.

### M3 — Read-only device inventory and rollback design

**Status:** in progress, with the surface-visibility blocker cleared for the
2026-06-21 apartment run. A fresh RAM-only artifact booted to USB ACM root shell
again and the 2026-06-21 read-only serial inventory captured identity, command
line, MTD partitions, mounts, `/proc/net/dev`, device nodes, process state, and
generated audio/network helper output. Current network exposure is still only
`lo`, `sit0`, and `ip6tnl0`; `/dev/snd`, `/sys/class/sound`, and
`/proc/asound/*` are absent; host-visible ADB remains unproven as a control
channel. The immediate M3 gap is no longer physical access; it is source-owned
repeatability, helper/tooling portability, and an approval-gated artifact path
for OTA83 module, USB-network, or Wi-Fi experiments.

**Outcome:** know the real running system: storage, partitions, audio devices, Wi-Fi, processes, services, writable paths, and recovery options.

**Acceptance criteria:** capture these from RAM shell or normal shell:

```sh
uname -a
cat /proc/cmdline
cat /proc/mtd
mount
cat /proc/partitions
ls -la /dev/snd
cat /proc/asound/cards
ip addr || ifconfig -a
ps w
netstat -lntup || ss -lntup
```

**Rollback gate:** before persistence, run the source-owned `make golden-nand-dump-plan` preflight, then at a separate operator-present gate dump MTD partitions with a proven read-only `nanddump --oob` transfer path and verify sizes/hashes against `/proc/mtd`.

### M4 — ALSA audio proof

**Status:** blocked on M3.

**Outcome:** prove the Invoke can play and capture PCM from Linux userspace.

**Acceptance criteria:**

- Speaker playback works through ALSA path.
- 5-second mic capture file exists and has non-zero, inspectable audio.
- Sample rate, channel count, device names, and mixer settings are recorded.
- Bluetooth baseline still recovers after power cycle.

### M5 — Host/server voice prototype

**Status:** available fallback. It can start before M4 using Mac audio and
Bluetooth output, and it does not require any Invoke firmware change or Linux
shell.

**Outcome:** prove the server-side agent loop independently of Invoke firmware.

**Reference direction:** LiveKit Agents supports realtime AI participants and OpenAI realtime speech-to-speech via its OpenAI plugin. OpenAI Realtime supports low-latency audio sessions; browser/mobile clients generally use WebRTC for realtime audio.

**Acceptance criteria:**

- `make host-realtime-dry-run` succeeds without querying the Invoke.
- `make host-audio-fallback` succeeds with Mac audio only when the Invoke is not
  currently visible.
- `make host-audio-devices` remains the strict Bluetooth-output baseline gate
  when proving the assistant response can play through the Invoke.
- Host-side prototype can join a LiveKit room or local equivalent.
- OpenAI and LiveKit credentials are kept off-device and out of git.
- Initial audio path works with Mac mic and Bluetooth output before device bridge work.
- Documentation and logs clearly label this as the host-assistant fallback, not
  proof of native Invoke mic/ALSA access.

### M6 — Invoke PCM bridge

**Status:** blocked on M4 and M5.

**Outcome:** a minimal device-side process streams mic PCM to a host and plays returned PCM.

**Acceptance criteria:**

- No cloud credentials on device.
- Bridge can be started manually in RAM/session context.
- Crash is recoverable by process restart or power cycle.
- 10-minute loop test passes without memory/process runaway.

### M7 — Home Assistant/Wyoming adapter

**Status:** blocked on M6.

**Outcome:** expose the voice path to Home Assistant Assist through Wyoming-compatible service boundaries.

**Reference direction:** Home Assistant's Wyoming integration connects external STT, TTS, and wake-word services to Assist; wake-word processing can run outside the satellite when scaling beyond tiny devices.

**Acceptance criteria:**

- Home Assistant discovers or can manually configure the service.
- Wake word and ASR can run off-device.
- Invoke remains just audio I/O plus minimal control plane.

### M8 — Minimal persistence

**Status:** intentionally deferred.

**Outcome:** optional persistent install in a verified writable app/data area, not bootloader/rootfs.

**Prerequisites:**

- Full partition dump exists.
- Restore path is documented and tested as far as practical.
- Disable/kill switch exists.
- Joe explicitly approves the exact persistent write.

## Priority stack and decision tree

1. If the 2026-06-21 RAM session is still live, preserve it: leave power and USB
   connected and continue only through `/dev/cu.usbmodemno_nand_probe_1` with
   read-only or explicitly approved RAM-only commands.
2. If the device was power-cycled, re-check M0 Bluetooth baseline and run
   `make surface-watch` / `make no-nand-readiness` before any repeat service-mode
   attempt. If readiness returns `hold_no_custom_boot`, restore either normal
   Bluetooth/audio baseline or Marvell service-loader visibility before arming a
   RAM listener.
3. Source-owned M3 repeatability now uses
   `scripts/hk-invoke/no_nand_serial_inventory.py` for the proven USB ACM root
   shell. The helper preserves transcripts/metadata and keeps default commands
   read-only; `--check` and `--print-commands` expose the contract without
   opening serial.
4. No-NAND helper portability now depends on explicit `/bin/busybox` applet
   calls for generated hardware/audio/network inventory scripts; regenerate a
   fresh artifact before any next proof and do not reuse pre-fix artifacts.
5. Use `make no-nand-plan` as the offline planning gate: it should record
   rootfs-82 candidates, OTA83 SD8887/ALSA evidence, and approval-gated
   RAM-only module/Wi-Fi templates without auto-loading anything.
6. Use `make no-nand-initramfs-ota83` only for an explicitly approved
   SD8887/Bluetooth module-load session. It stages OTA83 candidates under
   `/ota83-stage` and adds `/no-nand-ota83-module-load.sh`, but the helper must
   still refuse until `HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED=yes` is set in
   the live RAM shell.
7. For any read-only audio fact pass, regenerate `make no-nand-initramfs` and
   run `sh /no-nand-audio-inventory.sh` from the RAM shell before any audio
   proof or module-load escalation.
8. For any read-only network fact pass, regenerate `make no-nand-initramfs` and
   run `sh /no-nand-network-inventory.sh` from the RAM shell before any Wi-Fi
   association or USB-network reconfiguration.
9. For any RAM-only Wi-Fi proof, regenerate `make no-nand-initramfs` and use
   only a current `/no-nand-wifi.sh` helper that refuses unless
   `HK_INVOKE_RAM_WIFI_APPROVED=yes` plus runtime-only
   `HK_INVOKE_RAM_WIFI_SSID`/`HK_INVOKE_RAM_WIFI_PSK` or named runtime-only
   `HK_INVOKE_RAM_WIFI_YOURSSID_PSK`
   is set in the live shell after Joe's exact approval. Named profiles target
   `YOURSSID` at priority 22, `YOURSSID` at priority 20, and `yourssid` at priority 19 without storing
   the PSK in the artifact.
10. For any RAM-only USB-network proof, regenerate `make no-nand-initramfs` and
   use only a current generated `/no-nand-usbnet.sh` helper that refuses unless
   `HK_INVOKE_RAM_USBNET_APPROVED=yes` is set in the live shell after Joe's
   exact approval for live USB gadget/network changes.
11. Prove or falsify native audio/network surfaces: ALSA device names, sample
   rates, channel counts, mixer state, SDIO/Wi-Fi module path, USB networking,
   and host-visible ADB.
12. Use M5 host/server voice prototype in parallel or as fallback whenever the
   product question is "can the assistant loop work?" rather than "can Invoke
   Linux expose audio?" This path has no device firmware dependency.
13. Start M4 and M6 only after native audio facts exist: playback, capture, and a
   safe serial/network process boundary.
14. Start M7 only after the bridge has a stable host/device service boundary.
15. Start M8 only after full dump/restore evidence and exact persistent-write
   approval.

## Risk register

| Risk | Mitigation |
| --- | --- |
| Accidental NAND write | Guarded wrapper refuses known dangerous commands; operator runbook requires listener before service mode |
| Secret leakage | `.gitignore`, validation, no credentials in docs, keys provisioned only through secure local flow |
| Losing Bluetooth baseline | Run `make state` after experiments; power-cycle check is a milestone gate |
| Wrong firmware assumptions | Prefer on-device inventory over OTA guesses |
| Audio stack complexity | Prove ALSA playback/capture before LiveKit/HA integration |
| Overbuilding bridge | Start with PCM bridge only; no on-device AI credentials or complex orchestration |
