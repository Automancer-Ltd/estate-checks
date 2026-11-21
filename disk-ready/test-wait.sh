#!/usr/bin/env bash
# Tests for wait.sh: no-op without the directory, waits for the marker, times out.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

ASCII_LAZY_DIR="$tmp/absent" DISK_READY_PATH="$tmp" bash "$here/wait.sh" | grep -q "nothing to wait" || fail "no-op"

mkdir "$tmp/lazy"
( sleep 2; touch "$tmp/lazy/hydration-done" ) &
bg=$!
s=$SECONDS
ASCII_LAZY_DIR="$tmp/lazy" DISK_READY_PATH="$tmp" DISK_READY_INTERVAL=0.2 bash "$here/wait.sh" >/dev/null || fail "should pass once marker lands"
wait "$bg"
[ $((SECONDS - s)) -ge 1 ] || fail "returned before the marker"

touch "$tmp/lazy/active"
if ASCII_LAZY_DIR="$tmp/lazy" DISK_READY_PATH="$tmp" DISK_READY_TIMEOUT=1 DISK_READY_INTERVAL=0.2 bash "$here/wait.sh" 2>/dev/null; then fail "active marker must block"; fi
rm "$tmp/lazy/active"
echo ok
