#!/usr/bin/env zsh
set -euo pipefail

script_dir=${0:A:h}
check_only=false
if [[ "${1:-}" == "--check-only" ]]; then
  check_only=true
  shift
fi

image_dir=${1:-${HK_INVOKE_OTA2_DIR:-/tmp/hk-invoke-ota2-work-current}}
boot_bin=${HK_INVOKE_BOOT_BIN:-$HOME/.local/state/hk-invoke/bin/hk_usb_boot}
session_root=${HK_INVOKE_SESSION_ROOT:-$HOME/.local/state/hk-invoke/sessions}
session_id=${HK_INVOKE_SESSION_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
session_dir=$session_root/$session_id

usage() {
  print 'usage: scripts/hk-invoke/ram_boot_console.sh [--check-only] [OTA2-image-dir]'
  print
  print 'Starts a guarded USB boot listener for yellow/service-mode RAM-boot work.'
  print 'It refuses to run if 79_IMAGE contains NAND-writing U-Boot commands.'
  print '--check-only runs the image/native safety preflight and exits without USB polling.'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -d "$image_dir" ]]; then
  print -u2 "ERROR: OTA2 image directory not found: $image_dir"
  exit 2
fi

for required in 79_IMAGE 81_IMAGE 82_IMAGE; do
  if [[ ! -f "$image_dir/$required" ]]; then
    print -u2 "ERROR: missing $image_dir/$required"
    exit 2
  fi
done

if ! command -v rg >/dev/null 2>&1; then
  print -u2 'ERROR: rg is required for the NAND-command safety scan'
  exit 2
fi

forbidden='\b(l2nand|tftp2nand|usb2nand|tftp2emmc|usb2emmc|nanderase|nandinit|nandmarkbad|nandverify|nandwr|nand[[:space:]]+(write|erase)|mmc[[:space:]]+write|run[[:space:]]+upgrade|protect|erase|saveenv)\b'
if rg -n -i -- "$forbidden" "$image_dir/79_IMAGE"; then
  print -u2
  print -u2 "ERROR: refusing to serve $image_dir/79_IMAGE because it contains persistent-write commands."
  print -u2 'Use a RAM-boot-only 79_IMAGE before starting this listener.'
  exit 3
fi

if [[ ! -x "$boot_bin" ]]; then
  boot_bin=$("$script_dir/build_native_boot.sh")
fi

"$boot_bin" check-images "$image_dir"

if $check_only; then
  print "check-only passed for $image_dir"
  exit 0
fi

print '== guarded Invoke RAM-boot listener =='
print "image_dir: $image_dir"
print "boot_bin:  $boot_bin"
print "session:   $session_dir"
print
print 'When the listener says it is waiting for the Marvell USB device:'
print '  1. unplug Invoke power'
print '  2. hold reset'
print '  3. plug power back in while still holding reset'
print '  4. 4-click Mic-Off to yellow/service mode'
print
print 'Safety: this wrapper does not send persistent-write commands; it also'
print 'scanned 79_IMAGE for NAND/eMMC/SPI/env write commands.'
print 'The native USB console also blocks those command classes before USB OUT.'
print
mkdir -p "$session_dir"

{
  print "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  print "classification=RAM-only"
  print "image_dir=$image_dir"
  print "boot_bin=$boot_bin"
  print "transcript=$session_dir/console-transcript.txt"
  print "note=No NAND writes are automated; native console blocks persistent/NAND-affecting commands."
} > "$session_dir/command.log"

"$script_dir/hk_invoke_state.sh" > "$session_dir/device-state-before.txt" 2>&1 || true

set +e
"$boot_bin" auto "$image_dir" 2>&1 | tee "$session_dir/console-transcript.txt"
boot_status=${pipestatus[1]}
set -e

"$script_dir/hk_invoke_state.sh" > "$session_dir/device-state-after.txt" 2>&1 || true
print "session artifacts written to: $session_dir"
exit "$boot_status"
