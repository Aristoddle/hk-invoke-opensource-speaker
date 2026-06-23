#!/usr/bin/env zsh
set -euo pipefail

usage() {
  cat <<'USAGE'
usage: scripts/hk-invoke/normal_boot_probe.sh [--ip <candidate-ip>] [--adb-devices] [--check]

Normal-boot Invoke host-observation probe. Do not use reset/yellow mode for this.

Default mode gathers local Bluetooth/audio/USB/ARP/mDNS evidence only and does
not run protocol clients against the device.
With --ip, it additionally runs targeted TCP connect checks on common debug ports.
With --adb-devices, it additionally runs `adb devices -l`; this is an explicit
read-only protocol handshake probe, not passive host observation.
With --check, it prints the selected safety/config flags and exits without host
or device observation.
USAGE
}

print_config() {
  print "classification=normal-boot-host-observation"
  print "adb_devices_probe=$adb_devices_probe"
  if [[ -n "$candidate_ip" ]]; then
    print "tcp_connect_probe=true"
  else
    print "tcp_connect_probe=false"
  fi
}

candidate_ip=''
adb_devices_probe=false
check_only=false
mdns_seconds=${HK_INVOKE_MDNS_SECONDS:-5}
if ! [[ "$mdns_seconds" =~ '^[0-9]+$' ]]; then
  print -u2 "ERROR: HK_INVOKE_MDNS_SECONDS must be a non-negative integer, got: $mdns_seconds"
  exit 2
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip)
      candidate_ip=${2:-}
      if [[ -z "$candidate_ip" ]]; then
        print -u2 'ERROR: --ip requires a value'
        exit 2
      fi
      shift 2
      ;;
    --adb-devices)
      adb_devices_probe=true
      shift
      ;;
    --check)
      check_only=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      print -u2 "ERROR: unknown argument: $1"
      usage >&2
      exit 2
      ;;
  esac
done

print_config
if [[ "$check_only" == true ]]; then
  exit 0
fi

print '== baseline state =='
scripts/hk-invoke/hk_invoke_state.sh || true

print '\n== normal-boot USB interfaces matching Invoke/Marvell/service IDs =='
system_profiler SPUSBDataType -detailLevel mini 2>/dev/null | rg -i '1286|8174|BG2CD|Marvell|Invoke|Harman|Kardon|ADB|RNDIS|ECM|serial' -C 4 || true

if [[ "$adb_devices_probe" == true ]]; then
  print '\n== adb devices, explicit opt-in =='
  adb_bin=${HK_INVOKE_ADB_BIN:-}
  if [[ -z "$adb_bin" ]] && command -v adb >/dev/null 2>&1; then
    adb_bin=$(command -v adb)
  elif [[ -z "$adb_bin" && -x /tmp/hk-invoke-ota2-work-current/adb ]]; then
    adb_bin=/tmp/hk-invoke-ota2-work-current/adb
  fi
  if [[ -n "$adb_bin" && -x "$adb_bin" ]]; then
    "$adb_bin" devices -l || true
  else
    print 'adb not available'
  fi
else
  print '\n== adb devices =='
  print 'skipped by default; pass --adb-devices for explicit read-only ADB handshake probe'
fi

print '\n== network interfaces snapshot =='
ifconfig 2>/dev/null | rg -n '^[a-z0-9]+:|status: active|inet |ether ' || true

print '\n== ARP candidates =='
arp -a 2>/dev/null | rg -i 'Invoke|HK|Harman|Kardon|d8[:-]f7|d8f7' || true

print "\n== bounded mDNS browse (${mdns_seconds}s) =="
mdns_tmp=$(mktemp -t hk-invoke-mdns.XXXXXX)
mdns_pid=''
stop_mdns() {
  if [[ -n "${mdns_pid:-}" ]]; then
    kill "$mdns_pid" >/dev/null 2>&1 || true
    wait "$mdns_pid" >/dev/null 2>&1 || true
    mdns_pid=''
  fi
}
cleanup_mdns() {
  stop_mdns
  if [[ -n "${mdns_tmp:-}" ]]; then
    rm -f "$mdns_tmp"
  fi
}
(dns-sd -B _services._dns-sd._udp local > "$mdns_tmp" 2>&1) &
mdns_pid=$!
trap cleanup_mdns EXIT INT TERM
sleep "$mdns_seconds"
stop_mdns
trap - EXIT INT TERM
rg -i 'invoke|harman|kardon|spotify|googlecast|airplay|raop|adb|ssh|telnet' "$mdns_tmp" || true
rm -f "$mdns_tmp"

if [[ -n "$candidate_ip" ]]; then
  print "\n== targeted port checks for $candidate_ip =="
  for port in 22 23 80 443 5555 8008 8009 8080 10700; do
    nc -G 2 -vz "$candidate_ip" "$port" 2>&1 || true
  done
fi
