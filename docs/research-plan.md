# Invoke Voice-Satellite Research Plan

## Goal

Turn the recovered Harman Kardon Invoke into a safe research target for a future voice satellite: Invoke hardware captures audio and plays responses, while LiveKit Agents and OpenAI Realtime models run server-side.

## Phase 0: Baseline preservation

- Preserve OTA2 payloads, native USB boot source/binary, firmware extraction notes, live Bluetooth state, and checksums under `~/.local/state/hk-invoke/recovery-baselines/`.
- Keep repo-tracked files small; do not commit OTA payloads or NAND dumps.
- Verify normal Bluetooth playback after every experiment.
- Implemented support:
  - `scripts/hk-invoke/preserve_baseline.sh`
  - `scripts/hk-invoke/hk_invoke_state.sh`

## Phase 1: Non-invasive discovery

- Do not SSH until the Invoke has a proven IP and open service.
- Run normal-boot host observation first: ARP, mDNS, router leases if
  available, with any protocol-client probes labeled explicitly.
- If USB is needed, plug micro-USB in normal boot only: no reset, no Mic-Off 4-click, no yellow mode.
- Observe USB descriptors and new host network interfaces first. Run
  `adb devices -l` only with explicit `--adb-devices` opt-in because it is a
  read-only protocol handshake, not passive observation.
- Implemented support:
  - `scripts/hk-invoke/normal_boot_probe.sh`

## Phase 2: Offline firmware map

- Parse `83_IMAGE` header and partition metadata.
- Split or identify payloads only after the format is confirmed.
- Distinguish USB-boot initramfs services from normal runtime services.
- Pull GPL/source/prior-art references before custom build work.
- Implemented support:
  - `scripts/hk-invoke/parse_ota83.py`
  - `scripts/hk-invoke/extract_ota83.py`

## Phase 3: RAM-boot debug shell

- Use U-Boot RAM boot/debug bootargs, not NAND writes.
- Forbidden in automation: any persistent-write U-Boot path, including
  NAND/eMMC/SPI/env write commands such as `l2nand`, `usb2nand`, `nand write`,
  `mmc write`, `erase`, `protect`, `run upgrade`, or `saveenv`.
- Acceptance: shell access plus read-only inventory, then normal power cycle returns to Bluetooth mode.
- Implemented support:
  - `scripts/hk-invoke/build_native_boot.sh`
  - `scripts/hk-invoke/ram_boot_console.sh`

## Phase 4: Device inventory and rollback

- Inventory `/proc/mtd`, mounts, interfaces, `/dev/snd`, input events, services, and writable partitions.
- Dump MTD partitions before any persistence attempt.
- Verify dump size/hash and document restore path.

## Phase 5: Audio proof

- Prove ALSA speaker playback.
- Prove mic-array capture with a 5-second sample.
- Characterize sample rate, channels, noise, echo, and mixer controls.

## Phase 6: LiveKit/OpenAI prototype

- Build server-side LiveKit Agent using OpenAI Realtime.
- Keep OpenAI and LiveKit credentials off-device.
- Prototype with Mac mic/Bluetooth output first, then replace with Invoke PCM bridge.

## Phase 7: Invoke bridge daemon

- Minimal ARMv7 userland process or script.
- Capture PCM from ALSA, send to LAN bridge, receive PCM for playback.
- No cloud credentials on device.
- Crash must be recoverable by power cycle.

## Phase 8: Persistence after rollback only

- Persistent install only in a verified app/data partition.
- No bootloader/rootfs rewrite for v1.
- Include a disable/kill switch and recovery instructions.

## Phase 9: Home Assistant adapter

- Prefer server-side Wyoming adapter after LiveKit path works.
- Wake word and ASR should run off-device by default.
