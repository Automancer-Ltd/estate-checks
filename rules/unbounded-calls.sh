#!/usr/bin/env bash
# Unit test fixtures for shell script unbounded-calls rules

# ============================================================================
# shell-curl-no-timeout
# ============================================================================

# ruleid: shell-curl-no-timeout
curl https://example.com/api/data

# ruleid: shell-curl-no-timeout
curl -sL https://example.com/file.tar.gz

# ruleid: shell-curl-no-timeout
curl -X POST -H "Content-Type: application/json" -d '{"a":1}' https://example.com

# ok: shell-curl-no-timeout
curl -m 10 https://example.com/api/data

# ok: shell-curl-no-timeout
curl --max-time 15 https://example.com/api/data

# ok: shell-curl-no-timeout
curl --max-time=30 https://example.com/api/data

# ok: shell-curl-no-timeout
curl -m10 https://example.com/api/data

# ok: shell-curl-no-timeout
curl -sL -m 5 https://example.com/file.tar.gz

TIMEOUT=20
# ok: shell-curl-no-timeout
curl -m "$TIMEOUT" https://example.com/api/data

CURL_TIMEOUT_ARGS=(--max-time 15 --connect-timeout 5)
# ok: shell-curl-no-timeout
curl "${CURL_TIMEOUT_ARGS[@]}" https://example.com/api/data

# ok: shell-curl-no-timeout
# nosemgrep: shell-curl-no-timeout
curl https://example.com/unbounded-stream


# ============================================================================
# shell-wget-no-timeout
# ============================================================================

# ruleid: shell-wget-no-timeout
wget https://example.com/file.tar.gz

# ruleid: shell-wget-no-timeout
wget -q https://example.com/file.tar.gz

# ok: shell-wget-no-timeout
wget --timeout=15 https://example.com/file.tar.gz

# ok: shell-wget-no-timeout
wget --timeout 20 https://example.com/file.tar.gz

# ok: shell-wget-no-timeout
wget -T 10 https://example.com/file.tar.gz

# ok: shell-wget-no-timeout
wget -T10 https://example.com/file.tar.gz

WGET_TIMEOUT_OPTS="--timeout 10"
# ok: shell-wget-no-timeout
wget $WGET_TIMEOUT_OPTS https://example.com/file.tar.gz
