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
    test_call_site_deduplication()
    print("\nAll self-test validation assertions passed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
