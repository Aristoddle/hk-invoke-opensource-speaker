#!/usr/bin/env zsh
set -euo pipefail

boot_bin=${HK_INVOKE_BOOT_BIN:-$HOME/.local/state/hk-invoke/bin/hk_usb_boot}
if [[ ! -x "$boot_bin" && -x /tmp/hk-invoke-native/hk_usb_boot ]]; then
  boot_bin=/tmp/hk-invoke-native/hk_usb_boot
fi

state_probe_timeout=${HK_INVOKE_STATE_PROBE_TIMEOUT:-10}
if ! [[ "$state_probe_timeout" =~ '^[0-9]+$' ]]; then
  print -u2 "ERROR: HK_INVOKE_STATE_PROBE_TIMEOUT must be a non-negative integer, got: $state_probe_timeout"
  exit 2
fi

run_bounded() {
  python3 - "$state_probe_timeout" "$@" <<'PY_TIMEOUT'
import subprocess
import sys

try:
    timeout = int(sys.argv[1])
except ValueError:
    print("ERROR: HK_INVOKE_STATE_PROBE_TIMEOUT must be a non-negative integer", file=sys.stderr)
    sys.exit(2)

cmd = sys.argv[2:]
try:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        check=False,
    )
except FileNotFoundError:
    sys.exit(127)
except subprocess.TimeoutExpired as exc:
    output = exc.stdout or ""
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    sys.stdout.write(output)
    print(f"WARN: timed out after {timeout}s: {' '.join(cmd)}", file=sys.stderr)
    sys.exit(124)

sys.stdout.write(proc.stdout)
sys.exit(proc.returncode)
PY_TIMEOUT
}

print '== Bluetooth connected matching Invoke/HK/Harman/Kardon =='
if command -v blueutil >/dev/null 2>&1; then
  run_bounded blueutil --connected 2>/dev/null | rg -i 'Invoke|HK|Harman|Kardon' || true
else
  print 'blueutil not installed'
fi

print '\n== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth =='
run_bounded system_profiler SPAudioDataType 2>/dev/null | rg -i 'HK Invoke|Invoke|Harman|Kardon|Bluetooth' -C 2 || true

print '\n== Marvell service-loader USB check =='
if [[ -x "$boot_bin" ]]; then
  run_bounded "$boot_bin" detect || true
else
  print "hk_usb_boot not built. Run: scripts/hk-invoke/build_native_boot.sh"
  run_bounded system_profiler SPUSBDataType -detailLevel mini 2>/dev/null | rg -i '1286|8174|BG2CD|Marvell' -C 3 || true
fi

print '\n== ARP candidates matching Invoke/HK/Harman/Kardon/BT OUI =='
run_bounded arp -a 2>/dev/null | rg -i 'Invoke|HK|Harman|Kardon|d8[:-]f7|d8f7' || true
