#!/usr/bin/env bash
# Fetch the pinned gitleaks release in bounded, retried attempts.
#
# A download that stops moving prints nothing and never errors. On 19 Oct 2026 that step
# in gdp-maia (run 39339817582) stalled at 3.7 MB of 8.2 MB: curl reached its --max-time,
# tar read the truncated archive, and Check went red on a main SHA whose release then
# stopped. One attempt cannot tell a slow mirror from a broken one, and a step with no
# per-attempt bound can hold the job until the job timeout kills it.
#
# So each attempt is bounded, the loop retries, and the archive is checked against the
# pinned digest inside the loop, so a truncation retries instead of failing the job.
# A warm fetch takes about a second.
#
# Usage: fetch-gitleaks.sh [version] [dest-dir]
#   version   default 8.30.1 (GITLEAKS_VERSION overrides it)
#   dest-dir  default $RUNNER_TEMP, else /tmp; the binary lands at <dest-dir>/gitleaks
#
# Environment: GITLEAKS_FETCH_ATTEMPTS (3), GITLEAKS_FETCH_TIMEOUT (40 s per attempt),
#   GITLEAKS_SHA256 (expected digest; defaults to the pin for 8.30.1, empty skips the
#   check), GITLEAKS_BASE_URL.
set -euo pipefail

version="${1:-${GITLEAKS_VERSION:-8.30.1}}"
dest_dir="${2:-${RUNNER_TEMP:-/tmp}}"
attempts="${GITLEAKS_FETCH_ATTEMPTS:-3}"
attempt_seconds="${GITLEAKS_FETCH_TIMEOUT:-40}"
base_url="${GITLEAKS_BASE_URL:-https://github.com/gitleaks/gitleaks/releases/download}"

pin_sha=""
if [ "$version" = "8.30.1" ]; then
  pin_sha="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
fi
expected_sha="${GITLEAKS_SHA256-$pin_sha}"

url="${base_url}/v${version}/gitleaks_${version}_linux_x64.tar.gz"
tarball="${dest_dir}/gitleaks_${version}.tar.gz"
mkdir -p "$dest_dir"

digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# A digest that does not match means a truncated or corrupted archive, which is exactly the
# failure that reddened the job before: treat it as a failed attempt and retry.
sha_matches() {
  [ -n "$expected_sha" ] || return 0
  [ "$(digest "$tarball")" = "$expected_sha" ]
}

fetch() {
  if command -v timeout >/dev/null 2>&1; then
    timeout $((attempt_seconds + 5)) \
      curl -fsSL --connect-timeout 10 --max-time "$attempt_seconds" -o "$tarball" "$url"
  else
    curl -fsSL --connect-timeout 10 --max-time "$attempt_seconds" -o "$tarball" "$url"
  fi
}

attempt=1
while [ "$attempt" -le "$attempts" ]; do
  # Each attempt starts from nothing: a partial file left by a killed attempt fails tar
  # with "unexpected end of file". The tarball is removed once it has served its purpose.
  rm -f "$tarball"
  if fetch && sha_matches && tar -xzf "$tarball" -C "$dest_dir" gitleaks; then
    rm -f "$tarball"
    echo "fetch-gitleaks: v${version} in ${dest_dir}/gitleaks (attempt ${attempt})"
    exit 0
  fi
  rm -f "$tarball"
  echo "fetch-gitleaks: attempt ${attempt}/${attempts} failed" >&2
  attempt=$((attempt + 1))
  [ "$attempt" -gt "$attempts" ] || sleep 2
done

echo "fetch-gitleaks: could not download gitleaks v${version} after ${attempts} attempts" >&2
exit 1
