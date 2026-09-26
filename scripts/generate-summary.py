#!/usr/bin/env python3
"""Generates GitHub Step Summary and outputs from Semgrep JSON results."""

import json
import os
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: generate-summary.py <results.json> <is_pr> [base_commit]")
        return 1

    results_file = sys.argv[1]
    is_pr = sys.argv[2].lower() in ("true", "1", "yes")
    base_commit = sys.argv[3] if len(sys.argv) > 3 else ""

    if not os.path.exists(results_file):
        print(f"Results file {results_file} not found.", file=sys.stderr)
        return 1

    with open(results_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = data.get("results", [])
    count = len(results)

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
                f":x: **Failed:** Found **{count}** new unbounded call(s) introduced in this pull request{baseline_note}."
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

        for r in results:
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
