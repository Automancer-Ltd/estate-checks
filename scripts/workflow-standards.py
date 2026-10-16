#!/usr/bin/env python3
"""Workflow standards: the mechanical CI rules, reported as warnings.

The ci-standards skill's first four rules are structural and mechanical, so they
belong in a guard, not in 27 KB of incident prose. This check reads
``.github/workflows/*.yml`` in the repository it runs in and annotates:

1. ``job-missing-timeout`` / ``job-timeout-over-cap`` — every job declares
   ``timeout-minutes``, and none declares more than the 30-minute cap. A job
   that only calls a reusable workflow (``uses:``) is exempt: workflow syntax
   bans the key on a caller job, and the called workflow bounds its own jobs.
2. ``workflow-missing-concurrency`` / ``workflow-concurrency-no-group`` — every
   workflow declares a concurrency group.
3. ``push-unfiltered`` / ``push-wrong-branch`` — ``push:`` carries a branch
   filter and that filter includes the default branch.
4. ``runner-label-literal`` — in an organisation repository, ``runs-on`` comes
   from ``vars.CI_RUNNER``, never a literal hosted label.

Warn-only (Waseem, 2026-09-27, via the estate-checks action): every finding is
an inline annotation and a job-summary line. This script never fails a job, even
when it finds something — a style violation in a workflow must not block a pull
request. A workflow that cannot be parsed is reported the same way: a warning,
because a check that could not run must never look like a clean result.

Parsing is ``yaml.compose`` rather than ``yaml.safe_load`` so each finding can
carry the line of the key at fault. Standard library plus PyYAML.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yaml.nodes import Node

TIMEOUT_CAP_MINUTES = 30
DEFAULT_WORKFLOWS_DIR = ".github/workflows"
# Personal repositories run on GitHub-hosted runners (ci-standards, rule 4).
PERSONAL_OWNERS = {"Waseemilyas"}
HOSTED_LABEL = re.compile(r"^(ubuntu|macos|windows|blacksmith)-")

TITLES = {
    "job-missing-timeout": "Job declares no timeout-minutes",
    "job-timeout-over-cap": "Timeout above the 30-minute cap",
    "workflow-missing-concurrency": "Workflow declares no concurrency group",
    "workflow-concurrency-no-group": "Concurrency group is empty",
    "push-unfiltered": "push is not filtered to a branch",
    "push-wrong-branch": "push excludes the default branch",
    "runner-label-literal": "runs-on names a hosted runner label",
    "workflow-unparsable": "Workflow could not be checked",
}


class Finding:
    """One mechanical rule breach, with the position to annotate."""

    __slots__ = ("rule", "path", "line", "message")

    def __init__(self, rule: str, path: str, line: int, message: str) -> None:
        self.rule = rule
        self.path = path
        self.line = line
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Finding({self.rule!r}, {self.path!r}, {self.line}, {self.message!r})"


def _load_yaml() -> ModuleType | None:
    """Return the yaml module, or None when PyYAML is not importable."""
    try:
        import yaml  # noqa: PLC0415 - optional at import time on purpose
    except Exception:  # pragma: no cover - depends on the runner image
        return None
    return yaml


def _items(node: Node | None) -> list[tuple[str, Node, int]]:
    """(key text, value node, key line) for a mapping node, else []."""
    from yaml.nodes import MappingNode, ScalarNode

    if not isinstance(node, MappingNode):
        return []
    return [(key.value, value, key.start_mark.line + 1)
            for key, value in node.value if isinstance(key, ScalarNode)]


def _find(node: Node | None, name: str) -> tuple[Node | None, int]:
    """The (value node, key line) of a mapping key, else (None, 1)."""
    for key, value, line in _items(node):
        if key == name:
            return value, line
    return None, 1


def _scalars(node: Node | None) -> list[str]:
    """Every scalar value under a node (one value, or a sequence of them)."""
    from yaml.nodes import ScalarNode, SequenceNode

    if isinstance(node, ScalarNode):
        return [node.value]
    if isinstance(node, SequenceNode):
        return [child.value for child in node.value if isinstance(child, ScalarNode)]
    return []


def _job_label(key: str) -> str:
    return f"job {key!r}"


def _check_jobs(doc: Node, path: str, owner: str, findings: list[Finding]) -> None:
    jobs, _jobs_line = _find(doc, "jobs")
    if jobs is None:
        return
    for job_key, job, job_line in _items(jobs):
        uses, _uses_line = _find(job, "uses")
        if uses is not None:
            # A reusable-workflow caller job cannot declare ``timeout-minutes``
            # or ``runs-on`` — workflow syntax bans both keys there — and the
            # called workflow bounds its own jobs, so neither rule applies.
            continue
        timeout, timeout_line = _find(job, "timeout-minutes")
        if timeout is None:
            findings.append(
                Finding(
                    "job-missing-timeout",
                    path,
                    job_line,
                    f"{_job_label(job_key)} declares no timeout-minutes. Add "
                    "`timeout-minutes:` (at most 10 in routine CI, at most 30 in an "
                    "extended suite); with none declared GitHub allows 360 minutes.",
                )
            )
        else:
            values = _scalars(timeout)
            raw = values[0] if values else ""
            if raw.strip().isdigit() and int(raw) > TIMEOUT_CAP_MINUTES:
                findings.append(
                    Finding(
                        "job-timeout-over-cap",
                        path,
                        timeout_line,
                        f"{_job_label(job_key)} declares timeout-minutes: {int(raw)}, above "
                        f"the {TIMEOUT_CAP_MINUTES}-minute absolute cap. Diagnose and tune the "
                        "slow step; do not raise the limit.",
                    )
                )

        runs_on, runs_line = _find(job, "runs-on")
        if runs_on is None or not owner or owner in PERSONAL_OWNERS:
            continue
        values = _scalars(runs_on)
        if not values or any("vars.CI_RUNNER" in value for value in values):
            continue  # an expression or a self-hosted label: not this rule's business
        literal = next((value for value in values if HOSTED_LABEL.match(value.strip())), None)
        if literal:
            findings.append(
                Finding(
                    "runner-label-literal",
                    path,
                    runs_line,
                    f"{_job_label(job_key)} runs on the literal label {literal!r}. An "
                    "organisation job takes `runs-on: ${{ fromJSON(vars.CI_RUNNER || "
                    "'\"ubuntu-latest\"') }}` so the monthly runner switch reaches it.",
                )
            )


def _check_concurrency(doc: Node, path: str, findings: list[Finding]) -> None:
    concurrency, line = _find(doc, "concurrency")
    if concurrency is None:
        findings.append(
            Finding(
                "workflow-missing-concurrency",
                path,
                1,
                "This workflow declares no concurrency group, so every push runs the whole "
                "matrix to completion. Add the standard block (group keyed on workflow, "
                "event and pull-request number or ref; cancel-in-progress true only on pull "
                "requests). A deploy scheduler or worker keeps its own group with "
                "cancel-in-progress: false.",
            )
        )
        return
    group, group_line = _find(concurrency, "group")
    if group is not None:
        if not _scalars(group) or not _scalars(group)[0].strip():
            findings.append(
                Finding(
                    "workflow-concurrency-no-group",
                    path,
                    group_line,
                    "The concurrency group is empty, so nothing is grouped. Key it on the "
                    "workflow, the event and the pull-request number or ref.",
                )
            )
        return
    if _scalars(concurrency) and _scalars(concurrency)[0].strip():
        return  # the short form `concurrency: <group>`
    findings.append(
        Finding(
            "workflow-concurrency-no-group",
            path,
            line,
            "This concurrency block declares no `group`, so nothing is grouped. Key it on "
            "the workflow, the event and the pull-request number or ref.",
        )
    )


def _check_push(doc: Node, path: str, default_branch: str, findings: list[Finding]) -> None:
    from yaml.nodes import MappingNode

    triggers, _line = _find(doc, "on")
    if triggers is None:
        return
    event_names = []
    if isinstance(triggers, MappingNode):
        event_names = [key for key, _value, _line in _items(triggers)]
        push, push_line = _find(triggers, "push")
    else:
        event_names = _scalars(triggers)
        push, push_line = (triggers, triggers.start_mark.line + 1) if "push" in event_names else (None, 1)
    if "push" not in event_names:
        return
    if push is None or not isinstance(push, MappingNode):
        findings.append(
            Finding(
                "push-unfiltered",
                path,
                push_line or 1,
                "`push:` carries no branch filter, so every branch push bills the whole matrix "
                "a second time alongside the pull request. Filter it to the default branch.",
            )
        )
        return
    keys = [key for key, _value, _line in _items(push)]
    has_branches = "branches" in keys or "branches-ignore" in keys
    has_tags = "tags" in keys or "tags-ignore" in keys
    if not has_branches:
        if has_tags:
            return  # tag-only pushes do not double-bill branch work
        findings.append(
            Finding(
                "push-unfiltered",
                path,
                push_line,
                "`push:` carries no branch filter, so every branch push bills the whole matrix "
                "a second time alongside the pull request. Filter it to the default branch.",
            )
        )
        return
    if not default_branch:
        return
    branches_node, branches_line = _find(push, "branches")
    branches = _scalars(branches_node)
    if not branches:
        return  # branches-ignore, which this check does not judge
    if not any(fnmatch.fnmatch(default_branch, pattern) for pattern in branches):
        findings.append(
            Finding(
                "push-wrong-branch",
                path,
                branches_line,
                f"`push.branches` is {branches} and does not include the default branch "
                f"{default_branch!r}, so the default branch's own pushes do not run this "
                "workflow.",
            )
        )


def check_workflow(path: str, text: str, owner: str, default_branch: str) -> list[Finding]:
    """Every finding for one workflow file, in file order."""
    yaml = _load_yaml()
    if yaml is None:
        return [
            Finding(
                "workflow-unparsable",
                path,
                1,
                "PyYAML is not importable here, so this workflow was not checked.",
            )
        ]
    from yaml.nodes import MappingNode

    findings: list[Finding] = []
    try:
        docs = [doc for doc in yaml.compose_all(text) if doc is not None]
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return [
            Finding(
                "workflow-unparsable",
                path,
                1,
                f"This workflow could not be parsed ({first}), so the workflow standards check "
                "skipped it.",
            )
        ]
    for doc in docs:
        if not isinstance(doc, MappingNode):
            findings.append(
                Finding(
                    "workflow-unparsable",
                    path,
                    doc.start_mark.line + 1,
                    "This file is not a YAML mapping, so the workflow standards check skipped it.",
                )
            )
            continue
        _check_jobs(doc, path, owner, findings)
        _check_concurrency(doc, path, findings)
        _check_push(doc, path, default_branch, findings)
    return findings


def _workflow_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    names = sorted(os.listdir(directory))
    return [
        os.path.join(directory, name)
        for name in names
        if name.endswith((".yml", ".yaml")) and os.path.isfile(os.path.join(directory, name))
    ]


def scan(root: str, workflows_dir: str, owner: str, default_branch: str) -> list[Finding]:
    findings: list[Finding] = []
    for path in _workflow_files(workflows_dir):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            findings.append(
                Finding("workflow-unparsable", os.path.relpath(path, root), 1, f"Unreadable: {exc}")
            )
            continue
        relative = os.path.relpath(path, root)
        findings.extend(check_workflow(relative, text, owner, default_branch))
    return findings


def _escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_annotations(findings: list[Finding]) -> None:
    for finding in findings:
        title = TITLES.get(finding.rule, finding.rule)
        print(f"::warning file={finding.path},line={finding.line},title={title}::{_escape(finding.message)}")


def emit_summary(findings: list[Finding], summary_path: str | None) -> None:
    if not summary_path:
        return
    lines = [
        "## Workflow standards: "
        + (f"{len(findings)} warning(s)" if findings else "no warnings"),
        "",
        "Warn-only check of ci-standards rules 1-4 (job timeouts, concurrency groups, a "
        "filtered `push:`, and `runs-on` from `vars.CI_RUNNER`). It never fails this job.",
        "",
    ]
    if findings:
        lines += ["| Where | Rule | What to fix |", "| --- | --- | --- |"]
        lines += [
            f"| `{finding.path}:{finding.line}` | {TITLES.get(finding.rule, finding.rule)} | "
            f"{finding.message} |"
            for finding in findings
        ]
    else:
        lines.append("Every workflow in this repository satisfies all four rules.")
    lines.append("")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Warn about ci-standards rules 1-4 in this repo's workflows.")
    parser.add_argument("--root", default=os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    parser.add_argument("--workflows-dir", default=None)
    parser.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    parser.add_argument("--default-branch", default="")
    parser.add_argument("--json", action="store_true", help="print findings as JSON and nothing else")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    workflows_dir = args.workflows_dir or os.path.join(root, DEFAULT_WORKFLOWS_DIR)
    findings = scan(root, workflows_dir, args.owner, args.default_branch)
    if args.json:
        import json  # noqa: PLC0415

        print(
            json.dumps(
                [
                    {"rule": f.rule, "path": f.path, "line": f.line, "message": f.message}
                    for f in findings
                ],
                indent=2,
            )
        )
        return 0
    emit_annotations(findings)
    emit_summary(findings, args.summary)
    print(
        f"Workflow standards: {len(findings)} warning(s) across "
        f"{len(_workflow_files(workflows_dir))} workflow file(s)."
    )
    # Machine-readable count for the action step's output.
    print(f"workflow-standards-count={len(findings)}")
    return 0  # warn-only: this check never fails the job


if __name__ == "__main__":
    sys.exit(main())
