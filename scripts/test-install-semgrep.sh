#!/usr/bin/env bash
# install-semgrep.sh: each install attempt is bounded and retried, so a stalled download
# ends in seconds (19 Oct 2026, automancer-ops run 39334640234 hung 9m23s).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/install-semgrep.sh"
t="$(mktemp -d)"; trap 'rm -rf "$t"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# The real installer path gets the pinned version (a stub uv on PATH records its arguments).
mkdir -p "$t/bin"
printf '#!/bin/sh\necho "$@" >"%s/uv.args"\n' "$t" >"$t/bin/uv"; chmod +x "$t/bin/uv"
PATH="$t/bin:$PATH" "$SCRIPT" 9.9.9 >/dev/null 2>&1 || fail "stub uv install failed"
grep -q "semgrep==9.9.9" "$t/uv.args" || fail "version not passed to the installer: $(cat "$t/uv.args")"

# A working install succeeds on the first attempt.
SEMGREP_INSTALL_COMMAND='true' "$SCRIPT" 9.9.9 >"$t/ok" 2>&1 || fail "a good install was refused"
grep -q "attempt 1)" "$t/ok" || fail "success not reported"

# A flaky install that fails once then works is retried.
SEMGREP_INSTALL_COMMAND="[ -e $t/flag ] || { touch $t/flag; exit 1; }" "$SCRIPT" 9.9.9 >"$t/flaky" 2>&1 || fail "a retry did not recover"
grep -q "attempt 2)" "$t/flaky" || fail "flaky install not recovered on attempt 2"

# An installer that hangs (the 19 Oct shape, ignoring TERM) ends in seconds, with a clear message.
start=$SECONDS
if SEMGREP_INSTALL_COMMAND='trap "" TERM; sleep 120' SEMGREP_INSTALL_ATTEMPTS=2 SEMGREP_INSTALL_TIMEOUT=2 \
    "$SCRIPT" 9.9.9 >"$t/stall" 2>&1; then
  fail "a stalled install was reported as success"
fi
elapsed=$((SECONDS - start))
grep -q "attempt 2/2 failed or timed out after 2s" "$t/stall" || fail "stall not bounded and retried: $(cat "$t/stall")"
grep -q "could not install semgrep 9.9.9 after 2 attempts" "$t/stall" || fail "failure not reported"
[ "$elapsed" -lt 30 ] || fail "a stalled install took ${elapsed}s"
echo "ok: install bounded per attempt, retried, a stall fails in ${elapsed}s"
