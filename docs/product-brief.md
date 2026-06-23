# Product Brief

## One-liner

Recover the Harman Kardon Invoke as a high-quality, safe-to-hack voice satellite whose audio I/O can be routed to server-side voice agents and Home Assistant without putting cloud credentials or unrecoverable firmware changes on the device.

## Users

| User | Need | Success looks like |
| --- | --- | --- |
| Joe, hardware owner/operator | A clear next physical action and confidence the device will not be re-bricked casually | Joe knows when to plug/reset/click and when not to |
| Agentic developer | Safe tooling, evidence paths, and reversible gates | Scripts refuse destructive commands and docs state exact acceptance criteria |
| Future household user | A responsive speaker/mic voice endpoint | Wake phrase, natural response, reliable audio playback |
| Home Assistant admin | Local control and integration with Assist | Wyoming/Assist sees a satellite or server adapter |

## Jobs to be done

1. **Recover baseline:** preserve the official OTA2 Bluetooth-speaker state so experiments have a known-good rollback target.
2. **Discover safely:** learn the Linux, audio, storage, and network shape without persistent writes.
3. **Prove audio:** capture microphone audio and play speaker audio from Linux userspace.
4. **Prototype intelligence off-device:** run wake word/ASR/LLM/TTS or realtime speech-to-speech on a trusted host.
5. **Bridge the Invoke:** stream PCM between Invoke ALSA and the trusted host with no device-held API keys.
6. **Integrate Home Assistant:** expose the bridge through Wyoming/Assist once the lower-level audio path is reliable.

## Goals

- Preserve Bluetooth speaker behavior as an always-checkable baseline.
- Keep secrets server-side or in 1Password/local ignored env files.
- Build the smallest reversible bridge that proves the hardware is useful.
- Favor server-side models and orchestration over trying to run modern AI on the Invoke.
- Document every hardware mode and rollback gate before using it.

## Non-goals for v1

- No custom bootloader.
- No NAND persistence before full dump/restore proof.
- No OpenAI/LiveKit/Home Assistant credentials on the Invoke.
- No attempt to run large ASR/LLM models on the Invoke hardware.
- No polished consumer UX until basic audio bridge reliability is proven.

## Product principles

1. **Recoverability beats speed.** RAM-only experiments first; persistent installs only after rollback proof.
2. **Device is an audio peripheral.** Intelligence belongs on a host/server unless a tiny local function is clearly safe.
3. **Secrets do not travel to the device.** The bridge transports audio/control, not cloud credentials.
4. **Hardware evidence wins.** Every assumption about ALSA, Wi-Fi, services, partitions, and persistence gets verified on-device.
5. **Operator steps must be explicit.** If Joe needs to reset, click, unplug, or plug USB, docs say exactly when.

## Success metrics

| Stage | Metric |
| --- | --- |
| Baseline | `make state` detects Bluetooth output and no service-loader device in normal boot |
| RAM shell | shell access achieved without NAND writes; power cycle returns to Bluetooth |
| Inventory | partition/audio/network reports captured under `~/.local/state/hk-invoke/` |
| Audio proof | 5-second mic sample and speaker playback verified from Invoke Linux |
| Server prototype | host-side LiveKit/OpenAI voice loop works without device dependency |
| Bridge proof | Invoke streams mic PCM to host and plays returned PCM for 10 minutes |
| HA proof | Home Assistant Assist can invoke a response through a Wyoming-compatible path |
