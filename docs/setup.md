# Setup

## Repositories and paths

Parent chezmoi repo:

```bash
cd /Users/joe/.local/share/chezmoi
```

Submodule checkout:

```bash
cd /Users/joe/.local/share/chezmoi/projects/hk-invoke-voice-satellite
```

Current canonical local checkout:

```bash
cd /Users/joe/.local/share/chezmoi/projects/hk-invoke-voice-satellite
```

Private remote:

```text
git@github.com:Aristoddle/hk-invoke-voice-satellite.git
```

## Local readiness check

Run:

```bash
make setup-check
make validate
```

`make setup-check` is non-mutating. It reports required and optional tools, checks whether known local firmware artifacts are present, and verifies the current git branch.

`make validate` runs syntax checks, OTA parser checks when local OTA artifacts exist, native boot build when `libusb` is available, and the RAM-boot NAND-command guard.

## Required host tools

Required for current docs/scripts:

- `zsh`
- `git`
- `python3`
- `rg`
- `fd`
- `cc` / Apple clang
- `pkg-config`
- `libusb-1.0`

Useful optional tools:

- `blueutil` for Bluetooth state checks.
- `7z` or `p7zip` for inspecting SquashFS/zip payloads.
- `gh` for GitHub repo/submodule management.
- `nc`, `dns-sd`, `ifconfig`, `system_profiler` for host-observation probes.
- `uv` for the future Python LiveKit agent prototype.

On this Mac, use `mise` for tool shims where applicable and Homebrew for system libraries such as `libusb`.

## Local artifact policy

Keep large or sensitive artifacts here, not in git:

```text
~/.local/state/hk-invoke/
```

Known local artifacts from the recovery session:

```text
~/.local/state/hk-invoke/recovery-baselines/20260619T012008/
~/.local/state/hk-invoke/ota83-extracts/20260619T012626/
/tmp/hk-invoke-ota2-work-current/
/tmp/hk-invoke-native/
```

Do not commit:

- OTA zips.
- `*_IMAGE` payloads.
- NAND/MTD dumps.
- extracted partitions.
- factory certs/keys.
- Wi-Fi credentials.
- cloud/API credentials.

## Credentials policy

Credentials are not needed for the current hardware discovery work.

When we reach the LiveKit/OpenAI prototype milestone:

- OpenAI credentials must be provisioned through the secure OpenAI Platform/local-secret flow or 1Password, not pasted into chat or committed.
- LiveKit credentials must live in an ignored local env file or 1Password, not on the Invoke.
- The Invoke should never receive OpenAI, LiveKit, or Home Assistant long-lived credentials.

A future prototype may use these environment variable names on the trusted host only:

```text
OPENAI_API_KEY
LIVEKIT_URL
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
HOME_ASSISTANT_URL
HOME_ASSISTANT_TOKEN
```

Do not create or fill these until the API-backed prototype is actually being built.

## Current validation loop

```bash
cd /Users/joe/.local/share/chezmoi/projects/hk-invoke-voice-satellite
make setup-check
make validate
make state
```

Expected today:

- validation passes.
- state shows `HK Invoke_C114D2` if the speaker is powered and paired.
- state shows no `1286:8174` device in normal boot.

## Updating the parent submodule pointer

After committing and pushing child repo changes:

```bash
cd /Users/joe/.local/share/chezmoi

git -C projects/hk-invoke-voice-satellite fetch origin
git -C projects/hk-invoke-voice-satellite checkout main
git -C projects/hk-invoke-voice-satellite pull --ff-only

git add projects/hk-invoke-voice-satellite
git diff --cached --name-only
git commit -m "chore(hk-invoke): advance voice satellite submodule [Codex-HKInvoke]"
git push
```
