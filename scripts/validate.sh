#!/usr/bin/env zsh
set -euo pipefail

repo=${0:A:h:h}
cd "$repo"

validate_tmp=$(mktemp -d)
trap 'rm -rf "$validate_tmp"' EXIT

zsh -n scripts/hk-invoke/*.sh
python3 -m py_compile scripts/hk-invoke/*.py
python3 - <<'PY'
from pathlib import Path

state = Path("scripts/hk-invoke/hk_invoke_state.sh").read_text()
makefile = Path("Makefile").read_text()
assert "run_bounded()" in state, state
assert "HK_INVOKE_STATE_PROBE_TIMEOUT" in state, state
assert 'run_bounded blueutil --connected' in state, state
assert 'run_bounded system_profiler SPAudioDataType' in state, state
assert 'run_bounded "$boot_bin" detect' in state, state
assert 'run_bounded arp -a' in state, state
assert "surface-watch:" in makefile, makefile
assert "scripts/hk-invoke/surface_watch.py" in makefile, makefile
print("state probe bounded-command validation passed")
PY
scripts/hk-invoke/normal_boot_probe.sh --check >$validate_tmp/hk-invoke-normal-boot-probe-check.out
rg -q '^adb_devices_probe=false$' $validate_tmp/hk-invoke-normal-boot-probe-check.out
scripts/hk-invoke/normal_boot_probe.sh --check --adb-devices >$validate_tmp/hk-invoke-normal-boot-probe-adb-check.out
rg -q '^adb_devices_probe=true$' $validate_tmp/hk-invoke-normal-boot-probe-adb-check.out
if HK_INVOKE_MDNS_SECONDS=not-a-number scripts/hk-invoke/normal_boot_probe.sh --check >$validate_tmp/hk-invoke-normal-boot-probe-bad-mdns.out 2>&1; then
  print -u2 'ERROR: normal_boot_probe.sh allowed a malformed HK_INVOKE_MDNS_SECONDS value'
  exit 1
fi
rg -q 'HK_INVOKE_MDNS_SECONDS must be a non-negative integer' $validate_tmp/hk-invoke-normal-boot-probe-bad-mdns.out
normal_probe_sim=$(mktemp -d)
trap 'rm -rf "$normal_probe_sim" "$validate_tmp"' EXIT
normal_probe_bin=$normal_probe_sim/bin
mkdir -p "$normal_probe_bin"
cat > "$normal_probe_bin/blueutil" <<'SH'
#!/bin/sh
printf 'address: aa-bb-cc-dd-ee-ff, connected: 1, name: "HK Invoke_C114D2"\n'
SH
cat > "$normal_probe_bin/system_profiler" <<'SH'
#!/bin/sh
case "$1" in
  SPAudioDataType)
    printf 'Audio:\n  HK Invoke_C114D2:\n    Default Output Device: Yes\n'
    ;;
  SPUSBDataType)
    printf 'USB:\n  harmless normal-boot fixture\n'
    ;;
  *)
    printf 'system_profiler fixture\n'
    ;;
esac
SH
cat > "$normal_probe_bin/hk_usb_boot" <<'SH'
#!/bin/sh
printf 'no Invoke/Marvell WTP device currently visible\n'
SH
cat > "$normal_probe_bin/ifconfig" <<'SH'
#!/bin/sh
printf 'en0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500\n\tinet 192.0.2.10 netmask 0xffffff00\n'
SH
cat > "$normal_probe_bin/arp" <<'SH'
#!/bin/sh
printf '? (192.0.2.1) at aa:bb:cc:dd:ee:01 on en0 ifscope [ethernet]\n'
SH
cat > "$normal_probe_bin/dns-sd" <<'SH'
#!/bin/sh
printf 'Browsing for _services._dns-sd._udp.local\n'
sleep 30
SH
cat > "$normal_probe_bin/adb" <<'SH'
#!/bin/sh
printf 'adb-called\n' >> "$HK_INVOKE_SIM_ADB_LOG"
printf 'List of devices attached\n'
SH
chmod +x "$normal_probe_bin"/*
PATH="$normal_probe_bin:$PATH" \
  HK_INVOKE_BOOT_BIN="$normal_probe_bin/hk_usb_boot" \
  HK_INVOKE_MDNS_SECONDS=0 \
  HK_INVOKE_STATE_PROBE_TIMEOUT=1 \
  HK_INVOKE_SIM_ADB_LOG="$normal_probe_sim/adb.log" \
  zsh -f scripts/hk-invoke/normal_boot_probe.sh >$validate_tmp/hk-invoke-normal-probe-sim-default.out
if [[ -s "$normal_probe_sim/adb.log" ]]; then
  print -u2 'ERROR: normal_boot_probe.sh default path invoked adb in simulation'
  exit 1
fi
PATH="$normal_probe_bin:$PATH" \
  HK_INVOKE_BOOT_BIN="$normal_probe_bin/hk_usb_boot" \
  HK_INVOKE_MDNS_SECONDS=0 \
  HK_INVOKE_STATE_PROBE_TIMEOUT=1 \
  HK_INVOKE_SIM_ADB_LOG="$normal_probe_sim/adb.log" \
  HK_INVOKE_ADB_BIN="$normal_probe_bin/adb" \
  zsh -f scripts/hk-invoke/normal_boot_probe.sh --adb-devices >$validate_tmp/hk-invoke-normal-probe-sim-adb.out
rg -q '^adb-called$' "$normal_probe_sim/adb.log"
rm -rf "$normal_probe_sim"
trap - EXIT
trap 'rm -rf "$validate_tmp"' EXIT
print 'normal boot probe simulation passed'
scripts/hk-invoke/prepare_no_nand_probe.py --check >$validate_tmp/hk-invoke-no-nand-probe-check.out
scripts/hk-invoke/build_no_nand_initramfs.py --check >$validate_tmp/hk-invoke-no-nand-initramfs-check.out
scripts/hk-invoke/build_no_nand_initramfs.py --check --stage-ota83-connectivity >$validate_tmp/hk-invoke-no-nand-initramfs-ota83-check.out
scripts/hk-invoke/no_nand_post_boot_probe.py --check >$validate_tmp/hk-invoke-no-nand-post-boot-probe-check.out
scripts/hk-invoke/surface_watch.py --check >$validate_tmp/hk-invoke-surface-watch-check.out
scripts/hk-invoke/no_nand_readiness.py --check >$validate_tmp/hk-invoke-no-nand-readiness-check.out
scripts/hk-invoke/native_connectivity_packet.py --check >$validate_tmp/hk-invoke-native-connectivity-packet-check.out
python3 - $validate_tmp/hk-invoke-no-nand-readiness-check.out <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert j["classification"] == "host-observation-with-libusb-detect", j
assert j["sends_usb_payloads"] is False, j
assert j["opens_serial"] is False, j
assert j["runs_adb"] is False, j
assert j["writes_device_storage"] is False, j
print("no-NAND readiness check classification validation passed")
PY
python3 - $validate_tmp/hk-invoke-native-connectivity-packet-check.out <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert j["classification"] == "host-observation-with-libusb-detect", j
assert j["sends_usb_payloads"] is False, j
assert j["opens_serial"] is False, j
assert j["runs_adb"] is False, j
assert j["writes_device_storage"] is False, j
assert j["persistent_storage_changes_allowed"] is False, j
assert j["builds_artifacts_offline_only"] is True, j
assert j["escalation_packet_defined"] is True, j
gates = {gate["id"]: gate for gate in j["escalation_gates"]}
for required in (
    "serial_read_only_inventory",
    "audio_read_only_inventory",
    "network_read_only_inventory",
    "ota83_module_load",
    "usb_network",
    "wifi_named_profiles",
):
    assert required in gates, gates
assert gates["serial_read_only_inventory"]["approval_required"] is False, gates
assert "no_nand_serial_inventory.py" in gates["serial_read_only_inventory"]["host_command"], gates
for gated_id, env_name in (
    ("ota83_module_load", "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED"),
    ("usb_network", "HK_INVOKE_RAM_USBNET_APPROVED"),
    ("wifi_named_profiles", "HK_INVOKE_RAM_WIFI_APPROVED"),
):
    gate = gates[gated_id]
    assert gate["approval_required"] is True, gate
    assert gate["approval_env"] == env_name, gate
    assert gate["persistent_storage_changes_allowed"] is False, gate
print("native-connectivity packet static safety validation passed")
PY
python3 - $validate_tmp/hk-invoke-surface-watch-check.out <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert j["classification"] == "host-observation-with-libusb-detect"
assert j["polls_for"] == ["normal_bluetooth_or_audio", "marvell_service_loader"]
assert j["sends_usb_payloads"] is False
assert j["opens_serial"] is False
assert j["runs_adb"] is False
assert j["writes_device_storage"] is False
print("surface watch static safety validation passed")
PY
python3 - $validate_tmp/hk-invoke-no-nand-initramfs-ota83-check.out <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert j["ota83_stage_requested"] is True, j
assert j["ota83_stage_ready"] is True, j
assert j["ota83_stage"]["modules"]["mlan"], j
assert j["ota83_stage"]["modules"]["sd8xxx"], j
assert j["ota83_stage"]["modules"]["bt8xxx"], j
assert j["ota83_stage"]["firmware"]["wlan_fw"], j
assert j["ota83_stage"]["firmware"]["bt_fw"], j
assert j["ready"] is True, j
print("no-NAND OTA83 staging check validation passed")
PY
alloc_tmp=$(mktemp -d)
python3 - "$alloc_tmp" <<'PY'
import importlib.util
import sys
from pathlib import Path

path = Path("scripts/hk-invoke/build_no_nand_initramfs.py")
spec = importlib.util.spec_from_file_location("build_no_nand_initramfs", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)
out_root = Path(sys.argv[1])
first = module.create_artifact_dir(out_root, "20260619T000000Z")
second = module.create_artifact_dir(out_root, "20260619T000000Z")
assert first.name == "20260619T000000Z", first
assert second.name == "20260619T000000Z-1", second
print("no-NAND artifact directory collision validation passed")
PY
rm -rf "$alloc_tmp"
readiness_tmp=$(mktemp -d)
cat > "$readiness_tmp/normal-baseline.txt" <<'TXT'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==
address: aa-bb-cc-dd-ee-ff, connected: 1, name: "HK Invoke_C114D2"

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==
  HK Invoke_C114D2:
    Default Output Device: Yes

== Marvell service-loader USB check ==
no Invoke/Marvell WTP device currently visible
TXT
cat > "$readiness_tmp/service-loader.txt" <<'TXT'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==

== Marvell service-loader USB check ==
libusb devices matching 1286:8174
device ready class=ff subclass=ff proto=00 (iROM)
TXT
cat > "$readiness_tmp/no-surface.txt" <<'TXT'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==

== Marvell service-loader USB check ==
libusb devices matching 1286:8174
no Invoke/Marvell WTP device currently visible
TXT
python3 - "$readiness_tmp" <<'PY'
import json, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1])

def run(name):
    out = subprocess.check_output(
        [
            "scripts/hk-invoke/no_nand_readiness.py",
            "--state-file",
            str(root / f"{name}.txt"),
            "--json",
            "--skip-builder-check",
        ],
        text=True,
    )
    return json.loads(out)

normal = run("normal-baseline")
assert normal["decision"] == "normal_baseline_visible", normal
assert normal["custom_ram_boot_next"] is False, normal
assert normal["bluetooth_visible"] is True, normal
assert normal["audio_visible"] is True, normal
assert normal["marvell_service_loader_visible"] is False, normal

service = run("service-loader")
assert service["decision"] == "arm_ram_listener_allowed", service
assert service["custom_ram_boot_next"] is True, service
assert service["marvell_service_loader_visible"] is True, service
assert "make no-nand-initramfs" in "\n".join(service["recommended_commands"]), service
assert "ram_boot_console.sh --check-only" in "\n".join(service["recommended_commands"]), service

hold = run("no-surface")
assert hold["decision"] == "hold_no_custom_boot", hold
assert hold["custom_ram_boot_next"] is False, hold
assert hold["bluetooth_visible"] is False, hold
assert hold["audio_visible"] is False, hold
assert hold["marvell_service_loader_visible"] is False, hold
assert "make surface-watch" in "\n".join(hold["recommended_commands"]), hold

for result in (normal, service, hold):
    forbidden = ("saveenv", "l2nand", "tftp2nand", "nanderase", "nand write", "nand erase")
    rendered = json.dumps(
        {
            "reason": result.get("reason", ""),
            "recommended_commands": result.get("recommended_commands", []),
        }
    )
    assert not any(token in rendered for token in forbidden), result
print("no-NAND readiness fixture decision validation passed")
PY
rm -rf "$readiness_tmp"
packet_tmp=$(mktemp -d)
cat > "$packet_tmp/no-surface-state.txt" <<'EOF'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==

== Marvell service-loader USB check ==
libusb devices matching 1286:8174
no Invoke/Marvell WTP device currently visible

== ARP candidates matching Invoke/HK/Harman/Kardon/BT OUI ==
EOF
scripts/hk-invoke/native_connectivity_packet.py \
  --state-file "$packet_tmp/no-surface-state.txt" \
  --session-dir "$packet_tmp/native-packet" \
  --skip-artifact-build \
  --skip-host-fallback >$validate_tmp/hk-invoke-native-connectivity-packet-validation.out
python3 - "$packet_tmp/native-packet/summary.json" "$packet_tmp/native-packet/summary.md" <<'PY'
import json, sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
markdown = Path(sys.argv[2]).read_text()
assert summary["decision"] == "hold_no_custom_boot", summary
assert summary["custom_ram_boot_next"] is False, summary
assert summary["sends_usb_payloads"] is False, summary
assert summary["opens_serial"] is False, summary
assert summary["runs_adb"] is False, summary
assert summary["writes_device_storage"] is False, summary
assert summary["artifacts"]["plan_dir"], summary
assert summary["wifi_profiles"]["named_targets"] == ["YOURSSID", "YOURSSID", "yourssid"], summary
assert summary["wifi_profiles"]["credential_posture"] == "runtime_env_only", summary
assert "HK_INVOKE_RAM_WIFI_YOURSSID_PSK" in summary["wifi_profiles"]["runtime_psk_env"], summary
gates = {gate["id"]: gate for gate in summary["escalation_gates"]}
assert "serial_read_only_inventory" in gates, gates
assert "no_nand_serial_inventory.py" in gates["serial_read_only_inventory"]["host_command"], gates
assert gates["ota83_module_load"]["approval_env"] == "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED", gates
assert gates["usb_network"]["approval_env"] == "HK_INVOKE_RAM_USBNET_APPROVED", gates
assert gates["wifi_named_profiles"]["approval_env"] == "HK_INVOKE_RAM_WIFI_APPROVED", gates
assert "HK_INVOKE_RAM_WIFI_YOURSSID_PSK" in "\n".join(gates["wifi_named_profiles"]["runtime_secret_env"]), gates
assert all(gate["persistent_storage_changes_allowed"] is False for gate in gates.values()), gates
assert summary["surface_watch"]["decision"] == "hold_no_custom_boot", summary
assert summary["surface_watch"]["surface_seen"] is False, summary
assert summary["surface_watch"]["custom_ram_boot_next"] is False, summary
assert summary["surface_watch"]["samples_count"] == 1, summary
assert Path(summary["surface_watch"]["summary_json"]).exists(), summary
assert Path(summary["files"]["state"]).exists(), summary
assert Path(summary["files"]["readiness_json"]).exists(), summary
assert "make surface-watch" in "\n".join(summary["recommended_commands"]), summary
assert "Do not start a RAM listener" in markdown, markdown
assert "make surface-watch" in markdown, markdown
assert "YOURSSID" in markdown, markdown
assert "YOURSSID" in markdown, markdown
assert "yourssid" in markdown, markdown
assert "Surface watch" in markdown, markdown
assert "host assistant fallback" in markdown, markdown
assert "Escalation packet" in markdown, markdown
assert "serial_read_only_inventory" in markdown, markdown
assert "no_nand_serial_inventory.py" in markdown, markdown
assert "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED" in markdown, markdown
assert "HK_INVOKE_RAM_USBNET_APPROVED" in markdown, markdown
assert "HK_INVOKE_RAM_WIFI_APPROVED" in markdown, markdown
print("native-connectivity packet fixture validation passed")
PY
rm -rf "$packet_tmp"
watch_tmp=$(mktemp -d)
cat > "$watch_tmp/no-surface-state.txt" <<'EOF'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==

== Marvell service-loader USB check ==
libusb devices matching 1286:8174
no Invoke/Marvell WTP device currently visible

== ARP candidates matching Invoke/HK/Harman/Kardon/BT OUI ==
EOF
cat > "$watch_tmp/bluetooth-state.txt" <<'EOF'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==
address: aa-bb-cc-dd-ee-ff, connected: 1, name: "HK Invoke_C114D2"

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==
HK Invoke_C114D2:
  Default Output Device: Yes

== Marvell service-loader USB check ==
libusb devices matching 1286:8174
no Invoke/Marvell WTP device currently visible

== ARP candidates matching Invoke/HK/Harman/Kardon/BT OUI ==
EOF
cat > "$watch_tmp/marvell-state.txt" <<'EOF'
== Bluetooth connected matching Invoke/HK/Harman/Kardon ==

== macOS audio matching Invoke/HK/Harman/Kardon/Bluetooth ==

== Marvell service-loader USB check ==
libusb devices matching 1286:8174
device ready: BG2CD iROM class=ff subclass=ff

== ARP candidates matching Invoke/HK/Harman/Kardon/BT OUI ==
EOF
scripts/hk-invoke/surface_watch.py \
  --state-file "$watch_tmp/no-surface-state.txt" \
  --state-file "$watch_tmp/bluetooth-state.txt" \
  --session-dir "$watch_tmp/bluetooth-watch" \
  --interval 0 \
  --duration 0 \
  --json >$validate_tmp/hk-invoke-surface-watch-bluetooth.json
scripts/hk-invoke/surface_watch.py \
  --state-file "$watch_tmp/no-surface-state.txt" \
  --state-file "$watch_tmp/marvell-state.txt" \
  --session-dir "$watch_tmp/marvell-watch" \
  --interval 0 \
  --duration 0 \
  --json >$validate_tmp/hk-invoke-surface-watch-marvell.json
python3 - $validate_tmp/hk-invoke-surface-watch-bluetooth.json $validate_tmp/hk-invoke-surface-watch-marvell.json <<'PY'
import json, sys
from pathlib import Path

bluetooth = json.loads(Path(sys.argv[1]).read_text())
marvell = json.loads(Path(sys.argv[2]).read_text())
assert bluetooth["decision"] == "normal_baseline_visible", bluetooth
assert bluetooth["surface_seen"] is True, bluetooth
assert bluetooth["samples_count"] == 2, bluetooth
assert bluetooth["first_surface_sample"] == 2, bluetooth
assert bluetooth["custom_ram_boot_next"] is False, bluetooth
assert Path(bluetooth["summary_md"]).exists(), bluetooth
for sample in bluetooth["samples"]:
    assert Path(sample["state_file"]).exists(), sample
    assert Path(sample["readiness_file"]).exists(), sample
assert marvell["decision"] == "arm_ram_listener_allowed", marvell
assert marvell["surface_seen"] is True, marvell
assert marvell["samples_count"] == 2, marvell
assert marvell["first_surface_sample"] == 2, marvell
assert marvell["custom_ram_boot_next"] is True, marvell
for sample in marvell["samples"]:
    assert Path(sample["state_file"]).exists(), sample
    assert Path(sample["readiness_file"]).exists(), sample
for summary in (bluetooth, marvell):
    assert summary["sends_usb_payloads"] is False, summary
    assert summary["opens_serial"] is False, summary
    assert summary["runs_adb"] is False, summary
    assert summary["writes_device_storage"] is False, summary
no_surface = json.loads(Path(bluetooth["samples"][0]["readiness_file"]).read_text())
assert "make surface-watch" in "\n".join(no_surface["recommended_commands"]), no_surface
print("surface watch fixture validation passed")
PY
rm -rf "$watch_tmp"
plan_tmp=$(mktemp -d)
plan_artifact=$(scripts/hk-invoke/prepare_no_nand_probe.py --out-root "$plan_tmp")
test -f "$plan_artifact/PLAN.md"
test -f "$plan_artifact/init-no-nand-template.sh"
test -f "$plan_artifact/wifi-tmp-template.sh"
test -f "$plan_artifact/ram-module-load-template.sh"
test -f "$plan_artifact/next-no-nand-artifact.md"
test ! -f "$plan_artifact/uboot-ram-commands.txt"
python3 - "$plan_artifact" <<'PY'
import json, re, sys
from pathlib import Path

artifact = Path(sys.argv[1])
manifest = json.loads((artifact / "manifest.json").read_text())
plan = (artifact / "PLAN.md").read_text()
init_template = (artifact / "init-no-nand-template.sh").read_text()
wifi_template = (artifact / "wifi-tmp-template.sh").read_text()
module_template = (artifact / "ram-module-load-template.sh").read_text()
next_artifact = (artifact / "next-no-nand-artifact.md").read_text()

def active_lines(text):
    return [
        (idx, line.strip())
        for idx, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]

def assert_commands_after_guard(text, guard_token, exit_token, command_pattern):
    lines = active_lines(text)
    guard_lines = [idx for idx, line in lines if guard_token in line]
    exit_lines = [idx for idx, line in lines if exit_token in line]
    assert guard_lines, text
    assert exit_lines, text
    guard_end = max(exit_lines)
    command_lines = [
        (idx, line)
        for idx, line in lines
        if re.search(command_pattern, line)
    ]
    assert command_lines, text
    assert all(idx > guard_end for idx, _ in command_lines), command_lines

assert manifest["ready_for_no_nand_plan"] is True, manifest
assert manifest["ota83_evidence_ready"] is True, manifest
assert manifest["live_sdio_ids_seen"] == ["02DF:9135", "02DF:9136", "02DF:9137"], manifest
ota83 = manifest["ota83_evidence"]
assert ota83["exists"] is True, ota83
assert ota83["inspection_tool"], ota83
assert ota83["modules"]["sd8xxx"] is True, ota83
assert ota83["modules"]["bt8xxx"] is True, ota83
assert ota83["firmware"]["wlan_fw"] is True, ota83
assert ota83["audio_userspace"]["aplay"] is True, ota83
assert ota83["alsa_builtins"]["snd_soc_berlin"] is True, ota83
assert ota83["sdio_aliases"]["sd8xxx_02DF_9135"] is True, ota83
assert ota83["sdio_aliases"]["bt8xxx_02DF_9136"] is True, ota83

non_comment_init_lines = [
    line.strip()
    for line in init_template.splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
assert not any(re.search(r"(^|[;&| ])insmod([;&| ]|$)", line) for line in non_comment_init_lines), init_template
assert not any(re.search(r"(^|[;&| ])(wpa_supplicant|udhcpc|adbd-root)([;&| ]|$)", line) for line in non_comment_init_lines), init_template
assert "HK_INVOKE_RAM_WIFI_APPROVED" in wifi_template, wifi_template
assert "HK_INVOKE_RAM_WIFI_SSID" in wifi_template, wifi_template
assert "HK_INVOKE_RAM_WIFI_PSK" in wifi_template, wifi_template
assert "HK_INVOKE_RAM_WIFI_YOURSSID_PSK" in wifi_template, wifi_template
assert 'ssid="YOURSSID"' in wifi_template, wifi_template
assert 'ssid="YOURSSID"' in wifi_template, wifi_template
assert 'ssid="yourssid"' in wifi_template, wifi_template
assert "priority=20" in wifi_template, wifi_template
assert "priority=10" not in wifi_template, wifi_template
assert "TEST_SSID" not in wifi_template, wifi_template
assert "TEST_PSK" not in wifi_template, wifi_template
assert "HK_INVOKE_RAM_MODULE_LOAD_APPROVED" in module_template, module_template
assert_commands_after_guard(
    module_template,
    "HK_INVOKE_RAM_MODULE_LOAD_APPROVED",
    "exit 20",
    r"(^|[;&| ])insmod([;&| ]|$)",
)
assert_commands_after_guard(
    wifi_template,
    "HK_INVOKE_RAM_WIFI_APPROVED",
    "exit 21",
    r"(^|[;&| ])(wpa_supplicant|udhcpc)([;&| ]|$)",
)
assert "Only if Joe explicitly approves RAM-only Wi-Fi association" in plan, plan
assert "HK_INVOKE_RAM_WIFI_APPROVED=yes" in plan, plan
assert "HK_INVOKE_RAM_WIFI_YOURSSID_PSK=<psk>" in plan, plan
assert re.search(r"does\s+not emit its own U-Boot command file", plan), plan
assert "This file intentionally contains host-side commands only" in next_artifact, next_artifact
assert "/tmp/hk-invoke-ota2-work-current" not in next_artifact, next_artifact
print("no-NAND plan OTA83/read-only/approval validation passed")
PY
rm -rf "$plan_tmp"
python3 - $validate_tmp/hk-invoke-no-nand-post-boot-probe-check.out <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert "runs_libusb_detect" in j, j
expected = "host-observation-with-libusb-detect" if j["runs_libusb_detect"] else "host-observation"
assert j["classification"] == expected, j
assert j["adb_devices_probe"] is False, j
print("post-boot probe default host-observation validation passed")
PY
python3 - <<'PY'
import importlib.util
from pathlib import Path

path = Path("scripts/hk-invoke/no_nand_post_boot_probe.py")
spec = importlib.util.spec_from_file_location("no_nand_post_boot_probe", path)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

ordinary_host_ifconfig = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.0.2.10 netmask 0xffffff00 broadcast 192.0.2.255
bridge100: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
"""
assert mod.matching_lines(ordinary_host_ifconfig, mod.NET_PATTERN) == []
ordinary_sample = {
    "serial_devices": [],
    "usb_matches": [],
    "audio_matches": [],
    "network_matches": mod.matching_lines(ordinary_host_ifconfig, mod.NET_PATTERN),
    "marvell_visible": False,
    "adb": [],
}
assert not mod.has_surface(ordinary_sample), ordinary_sample

usb_network_ifconfig = "usb0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500\n"
usb_sample = dict(ordinary_sample)
usb_sample["network_matches"] = mod.matching_lines(usb_network_ifconfig, mod.NET_PATTERN)
assert mod.has_surface(usb_sample), usb_sample
print("post-boot probe network surface classification validation passed")
PY
if rg -n 'ram_boot_console\.sh[^\n]*/tmp/hk-invoke-ota2-work-current' README.md docs/operator-runbook.md docs/current-state.md; then
  print -u2 'ERROR: docs must not instruct no-NAND RAM boot against the stock image directory'
  print -u2 'Use make no-nand-initramfs and the generated <artifact>/image-dir instead.'
  exit 1
