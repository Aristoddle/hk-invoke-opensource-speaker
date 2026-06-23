.PHONY: setup-check validate state normal-probe wifi-ip-probe surface-watch host-audio-devices host-audio-fallback host-realtime-dry-run no-nand-readiness native-connectivity-packet no-nand-plan no-nand-initramfs no-nand-initramfs-ota83 no-nand-post-boot-probe no-nand-serial-inventory parse-ota83 analyze-stockroot-bootimgs golden-nand-dump-plan roadmap

setup-check:
	zsh scripts/setup_check.sh

validate:
	zsh scripts/validate.sh

state:
	zsh scripts/hk-invoke/hk_invoke_state.sh

normal-probe:
	zsh scripts/hk-invoke/normal_boot_probe.sh

wifi-ip-probe:
	@python3 scripts/hk-invoke/wifi_ip_probe.py --ip "$${IP:?set IP=192.168.x.y}"

surface-watch:
	python3 scripts/hk-invoke/surface_watch.py

host-audio-devices:
	python3 scripts/host/list_audio_devices.py --require-input "MacBook" --require-output "HK Invoke"

host-audio-fallback:
	python3 scripts/host/list_audio_devices.py --require-input "MacBook"

host-realtime-dry-run:
	python3 scripts/host/host_realtime_assistant.py --dry-run --skip-device-query

no-nand-readiness:
	python3 scripts/hk-invoke/no_nand_readiness.py

native-connectivity-packet:
	python3 scripts/hk-invoke/native_connectivity_packet.py

no-nand-plan:
	python3 scripts/hk-invoke/prepare_no_nand_probe.py

no-nand-initramfs:
	python3 scripts/hk-invoke/build_no_nand_initramfs.py

no-nand-initramfs-ota83:
	python3 scripts/hk-invoke/build_no_nand_initramfs.py --stage-ota83-connectivity

no-nand-post-boot-probe:
	python3 scripts/hk-invoke/no_nand_post_boot_probe.py

no-nand-serial-inventory:
	python3 scripts/hk-invoke/no_nand_serial_inventory.py --port "$${PORT:-/dev/cu.usbmodemno_nand_probe_1}"

parse-ota83:
	python3 scripts/hk-invoke/parse_ota83.py /tmp/hk-invoke-ota2-work-current/83_IMAGE

analyze-stockroot-bootimgs:
	python3 scripts/hk-invoke/analyze_stockroot_bootimgs.py /tmp/hk-invoke-stockroot-83/83_IMAGE

golden-nand-dump-plan:
	python3 scripts/hk-invoke/golden_nand_dump_plan.py

roadmap:
	@bat --paging=never docs/roadmap.md 2>/dev/null || python3 -c "print(open('docs/roadmap.md').read())"
