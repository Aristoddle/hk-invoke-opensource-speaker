#!/usr/bin/env zsh
set -euo pipefail

script_dir=${0:A:h}
out_dir=${1:-$HOME/.local/state/hk-invoke/bin}
mkdir -p "$out_dir"

if ! command -v pkg-config >/dev/null 2>&1; then
  print -u2 'ERROR: pkg-config is required to build hk_usb_boot'
  exit 2
fi

if ! pkg-config --exists libusb-1.0; then
  print -u2 'ERROR: libusb-1.0 is required. Install with: brew install libusb'
  exit 2
fi

cc -Wall -Wextra -O2 \
  $(pkg-config --cflags libusb-1.0) \
  "$script_dir/hk_usb_boot.c" \
  $(pkg-config --libs libusb-1.0) \
  -o "$out_dir/hk_usb_boot"

print "$out_dir/hk_usb_boot"