fi
if [[ -d scripts/host ]]; then
  python3 -m py_compile scripts/host/*.py
  if [[ -x scripts/host/host_realtime_assistant.py ]]; then
    python3 scripts/host/host_realtime_assistant.py --dry-run --skip-device-query >$validate_tmp/hk-invoke-host-realtime-dry-run.out
  fi
  python3 - <<'PY'
from pathlib import Path

makefile = Path("Makefile").read_text()
readme = Path("README.md").read_text()
assert "host-audio-fallback:" in makefile, makefile
assert "host-audio-devices:" in makefile, makefile
fallback_block = makefile.split("host-audio-fallback:", 1)[1].split("\n\n", 1)[0]
strict_block = makefile.split("host-audio-devices:", 1)[1].split("\n\n", 1)[0]
assert "--require-input \"MacBook\"" in fallback_block, fallback_block
assert "--require-output \"HK Invoke\"" not in fallback_block, fallback_block
assert "--require-output \"HK Invoke\"" in strict_block, strict_block
assert "make host-audio-fallback" in readme, readme
assert "Mac audio only" in readme, readme
print("host audio fallback target validation passed")
PY
fi

secret_hits=$(rg -l -i '(gh[pousr]_[A-Za-z0-9]{20,}|sk-(proj|live|svcacct|admin)-[A-Za-z0-9_-]{20,}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY)' . || true)
if [[ -n "$secret_hits" ]]; then
  print -u2 'ERROR: possible secret material found in these files:'
  print -u2 "$secret_hits"
  exit 1
fi

if [[ -f /tmp/hk-invoke-ota2-work-current/83_IMAGE ]]; then
  scripts/hk-invoke/parse_ota83.py /tmp/hk-invoke-ota2-work-current/83_IMAGE >$validate_tmp/hk-invoke-parse-validation.out
  tmp_json=$(mktemp)
  scripts/hk-invoke/parse_ota83.py --json /tmp/hk-invoke-ota2-work-current/83_IMAGE > "$tmp_json"
  python3 - "$tmp_json" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert j["entry_count"] == 9, j["entry_count"]
assert j["payload_start"] == 0x280, hex(j["payload_start"])
assert j["all_crc32_ok"] is True
assert any(e["name"] == "rootfs" and e["crc32_ok"] for e in j["entries"])
print("parse json validation passed")
PY
  rm "$tmp_json"
else
  print 'SKIP: /tmp/hk-invoke-ota2-work-current/83_IMAGE not present; parser fixture validation skipped'
fi
python3 - <<'PY'
from pathlib import Path

makefile = Path("Makefile").read_text()
readme = Path("README.md").read_text()
current = Path("docs/current-state.md").read_text()
setup = Path("docs/setup.md").read_text()
assert "no-nand-readiness:" in makefile, makefile
assert "scripts/hk-invoke/no_nand_readiness.py" in makefile, makefile
assert "native-connectivity-packet:" in makefile, makefile
assert "scripts/hk-invoke/native_connectivity_packet.py" in makefile, makefile
assert "no-nand-initramfs-ota83:" in makefile, makefile
assert "--stage-ota83-connectivity" in makefile, makefile
assert "make no-nand-readiness" in readme, readme
assert "make native-connectivity-packet" in readme, readme
assert "make no-nand-initramfs-ota83" in readme, readme
assert "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED" in readme, readme
assert "hold_no_custom_boot" in current, current
for doc_name, text in {
    "README.md": readme,
    "docs/setup.md": setup,
    "docs/current-state.md": current,
}.items():
    assert "/Users/joe/.local/share/chezmoi.worktrees/hk-invoke-voice-satellite" not in text, doc_name
assert "/Users/joe/.local/share/chezmoi/projects/hk-invoke-voice-satellite" in readme, readme
assert "/Users/joe/.local/share/chezmoi/projects/hk-invoke-voice-satellite" in setup, setup
print("no-NAND readiness target/docs validation passed")
PY

if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists libusb-1.0; then
  boot_bin=$(scripts/hk-invoke/build_native_boot.sh)
  print "$boot_bin" >$validate_tmp/hk-invoke-build-validation.out
  "$boot_bin" safety-self-test >$validate_tmp/hk-invoke-safety-self-test.out
  push_tmp=$(mktemp -d)
  print 'printenv' > "$push_tmp/79_IMAGE"
  if "$boot_bin" push-file "$push_tmp/79_IMAGE" >$validate_tmp/hk-invoke-push-file-79-validation.out 2>&1; then
    print -u2 'ERROR: native push-file allowed 79_IMAGE command script'
    exit 1
  fi
  if ! rg -q 'refusing 79_IMAGE' $validate_tmp/hk-invoke-push-file-79-validation.out; then
    print -u2 'ERROR: native push-file 79_IMAGE refusal was not explicit'
    exit 1
  fi
  print 'dummy' > "$push_tmp/random.bin"
  if "$boot_bin" push-file "$push_tmp/random.bin" >$validate_tmp/hk-invoke-push-file-random-validation.out 2>&1; then
    print -u2 'ERROR: native push-file allowed non-81/82 payload name'
    exit 1
  fi
  if ! rg -q 'only 81_IMAGE\* or 82_IMAGE\* RAM payloads are allowed' $validate_tmp/hk-invoke-push-file-random-validation.out; then
    print -u2 'ERROR: native push-file non-81/82 refusal was not explicit'
    exit 1
  fi
else
  print 'SKIP: libusb-1.0/pkg-config not present; native boot build skipped'
fi

clean_tmp=$(mktemp -d)
tmp_dir=$(mktemp -d)
builder_tmp=''
trap 'rm -rf "$tmp_dir" "$clean_tmp" "$validate_tmp"; if [[ -n "$builder_tmp" ]]; then rm -rf "$builder_tmp"; fi' EXIT

print 'bootcmd=run ramboot' > "$clean_tmp/79_IMAGE"
: > "$clean_tmp/81_IMAGE"
: > "$clean_tmp/82_IMAGE"
scripts/hk-invoke/ram_boot_console.sh --check-only "$clean_tmp" >$validate_tmp/hk-invoke-clean-check-validation.out
if [[ -n "${boot_bin:-}" ]]; then
  "$boot_bin" check-images "$clean_tmp" >$validate_tmp/hk-invoke-clean-native-check-validation.out
fi

print 'l2nand 83' > "$tmp_dir/79_IMAGE"
: > "$tmp_dir/81_IMAGE"
: > "$tmp_dir/82_IMAGE"
if scripts/hk-invoke/ram_boot_console.sh --check-only "$tmp_dir" >$validate_tmp/hk-invoke-risky79-validation.out 2>&1; then
  print -u2 'ERROR: RAM boot guard allowed a NAND-writing 79_IMAGE'
  exit 1
fi
if [[ -n "${boot_bin:-}" ]] && "$boot_bin" check-images "$tmp_dir" >$validate_tmp/hk-invoke-risky79-native-validation.out 2>&1; then
  print -u2 'ERROR: native image safety guard allowed a NAND-writing 79_IMAGE'
  exit 1
fi

builder_tmp=$(mktemp -d)
artifact=$(scripts/hk-invoke/build_no_nand_initramfs.py --out-root "$builder_tmp")
ota83_artifact=$(scripts/hk-invoke/build_no_nand_initramfs.py --stage-ota83-connectivity --out-root "$builder_tmp")
/bin/sh -n "$artifact/rootfs-work/init"
/bin/sh -n "$artifact/rootfs-work/no-nand-inventory.sh"
/bin/sh -n "$artifact/rootfs-work/no-nand-audio-inventory.sh"
/bin/sh -n "$artifact/rootfs-work/no-nand-network-inventory.sh"
/bin/sh -n "$artifact/rootfs-work/no-nand-wifi.sh"
/bin/sh -n "$artifact/rootfs-work/no-nand-usbnet.sh"
/bin/sh -n "$ota83_artifact/rootfs-work/no-nand-ota83-module-load.sh"
scripts/hk-invoke/ram_boot_console.sh --check-only "$artifact/image-dir" >$validate_tmp/hk-invoke-no-nand-artifact-check-validation.out
scripts/hk-invoke/ram_boot_console.sh --check-only "$ota83_artifact/image-dir" >$validate_tmp/hk-invoke-no-nand-ota83-artifact-check-validation.out
scripts/hk-invoke/no_nand_post_boot_probe.py --check --artifact "$artifact" >$validate_tmp/hk-invoke-generated-post-boot-probe-check-validation.out
scripts/hk-invoke/no_nand_serial_inventory.py --check >$validate_tmp/hk-invoke-serial-inventory-check-validation.out
scripts/hk-invoke/no_nand_serial_inventory.py --print-commands >$validate_tmp/hk-invoke-serial-inventory-commands-validation.out
python3 - $validate_tmp/hk-invoke-generated-post-boot-probe-check-validation.out "$artifact" "$ota83_artifact" $validate_tmp/hk-invoke-serial-inventory-check-validation.out $validate_tmp/hk-invoke-serial-inventory-commands-validation.out <<'PY'
import json, re, sys
from pathlib import Path

with open(sys.argv[1]) as fh:
    j = json.load(fh)
assert j["adb_devices_probe"] is False, j
assert j["artifact"]["image_dir_exists"] is True, j
assert j["artifact"]["uboot_commands_exists"] is True, j
artifact = Path(sys.argv[2])
ota83_artifact = Path(sys.argv[3])
serial_check = json.loads(Path(sys.argv[4]).read_text())
serial_commands = json.loads(Path(sys.argv[5]).read_text())
init_script = (artifact / "rootfs-work" / "init").read_text()
hardware_helper = (artifact / "rootfs-work" / "no-nand-inventory.sh").read_text()
audio_helper = (artifact / "rootfs-work" / "no-nand-audio-inventory.sh").read_text()
network_helper = (artifact / "rootfs-work" / "no-nand-network-inventory.sh").read_text()
wifi_helper = (artifact / "rootfs-work" / "no-nand-wifi.sh").read_text()
usbnet_helper = (artifact / "rootfs-work" / "no-nand-usbnet.sh").read_text()
ota83_helper = (ota83_artifact / "rootfs-work" / "no-nand-ota83-module-load.sh").read_text()
ota83_manifest = json.loads((ota83_artifact / "manifest.json").read_text())
assert serial_check["classification"] == "deliberate-serial-open-read-only-inventory", serial_check
assert serial_check["opens_serial"] is True, serial_check
for field in (
    "sends_usb_boot_payloads",
    "runs_adb",
    "loads_modules",
    "starts_daemons",
    "wifi_or_network_changes",
    "writes_device_storage",
    "persistent_storage_changes_allowed",
):
    assert serial_check[field] is False, (field, serial_check)
assert isinstance(serial_commands, list) and serial_commands, serial_commands
serial_joined = "\n".join(serial_commands)
assert "cat /tmp/NO_NAND_PROBE_READY" in serial_joined, serial_commands
assert "sh /no-nand-inventory.sh" in serial_joined, serial_commands
assert "sh /no-nand-audio-inventory.sh" in serial_joined, serial_commands
assert "sh /no-nand-network-inventory.sh" in serial_joined, serial_commands
for forbidden in (
    "adb ",
    "fastboot",
    "insmod",
    "modprobe",
    "wpa_supplicant",
    "wpa_cli",
    "udhcpc",
    "ifconfig mlan0",
    "ifconfig usb0",
    "route add",
    "saveenv",
    "l2nand",
    "tftp2nand",
    "nanderase",
    "nand write",
    "nand erase",
):
    assert forbidden not in serial_joined, (forbidden, serial_commands)
assert "sh /no-nand-audio-inventory.sh" in init_script, init_script
assert "sh /no-nand-network-inventory.sh" in init_script, init_script
assert "/tmp/no-nand-audio-inventory.txt" in audio_helper, audio_helper
assert "/tmp/no-nand-network-inventory.txt" in network_helper, network_helper
assert "cat /proc/asound/cards" in audio_helper, audio_helper
assert "find /sys/class/sound" in audio_helper, audio_helper
assert "cat /proc/net/dev" in network_helper, network_helper
assert "ifconfig -a" in network_helper, network_helper
assert "route -n" in network_helper, network_helper
assert "cat /proc/net/route" in network_helper, network_helper
assert "find /sys/class/net" in network_helper, network_helper
assert "find /sys/bus/sdio/devices" in network_helper, network_helper
assert "find /sys/class/android_usb" in network_helper, network_helper
for helper_name, helper in (
    ("hardware", hardware_helper),
    ("audio", audio_helper),
    ("network", network_helper),
):
    assert "bb=/bin/busybox" in helper, (helper_name, helper)
    assert '$bb sh -c "$*"' in helper, (helper_name, helper)
    for bare_run in (
        r'run "date',
        r'run "uname',
        r'run "cat ',
        r'run "ls ',
        r'run "mount',
        r'run "find ',
        r'run "ifconfig',
        r'run "route',
        r'run "ps',
        r'run "dmesg',
    ):
        assert re.search(bare_run, helper) is None, (helper_name, bare_run, helper)
    for bare_pipe in ("| grep", "| sort", "| tail", "| head"):
        assert bare_pipe not in helper, (helper_name, bare_pipe, helper)
for forbidden in (
    "insmod",
    "modprobe",
    "udhcpc",
    "wpa_supplicant",
    "wpa_cli",
    "iwconfig",
    "route add",
    "ifconfig usb0",
    "ifconfig mlan0",
    "wifi_conf.sh",
    "wifi_join.sh",
    "reboot_usb.sh",
):
    assert forbidden not in network_helper, (forbidden, network_helper)
for forbidden in (
    "insmod",
    "modprobe",
    "run_amp",
    "start_amp",
    "wifi_conf.sh",
    "wifi_join.sh",
    "reboot_usb.sh",
):
    assert forbidden not in audio_helper, (forbidden, audio_helper)
assert "HK_INVOKE_RAM_WIFI_APPROVED" in wifi_helper, wifi_helper
assert "HK_INVOKE_RAM_USBNET_APPROVED" in usbnet_helper, usbnet_helper
assert "HK_INVOKE_RAM_WIFI_APPROVED=yes HK_INVOKE_RAM_WIFI_SSID=<ssid> HK_INVOKE_RAM_WIFI_PSK=<psk> /no-nand-wifi.sh" in init_script, init_script
assert "HK_INVOKE_RAM_WIFI_APPROVED=yes HK_INVOKE_RAM_WIFI_YOURSSID_PSK=<psk> /no-nand-wifi.sh" in init_script, init_script
assert "HK_INVOKE_RAM_USBNET_APPROVED=yes /no-nand-usbnet.sh" in init_script, init_script
assert "HK_INVOKE_RAM_WIFI_SSID" in wifi_helper, wifi_helper
assert "HK_INVOKE_RAM_WIFI_PSK" in wifi_helper, wifi_helper
assert "HK_INVOKE_RAM_WIFI_YOURSSID_PSK" in wifi_helper, wifi_helper
assert 'ssid="YOURSSID"' in wifi_helper, wifi_helper
assert 'ssid="YOURSSID"' in wifi_helper, wifi_helper
assert 'ssid="yourssid"' in wifi_helper, wifi_helper
assert "priority=22" in wifi_helper, wifi_helper
assert "priority=20" in wifi_helper, wifi_helper
assert "priority=19" in wifi_helper, wifi_helper
assert "priority=10" not in wifi_helper, wifi_helper
assert "TEST_SSID" not in wifi_helper, wifi_helper
assert "TEST_PSK" not in wifi_helper, wifi_helper
assert "bb=/bin/busybox" in wifi_helper, wifi_helper
assert "/lib/modules/linux_ver" not in wifi_helper, wifi_helper
assert 'linux_ver=$($bb uname -r)' in wifi_helper, wifi_helper
assert "/lib/modules/$linux_ver/kernel/arch/arm/mach-berlin/modules/wlan_sd8801/88mlan.ko" in wifi_helper, wifi_helper
assert "sd8887_uapsta.bin" in wifi_helper, wifi_helper
assert "'^mlan0:'" not in wifi_helper, wifi_helper
assert "/sys/class/net/mlan0" in wifi_helper, wifi_helper
for bare_tool in (
    "mkdir",
    "cat",
    "ln",
    "uname",
    "grep",
    "rmmod",
    "insmod",
    "sleep",
    "ifconfig",
    "route",
):
    for idx, line in (
        (idx, line.strip())
        for idx, line in enumerate(wifi_helper.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ):
        assert not re.search(rf"(^|[;&|]\s*){bare_tool}\b", line), (
            bare_tool,
            idx,
            line,
            wifi_helper,
        )
for forbidden in (
    "/home/galois",
    "/home/galois_rwdata",
    "wifi_join.sh",
    "wifi_conf.sh",
    "save_config",
    "saveenv",
    "l2nand",
    "tftp2nand",
    "nand write",
    "nand erase",
):
    for idx, line in (
        (idx, line.strip())
        for idx, line in enumerate(wifi_helper.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ):
        assert forbidden not in line, (forbidden, idx, line, wifi_helper)
assert ota83_manifest["ota83_stage_requested"] is True, ota83_manifest
assert ota83_manifest["ota83_stage_ready"] is True, ota83_manifest
for rel in (
    "ota83-stage/lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/wlan_sd8887/mlan.ko",
    "ota83-stage/lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/wlan_sd8887/sd8xxx.ko",
    "ota83-stage/lib/modules/3.8.13-yocto-standard/kernel/arch/arm/mach-berlin/modules/bt_sd8887/bt8xxx.ko",
    "ota83-stage/lib/firmware/mrvl/sd8887_wlan_a2_p78.bin",
    "ota83-stage/lib/firmware/mrvl/sd8887_bt_a2.bin",
    "ota83-stage/etc/asound-product.conf",
    "ota83-stage/usr/bin/aplay",
):
    assert (ota83_artifact / "rootfs-work" / rel).exists(), rel
assert "HK_INVOKE_RAM_OTA83_MODULE_LOAD_APPROVED" in ota83_helper, ota83_helper
assert "mlan.ko" in ota83_helper, ota83_helper
assert "sd8xxx.ko" in ota83_helper, ota83_helper
assert "bt8xxx.ko" in ota83_helper, ota83_helper
assert "wlan_sd8887/mlan.ko" not in ota83_helper, ota83_helper
assert "wlan_sd8887/sd8xxx.ko" not in ota83_helper, ota83_helper
assert "bt_sd8887/bt8xxx.ko" not in ota83_helper, ota83_helper
assert "wlan_sd8801/88mlan.ko" in ota83_helper, ota83_helper
assert "wlan_sd8801/sd8801.ko" in ota83_helper, ota83_helper
assert "/sys/class/net/mlan0" in ota83_helper, ota83_helper
assert "wpa_supplicant" not in ota83_helper, ota83_helper
assert "udhcpc" not in ota83_helper, ota83_helper
assert "wifi_join.sh" not in ota83_helper, ota83_helper
assert "bb=/bin/busybox" in ota83_helper, ota83_helper
assert "sd8887_uapsta.bin" in ota83_helper, ota83_helper
for bare_tool in (
    "uname",
    "cat",
    "find",
    "insmod",
    "dmesg",
    "tail",
):
    for idx, line in (
        (idx, line.strip())
        for idx, line in enumerate(ota83_helper.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ):
        assert not re.search(rf"(^|[;&|]\s*){bare_tool}\b", line), (
            bare_tool,
            idx,
            line,
            ota83_helper,
        )
active_init_lines = [
    (idx, line.strip())
    for idx, line in enumerate(init_script.split("cat > /tmp/NO_NAND_PROBE_READY", 1)[0].splitlines(), start=1)
    if line.strip() and not line.lstrip().startswith("#")
]
assert not any("/tmp/wifi" in line for _, line in active_init_lines), init_script
assert not any("/no-nand-ota83-module-load.sh" in line for _, line in active_init_lines), init_script

def script_active_lines(text):
    return [
        (idx, line.strip())
        for idx, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]

wifi_active = script_active_lines(wifi_helper)
guard_exits = [
    idx for idx, line in wifi_active if "exit 21" in line or "exit 23" in line
]
assert guard_exits, wifi_helper
guard_end = max(guard_exits)
wifi_command_lines = [
    (idx, line)
    for idx, line in wifi_active
    if (
        "/tmp/wifi" in line
        or "/lib/firmware/mrvl" in line
        or re.search(r"(^|[;&| ])(ifconfig|insmod|route|udhcpc|wpa_cli|wpa_supplicant)([;&| ]|$)", line)
        or re.search(r"(^|[;&| ])(ln|mkdir|rmmod)([;&| ]|$)", line)
    )
]
assert wifi_command_lines, wifi_helper
assert all(idx > guard_end for idx, _ in wifi_command_lines), wifi_command_lines
usbnet_active = script_active_lines(usbnet_helper)
usbnet_guard_exits = [idx for idx, line in usbnet_active if "exit 22" in line]
assert usbnet_guard_exits, usbnet_helper
usbnet_guard_end = max(usbnet_guard_exits)
usbnet_command_lines = [
    (idx, line)
    for idx, line in usbnet_active
    if (
        "/sys/class/android_usb" in line
        or "/sys/class/net" in line
        or "gadget" in line
        or "android_usb" in line
        or "rndis" in line
        or "ecm" in line
        or "ncm" in line
        or re.search(r"(^|[;&| ])(ifconfig|route|udhcpc)([;&| ]|$)", line)
    )
]
assert usbnet_command_lines, usbnet_helper
assert all(idx > usbnet_guard_end for idx, _ in usbnet_command_lines), usbnet_command_lines
ota83_active = script_active_lines(ota83_helper)
ota83_guard_exits = [idx for idx, line in ota83_active if "exit 24" in line]
assert ota83_guard_exits, ota83_helper
ota83_guard_end = max(ota83_guard_exits)
ota83_command_lines = [
    (idx, line)
    for idx, line in ota83_active
    if re.search(r"(^|[;&| ])(insmod|ln|mkdir)([;&| ]|$)", line)
]
assert ota83_command_lines, ota83_helper
assert all(idx > ota83_guard_end for idx, _ in ota83_command_lines), ota83_command_lines
print("generated post-boot probe host-observation/artifact validation passed")
PY

rootfs_dir=$HOME/.local/state/hk-invoke/recovery-baselines/20260619T012008/extracted/rootfs-82
if [[ -d "$rootfs_dir" ]] && scripts/hk-invoke/build_no_nand_initramfs.py --check --out-root "$rootfs_dir/tmp-out" >$validate_tmp/hk-invoke-bad-out-root-validation.out; then
  print -u2 'ERROR: no-NAND builder allowed an output root inside the source rootfs'
  exit 1
fi
if scripts/hk-invoke/build_no_nand_initramfs.py --check --out-root /tmp >$validate_tmp/hk-invoke-bad-out-root-tmp-validation.out; then
  print -u2 'ERROR: no-NAND builder allowed an output root that contains the base image directory'
  exit 1
fi

print 'validation passed'
