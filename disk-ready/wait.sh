#!/usr/bin/env bash
# Wait until a boat ci-base VM's workspace disk is the real one (t23520).
# Same test as boat-job's (t10986): /var/lib/ascii-lazy absent, or hydration-done present
# and no "active" marker, and the workspace filesystem is not fuse.
# A no-op where /var/lib/ascii-lazy does not exist (GitHub-hosted, Blacksmith).
# Env: ASCII_LAZY_DIR (default /var/lib/ascii-lazy), DISK_READY_PATH (default $GITHUB_WORKSPACE or $HOME),
#      DISK_READY_TIMEOUT seconds (default 180), DISK_READY_INTERVAL seconds (default 0.5).
set -euo pipefail
dir="${ASCII_LAZY_DIR:-/var/lib/ascii-lazy}"
path="${DISK_READY_PATH:-${GITHUB_WORKSPACE:-$HOME}}"
timeout="${DISK_READY_TIMEOUT:-180}"
interval="${DISK_READY_INTERVAL:-0.5}"

if [ ! -d "$dir" ]; then
  echo "disk-ready: no $dir, nothing to wait for"
  exit 0
fi

ready() {
  [ -e "$dir/hydration-done" ] || return 1
  [ ! -e "$dir/active" ] || return 1
  local fstype
  fstype="$(findmnt -n -o FSTYPE -T "$path" 2>/dev/null || true)"
  [[ "$fstype" != fuse* ]]
}

start=$SECONDS
while ! ready; do
  if [ $((SECONDS - start)) -ge "$timeout" ]; then
    echo "::error::disk-ready: workspace disk not hydrated after ${timeout}s" >&2
    exit 1
  fi
  sleep "$interval"
done
echo "disk-ready: real disk after $((SECONDS - start))s (fs $(findmnt -n -o FSTYPE -T "$path" 2>/dev/null || echo unknown), inode of $path $(stat -c %i "$path"))"
