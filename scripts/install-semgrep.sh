#!/usr/bin/env bash
# Install the pinned Semgrep, bounded, so a stalled download cannot eat the job.
#
# On 19 Oct 2026 automancer-ops run 39334640234 hung 9m23s in "Set up Semgrep" (normally ~10s)
# until the job hit its 10-minute cap. A transfer that stalls without failing is not an error
# until the kernel gives up minutes later, so each attempt runs under timeout(1) and is retried.
#
# Usage: install-semgrep.sh <version>
# Env: SEMGREP_INSTALL_ATTEMPTS (3), SEMGREP_INSTALL_TIMEOUT seconds per attempt (120).
set -euo pipefail

version="${1:?usage: install-semgrep.sh <version>}"
attempts="${SEMGREP_INSTALL_ATTEMPTS:-3}"
attempt_seconds="${SEMGREP_INSTALL_TIMEOUT:-120}"

# The attempt runs in a child bash, so it needs the version in its environment.
export version
export UV_HTTP_TIMEOUT=30
export PIP_DEFAULT_TIMEOUT=15 PIP_RETRIES=1

install_once() {
  if command -v uv >/dev/null 2>&1; then
    uv tool install --force "semgrep==$version"
  elif command -v pipx >/dev/null 2>&1; then
    pipx install --force "semgrep==$version"
  elif command -v pip3 >/dev/null 2>&1; then
    pip3 install --break-system-packages "semgrep==$version"
  else
    python3 -m pip install --break-system-packages "semgrep==$version"
  fi
}

attempt=1
while [ "$attempt" -le "$attempts" ]; do
  # -k: if the installer ignores TERM, KILL it shortly after.
  if timeout -k 5 "$attempt_seconds" bash -c "$(declare -f install_once); install_once"; then
    echo "install-semgrep: semgrep ${version} installed (attempt ${attempt})"
    exit 0
  fi
  echo "install-semgrep: attempt ${attempt}/${attempts} failed or timed out after ${attempt_seconds}s" >&2
  attempt=$((attempt + 1))
  [ "$attempt" -gt "$attempts" ] || sleep 2
done

echo "install-semgrep: could not install semgrep ${version} after ${attempts} attempts of ${attempt_seconds}s" >&2
exit 1
