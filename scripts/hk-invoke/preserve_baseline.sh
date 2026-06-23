#!/usr/bin/env zsh
set -euo pipefail

stamp=${1:-$(date +%Y%m%dT%H%M%S)}
base=${HK_INVOKE_STATE_DIR:-$HOME/.local/state/hk-invoke/recovery-baselines}
dest="$base/$stamp"

if [[ -e "$dest" ]]; then
  print -u2 "ERROR: destination already exists: $dest"
  exit 2
fi
mkdir -p "$dest"

copy_if_exists() {
  local src=$1
  local target=$2
  if [[ -e "$src" ]]; then
    mkdir -p "${target:h}"
    ditto "$src" "$target"
    print "copied $src -> $target"
  else
    print "missing $src" >> "$dest/MISSING.txt"
  fi
}

copy_if_exists /tmp/hk-invoke-ota2.zip "$dest/ota2/hk-invoke-ota2.zip"
copy_if_exists /tmp/hk-invoke-ota2-full/OTA2 "$dest/ota2/OTA2-full"
copy_if_exists /tmp/hk-invoke-native "$dest/native"
copy_if_exists /tmp/hk-invoke-82-image.decompressed "$dest/extracted/hk-invoke-82-image.decompressed"
copy_if_exists /tmp/hk-invoke-rootfs-82 "$dest/extracted/rootfs-82"

{
  print '# Harman Kardon Invoke recovery baseline manifest'
  print
  print "created_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  print "host: $(hostname)"
  print "dest: $dest"
  print
  print '## live state at preservation time'
  if [[ -x scripts/hk-invoke/hk_invoke_state.sh ]]; then
    scripts/hk-invoke/hk_invoke_state.sh || true
  else
    print 'state script unavailable'
  fi
} > "$dest/manifest.md"

{
  print '# SHA-256 checksums'
  print
  fd -a . "$dest" -t f \
    -E SHA256SUMS.md \
    -E file-inventory.txt \
    -E manifest.md \
    -E MISSING.txt \
    | sort \
    | while read -r f; do shasum -a 256 "$f"; done
} > "$dest/SHA256SUMS.md"

fd -a . "$dest" -t f | sort > "$dest/file-inventory.txt"

print "$dest"
