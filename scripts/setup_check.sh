#!/usr/bin/env zsh
set -euo pipefail

repo=${0:A:h:h}
cd "$repo"

print '== repo =='
printf 'path: %s\n' "$repo"
git status --short --branch

print '\n== required tools =='
required=(zsh git python3 rg fd cc pkg-config)
missing=0
for cmd in $required; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf 'ok   %-14s %s\n' "$cmd" "$(command -v "$cmd")"
  else
    printf 'MISS %-14s\n' "$cmd"
    missing=1
  fi
done

print '\n== libusb =='
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists libusb-1.0; then
  printf 'ok   libusb-1.0     %s\n' "$(pkg-config --modversion libusb-1.0)"
else
  print 'MISS libusb-1.0     required to build hk_usb_boot'
  missing=1
fi

print '\n== optional tools =='
optional=(blueutil gh 7z unzip nc dns-sd ifconfig system_profiler uv)
for cmd in $optional; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf 'ok   %-14s %s\n' "$cmd" "$(command -v "$cmd")"
  else
    printf 'skip %-14s\n' "$cmd"
  fi
done

print '\n== known local artifacts =='
artifacts=(
  /tmp/hk-invoke-ota2-work-current/83_IMAGE
  /tmp/hk-invoke-native/hk_usb_boot
  $HOME/.local/state/hk-invoke/recovery-baselines/20260619T012008
  $HOME/.local/state/hk-invoke/ota83-extracts/20260619T012626
)
for artifact_path in $artifacts; do
  if [[ -e "$artifact_path" ]]; then
    printf 'ok   %s\n' "$artifact_path"
  else
    printf 'skip %s\n' "$artifact_path"
  fi
done

print '\n== bluetooth/audio state =='
if [[ -x scripts/hk-invoke/hk_invoke_state.sh ]]; then
  zsh scripts/hk-invoke/hk_invoke_state.sh || true
fi

if [[ $missing -ne 0 ]]; then
  print '\nsetup-check completed with missing required dependencies'
  exit 1
fi

print '\nsetup-check passed'
