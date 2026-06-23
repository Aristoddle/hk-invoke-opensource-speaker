# Architecture

## Architecture thesis

The Invoke should become a thin audio/control endpoint. Wake word, ASR, reasoning, tools, TTS, Home Assistant integration, and cloud credentials should live on a trusted host/server.

There are two useful paths:

- **Host-assistant fallback:** prove the voice assistant with host audio and
  Bluetooth output. This path is available before any device firmware work and
  stays useful if native access stalls.
- **Native-connectivity path:** use service mode and RAM-only no-NAND booting
  to learn whether Invoke Linux can expose mic/speaker/network surfaces. This
  path is required before the Invoke can become a real mic/speaker bridge, but
  it must not depend on persistent writes.

## Staged topologies

### Stage 0 — recovered Bluetooth speaker

```text
Mac/iPhone ──Bluetooth A2DP──> Invoke speaker
```

Purpose: known-good baseline and daily utility.

### Stage 1 — host voice prototype

```text
Mac mic/browser/CLI ──LiveKit room──> LiveKit Agent ──OpenAI Realtime──> response audio ──Bluetooth──> Invoke
```

Purpose: validate agent behavior before depending on Invoke Linux access.

Current gate: host-side dry runs and Mac audio inventory can proceed now. This
does not prove Invoke microphones, USB networking, ALSA, or firmware access; it
only de-risks the assistant loop.

### Stage 2 — RAM-boot hardware inventory

```text
Mac hk_usb_boot ──USB service mode/U-Boot──> Invoke RAM kernel/initramfs
                                           └──> shell or host-visible Linux surfaces
```

Purpose: inspect hardware and runtime capabilities without persistent writes.

Current gate: Marvell service-loader and U-Boot access are proven. The
generated no-NAND artifact booted when U-Boot commands were fed line-by-line,
and a RAM-only root shell appeared over USB ACM. Read-only storage/process/kernel
inventory is captured. The minimal initramfs currently exposes USB ACM and an
Android gadget configured as `acm,adb`; host-visible ADB, USB networking, and
ALSA audio devices remain unproven. SDIO inventory shows Marvell `02DF:9135`,
`02DF:9136`, and `02DF:9137`, which is the next Wi-Fi/Bluetooth driver target.

### Stage 3 — Invoke PCM bridge

```text
Invoke ALSA mic ──LAN PCM/RTP/WebRTC bridge──> trusted host ──agent/model──> trusted host ──PCM──> Invoke ALSA speaker
```

Purpose: use Invoke mic/speaker hardware while keeping intelligence off-device.

Current gate: blocked until the RAM shell evidence identifies audio device
names, capture/playback formats, and a safe network or serial process boundary
for an Invoke-side bridge.

### Stage 4 — Home Assistant adapter

```text
Invoke bridge ──trusted host adapter──> Wyoming/Home Assistant Assist
                                 └──> LiveKit/OpenAI agent path
```

Purpose: support both smart-home Assist workflows and general realtime-agent workflows.

## Component boundaries

| Component | Runs on | Responsibility | Holds secrets? |
| --- | --- | --- | --- |
| `hk_usb_boot` | Mac | USB service-mode boot/image serving | no |
| RAM shell scripts | Invoke RAM session | inventory and audio proof | no |
| PCM bridge | Invoke | capture/play PCM and minimal health/control | no |
| Agent host | Mac/server | LiveKit room, OpenAI Realtime session, orchestration | yes, host only |
| HA adapter | Mac/server/Home Assistant host | Wyoming/Assist integration | maybe HA token, host only |

## Decision gates

| Question | Choose | Gate before moving on |
| --- | --- | --- |
| Need useful assistant behavior before firmware access? | Host-assistant fallback | Host dry-run plus host audio route works; no Invoke firmware dependency claimed |
| Need Invoke microphones/speaker from Linux? | Native-connectivity path | RAM shell exists; next prove ALSA devices and a safe transport without NAND writes |
| Did `82_IMAGE` transfer fail or U-Boot stay in service mode? | Stop native attempt | Preserve transcript; power-cycle/service-mode retry only; no NAND writes |
| Want persistence? | Defer | Full read-only inventory plus dump/restore evidence and exact Joe approval |

## OpenAI/LiveKit direction

Current source-grounded assumptions:

- LiveKit Agents can run Python or Node programs as realtime participants in LiveKit rooms.
- LiveKit supports realtime model flows, including an OpenAI Realtime plugin. The current LiveKit docs show Python installation with `uv add "livekit-agents[openai]~=1.5"` and usage through `AgentSession` plus `openai.realtime.RealtimeModel`.
- OpenAI Realtime is the low-latency audio path. Current OpenAI docs describe WebRTC, WebSocket, and SIP options, and recommend WebRTC for browser/mobile realtime audio clients.
- Server-side tools/business logic should stay on the application server rather than in untrusted clients or on the Invoke.

References:

- LiveKit Agents overview: https://docs.livekit.io/agents/
- LiveKit Voice AI quickstart: https://docs.livekit.io/agents/start/voice-ai/
- LiveKit OpenAI Realtime plugin: https://docs.livekit.io/agents/models/realtime/plugins/openai/
- OpenAI Realtime and audio: https://developers.openai.com/api/docs/guides/realtime
- OpenAI Realtime WebRTC: https://developers.openai.com/api/docs/guides/realtime-webrtc

## Home Assistant direction

Current source-grounded assumptions:

- Home Assistant's Wyoming integration connects external voice services to Assist.
- Wyoming can cover speech-to-text, text-to-speech, and wake-word services.
- Home Assistant's wake-word docs describe openWakeWord and streaming wake-word setup, with custom wake word models as a later path.
- For this project, wake word and ASR should default to off-device processing until Invoke CPU/audio constraints are known.

References:

- Wyoming integration: https://www.home-assistant.io/integrations/wyoming/
- Assist overview: https://www.home-assistant.io/voice_control/
- Wake words for Assist: https://www.home-assistant.io/voice_control/create_wake_word/
- Home Assistant wake-word approach: https://www.home-assistant.io/voice_control/about_wake_word/

## Security model

- The Invoke is not trusted to hold cloud credentials.
- The first bridge protocol should be LAN-only and mutually constrained by host firewall/network placement.
- All long-lived secrets stay in ignored local env files or 1Password.
- Any browser/mobile Realtime client should use ephemeral credentials or a server-mediated setup, not a pasted long-lived key.
- Persisting anything to Invoke storage is out of scope until the dump/restore path is proven.
