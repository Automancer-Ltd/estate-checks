# Unit test fixtures for Python unbounded-calls rules

import requests
import httpx
import urllib.request
from urllib.request import urlopen
import subprocess
from subprocess import check_output, check_call


# ============================================================================
# python-requests-no-timeout
# ============================================================================

# ruleid: python-requests-no-timeout
requests.get("https://api.example.com/data")

# ruleid: python-requests-no-timeout
requests.post("https://api.example.com/data", json={"key": "value"})

# ruleid: python-requests-no-timeout
requests.put("https://api.example.com/data", data="payload")

# ruleid: python-requests-no-timeout
requests.delete("https://api.example.com/data/1")

# ruleid: python-requests-no-timeout
requests.get("https://api.example.com/data", timeout=None)

# ok: python-requests-no-timeout
requests.get("https://api.example.com/data", timeout=10)

# ok: python-requests-no-timeout
requests.post("https://api.example.com/data", json={"key": "value"}, timeout=10)

# ok: python-requests-no-timeout
requests.get("https://api.example.com/data", timeout=(5, 30))

timeout_sec = 15
# ok: python-requests-no-timeout
requests.get("https://api.example.com/data", timeout=timeout_sec)

# Wrapper function passing kwargs
# ok: python-requests-no-timeout
def custom_requests_wrapper(url, **kwargs):
    return requests.get(url, **kwargs)

# ok: python-requests-no-timeout
# nosemgrep: python-requests-no-timeout
requests.get("https://api.example.com/long-poll")


# ============================================================================
# python-httpx-no-timeout
# ============================================================================

# ruleid: python-httpx-no-timeout
httpx.get("https://api.example.com/data")

# ruleid: python-httpx-no-timeout
httpx.post("https://api.example.com/data", json={"key": "value"})

# ruleid: python-httpx-no-timeout
httpx.Client()

# ruleid: python-httpx-no-timeout
httpx.Client(base_url="https://api.example.com")

# ruleid: python-httpx-no-timeout
httpx.AsyncClient()

# ruleid: python-httpx-no-timeout
httpx.get("https://api.example.com/data", timeout=None)

# ruleid: python-httpx-no-timeout
httpx.Client(timeout=None)

# ok: python-httpx-no-timeout
httpx.get("https://api.example.com/data", timeout=10.0)

# ok: python-httpx-no-timeout
client = httpx.Client(timeout=10.0)
# ok: python-httpx-no-timeout
client.get("https://api.example.com/data")

# ok: python-httpx-no-timeout
async_client = httpx.AsyncClient(timeout=10.0)

# Wrapper function passing kwargs
# ok: python-httpx-no-timeout
def custom_httpx_wrapper(url, **kwargs):
    return httpx.get(url, **kwargs)


# ============================================================================
# python-urllib-no-timeout
# ============================================================================

# ruleid: python-urllib-no-timeout
urllib.request.urlopen("https://api.example.com/data")

# ruleid: python-urllib-no-timeout
urllib.request.urlopen("https://api.example.com/data", b"payload")

# ruleid: python-urllib-no-timeout
urllib.request.urlopen("https://api.example.com/data", timeout=None)

# ruleid: python-urllib-no-timeout
urlopen("https://api.example.com/data")

# ok: python-urllib-no-timeout
urllib.request.urlopen("https://api.example.com/data", timeout=10)

# ok: python-urllib-no-timeout
urllib.request.urlopen("https://api.example.com/data", b"payload", 10)

# ok: python-urllib-no-timeout
urlopen("https://api.example.com/data", timeout=10)

# Wrapper function passing kwargs
# ok: python-urllib-no-timeout
def custom_urlopen_wrapper(url, **kwargs):
    return urllib.request.urlopen(url, **kwargs)


# ============================================================================
# python-subprocess-no-timeout
# ============================================================================

# ruleid: python-subprocess-no-timeout
subprocess.run(["ls", "-la"])

# ruleid: python-subprocess-no-timeout
subprocess.check_output(["echo", "hello"])

# ruleid: python-subprocess-no-timeout
subprocess.check_call(["echo", "hello"])

# ruleid: python-subprocess-no-timeout
check_output(["cat", "/etc/hosts"])

# ruleid: python-subprocess-no-timeout
subprocess.run(["sleep", "10"], timeout=None)

# ok: python-subprocess-no-timeout
subprocess.run(["ls", "-la"], timeout=30)

# ok: python-subprocess-no-timeout
subprocess.check_output(["echo", "hello"], timeout=5)

# ok: python-subprocess-no-timeout
subprocess.check_call(["echo", "hello"], timeout=5)

# ok: python-subprocess-no-timeout
check_output(["cat", "/etc/hosts"], timeout=10)

subp_timeout = 20
# ok: python-subprocess-no-timeout
subprocess.run(["ls", "-la"], timeout=subp_timeout)

# Wrapper function passing kwargs
# ok: python-subprocess-no-timeout
def custom_subprocess_wrapper(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)
