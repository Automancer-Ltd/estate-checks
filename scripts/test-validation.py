#!/usr/bin/env python3
"""Self-test suite for estate-checks failure modes, scan validation, and call-site deduplication."""

import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_SCRIPT = os.path.join(REPO_ROOT, "scripts", "generate-summary.py")


def test_fatal_error_fails() -> None:
    print("Testing that fatal/error scan results fail...")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({
            "results": [],
            "paths": {"scanned": ["foo.js"]},
            "errors": [{"level": "error", "message": "io_uring memory allocation failure"}]
        }, f)
        path = f.name
    try:
        proc = subprocess.run([sys.executable, SUMMARY_SCRIPT, path, "false", "", REPO_ROOT], capture_output=True, text=True)
        assert proc.returncode != 0, f"Expected non-zero exit code for fatal error, got {proc.returncode}"
        assert "fatal error" in proc.stderr.lower() or "error" in proc.stderr.lower(), f"Unexpected stderr: {proc.stderr}"
        print("  ✓ Fatal error in scan results successfully failed.")
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_empty_scan_with_in_scope_files_fails() -> None:
    print("Testing that empty scan paths with in-scope files fails...")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({
            "results": [],
            "paths": {"scanned": []},
            "errors": []
        }, f)
        path = f.name
    try:
        proc = subprocess.run([sys.executable, SUMMARY_SCRIPT, path, "false", "", REPO_ROOT], capture_output=True, text=True)
        assert proc.returncode != 0, f"Expected non-zero exit code for empty scan, got {proc.returncode}"
        assert "scanned 0 files" in proc.stderr.lower(), f"Unexpected stderr: {proc.stderr}"
        print("  ✓ Empty scan with in-scope files successfully failed.")
    finally:
        if os.path.exists(path):
            os.remove(path)


def _diff_repo(files_before: dict, files_after: dict) -> tuple:
    """A throwaway git repo with two commits; returns (root, base_sha)."""
    root = tempfile.mkdtemp()
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=root, check=True, capture_output=True, text=True)
    git("init", "-q")
    for name, body in files_before.items():
        os.makedirs(os.path.dirname(os.path.join(root, name)) or root, exist_ok=True)
        open(os.path.join(root, name), "w").write(body)
    git("add", "."); git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    for name, body in files_after.items():
        os.makedirs(os.path.dirname(os.path.join(root, name)) or root, exist_ok=True)
        open(os.path.join(root, name), "w").write(body)
    git("add", "."); git("commit", "-qm", "change")
    return root, base


def _run_empty_scan(root: str, base: str, excludes: str = "") -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"results": [], "paths": {"scanned": []}, "errors": []}, f)
    env = dict(os.environ, ESTATE_CHECKS_EXCLUDES=excludes)
    return subprocess.run([sys.executable, SUMMARY_SCRIPT, f.name, "true", base, root], capture_output=True, text=True, env=env)


def test_diff_scan_of_docs_only_change_passes() -> None:
    print("Testing that a PR changing no in-scope file passes an empty differential scan...")
    root, base = _diff_repo({"b.js": "x()\n"}, {"README.md": "# docs\n", "deploy.json": "{}\n"})
    proc = _run_empty_scan(root, base)
    assert proc.returncode == 0, f"docs-only PR must pass, got {proc.returncode}: {proc.stderr}"
    print("  ✓ Docs/config-only change passed.")


def test_diff_scan_of_code_change_that_scanned_nothing_fails() -> None:
    print("Testing that an empty differential scan fails when a changed code file exists...")
    root, base = _diff_repo({"b.js": "x()\n"}, {"b.js": "y()\n"})
    proc = _run_empty_scan(root, base)
    assert proc.returncode != 0 and "scanned 0 files" in proc.stderr.lower(), f"expected failure, got {proc.returncode}: {proc.stderr}"
    print("  ✓ Changed code file with empty scan failed.")


def test_diff_scan_of_excluded_change_passes() -> None:
    print("Testing that a change only to excluded paths passes an empty differential scan...")
    root, base = _diff_repo({"b.js": "x()\n"}, {"tests/a.test.js": "t()\n", "web/ui/c.ts": "u()\n"})
    proc = _run_empty_scan(root, base, "*.test.*, tests/**,web/ui/**")
    assert proc.returncode == 0, f"excluded-only PR must pass, got {proc.returncode}: {proc.stderr}"
    print("  ✓ Excluded-only change passed.")


def test_missing_results_file_fails() -> None:
    print("Testing that missing results file fails...")
    path = os.path.join(tempfile.gettempdir(), "missing-semgrep-results-99999.json")
    if os.path.exists(path):
        os.remove(path)
    proc = subprocess.run([sys.executable, SUMMARY_SCRIPT, path, "false", "", REPO_ROOT], capture_output=True, text=True)
    assert proc.returncode != 0, f"Expected non-zero exit code for missing file, got {proc.returncode}"
    print("  ✓ Missing results file successfully failed.")


def test_call_site_deduplication() -> None:
    print("Testing call-site deduplication on identical line...")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({
            "results": [
                {"check_id": "rules.fetch-no-signal", "path": "src/api.ts", "start": {"line": 42, "col": 10}, "extra": {"message": "unbounded"}},
                {"check_id": "rules.fetch-no-signal", "path": "src/api.ts", "start": {"line": 42, "col": 35}, "extra": {"message": "unbounded"}},
                {"check_id": "rules.fetch-no-signal", "path": "src/api.ts", "start": {"line": 42, "col": 60}, "extra": {"message": "unbounded"}},
            ],
            "paths": {"scanned": ["src/api.ts"]},
            "errors": []
        }, f)
        path = f.name
    try:
        proc = subprocess.run([sys.executable, SUMMARY_SCRIPT, path, "false", "", REPO_ROOT], capture_output=True, text=True)
        assert proc.returncode == 0, f"Expected 0 exit code for deduplication, got {proc.returncode}: {proc.stderr}"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["results"]) == 1, f"Expected 1 deduplicated result, got {len(data['results'])}"
        print("  ✓ Call-site deduplication collapsed 3 hits on line 42 to 1 finding.")
    finally:
        if os.path.exists(path):
            os.remove(path)


def main() -> int:
    test_fatal_error_fails()
    test_empty_scan_with_in_scope_files_fails()
    test_missing_results_file_fails()
    test_diff_scan_of_docs_only_change_passes()
    test_diff_scan_of_code_change_that_scanned_nothing_fails()
    test_diff_scan_of_excluded_change_passes()
    test_call_site_deduplication()
    print("\nAll self-test validation assertions passed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
