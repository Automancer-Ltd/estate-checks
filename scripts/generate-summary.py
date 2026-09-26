#!/usr/bin/env python3
"""Generates GitHub Step Summary and outputs from Semgrep JSON results, with scan validation and call-site deduplication."""

import fnmatch
import json
import os
import subprocess
import sys


IN_SCOPE_EXTENSIONS = {
    ".js", ".mjs", ".cjs", ".jsx",
    ".ts", ".mts", ".cts", ".tsx",
    ".py",
    ".sh", ".bash",
    ".yml", ".yaml",
}


def _excluded(path: str, patterns: list) -> bool:
    """True when any exclude pattern matches the path or one of its trailing sub-paths.

    Errs towards "excluded": a wrong match can only hide a crash on a scan that had
    nothing to scan, never fail a healthy one.
    """
    parts = path.split("/")
    suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
    for pat in patterns:
        prefix = pat[:-3] if pat.endswith("/**") else None
        for suffix in suffixes:
            if fnmatch.fnmatch(suffix, pat) or fnmatch.fnmatch(os.path.basename(suffix), pat):
                return True
            if prefix and (suffix.startswith(prefix + "/") or suffix == prefix):
                return True
    return False


def find_in_scope_files(root: str, base_commit: str = "", excludes: list = None, max_check: int = 10) -> list:
    """Files Semgrep should have scanned: changed files since the baseline on a
    differential scan, every tracked file otherwise, minus exclusions."""
    excludes = excludes or []
    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", base_commit, "HEAD"] if base_commit else ["git", "ls-files"]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        # Cannot tell what should have been scanned: say so rather than guess.
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    found = []
    for line in proc.stdout.splitlines():
        if os.path.splitext(line)[1].lower() in IN_SCOPE_EXTENSIONS and not _excluded(line, excludes):
            found.append(line)
            if len(found) >= max_check:
                break
    return found


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: generate-summary.py <results.json> <is_pr> [base_commit] [repo_root]", file=sys.stderr)
        return 1

    results_file = sys.argv[1]
    is_pr = sys.argv[2].lower() in ("true", "1", "yes")
    base_commit = sys.argv[3] if len(sys.argv) > 3 else ""
    repo_root = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else os.getcwd()

    if not os.path.exists(results_file):
        print(f"::warning::Results file '{results_file}' not found.", file=sys.stderr)
        return 1

    try:
        with open(results_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"::warning::Failed to parse JSON from '{results_file}': {e}", file=sys.stderr)
        return 1

    # 1. Assert JSON has no errors at level error or fatal
    errors = data.get("errors", [])
    fatal_errors = [
        e for e in errors
        if isinstance(e, dict) and str(e.get("level", "")).lower() in ("error", "fatal")
    ]
    if fatal_errors:
        err_msgs = [f"[{e.get('type', 'Error')}] {e.get('message', e)}" for e in fatal_errors]
        print(f"::warning::Semgrep scan reported fatal error(s): {'; '.join(err_msgs)}", file=sys.stderr)
        return 1

    # 2. Assert paths.scanned is not empty while repo has files in scope
    scanned = data.get("paths", {}).get("scanned", [])
    if len(scanned) == 0:
        excludes = [p.strip() for p in os.environ.get("ESTATE_CHECKS_EXCLUDES", "").replace(",", "\n").splitlines() if p.strip()]
        try:
            in_scope = find_in_scope_files(repo_root, base_commit, excludes)
        except Exception as e:
            print(f"::warning::Semgrep scanned 0 files and the in-scope file list could not be read: {e}", file=sys.stderr)
            return 1
        if in_scope:
            sample = ", ".join(in_scope[:3])
            print(
                f"::warning::Semgrep scanned 0 files, but {len(in_scope)} in-scope file(s) exist in repository (e.g. {sample}). Scan may have failed or crashed silently.",
                file=sys.stderr,
            )
            return 1

    # 3. Deduplicate findings by call site (check_id, path, line)
    raw_results = data.get("results", [])
    deduped_results = []
    seen = set()
    for r in raw_results:
        check_id = r.get("check_id")
        path = r.get("path")
        start = r.get("start", {})
        line_no = start.get("line", 1)
        key = (check_id, path, line_no)
        if key not in seen:
            seen.add(key)
            deduped_results.append(r)

    # Write deduplicated results back to results_file
    data["results"] = deduped_results
    try:
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"::warning::Failed to update '{results_file}' with deduplicated results: {e}", file=sys.stderr)

    count = len(deduped_results)

    # One inline annotation per finding, so it shows on the PR diff as a warning.
    for r in deduped_results:
        msg = r.get("extra", {}).get("message", "").replace("\n", " ").strip()
        rule_id = r.get("check_id", "").split(".")[-1]
        print(f"::warning file={r.get('path','')},line={r.get('start', {}).get('line', 1)},title=Unbounded outside call ({rule_id})::{msg}")

    # Set step output
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and os.path.exists(output_path):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"findings_count={count}\n")

    summary_lines = [
        "## Estate Checks: Unbounded Outside Calls",
        "",
    ]

    if count == 0:
        summary_lines.append(
            ":white_check_mark: **Passed:** No unbounded network or subprocess calls detected."
        )
    else:
        if is_pr:
            baseline_note = f" (relative to baseline `{base_commit[:8]}`)" if base_commit else ""
            summary_lines.append(
                f":warning: **Warning:** this pull request adds **{count}** call(s) without a time limit{baseline_note}. Not blocking; please address."
            )
        else:
            summary_lines.append(
                f":warning: **Report:** Found **{count}** unbounded call(s) on default branch (non-blocking)."
            )

        summary_lines.extend(
            [
                "",
                "| Rule | Location | Description & Fix |",
                "|---|---|---|",
            ]
        )

        for r in deduped_results:
            rule_id = r.get("check_id", "").split(".")[-1]
            path = r.get("path", "")
            start = r.get("start", {})
            line_no = start.get("line", 1)
            loc = f"`{path}:{line_no}`"
            msg = r.get("extra", {}).get("message", "").replace("\n", " ").strip()
            summary_lines.append(f"| `{rule_id}` | {loc} | {msg} |")

        summary_lines.extend(
            [
                "",
                "> [!TIP]",
                "> If a call is deliberately unbounded (e.g. a long-poll with its own watchdog), suppress it with a reason:",
                "> `// nosemgrep: <rule-id> - <reason>` or `# nosemgrep: <rule-id> - <reason>`.",
            ]
        )

    summary_text = "\n".join(summary_lines) + "\n"

    step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        with open(step_summary_path, "a", encoding="utf-8") as f:
            f.write(summary_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
