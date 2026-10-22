#!/usr/bin/env python3
"""Install timeouts: a workflow step that installs packages must be bounded.

A package install is the one CI step that talks to a package index for minutes
on end, so an install with no time limit is the one step that can hold a job
until GitHub cancels it. That exact fault killed the automancer-ops release
check on 19 Oct 2026 (run 39332563829, job 118334064821): the job was killed
4m38s in while a ``pip`` install was still running and the release PR stayed
red and unlanded.

This check reads ``.github/workflows/*.yml`` in the repository it runs in and
fails the run when a ``run:`` step executes a package install — ``pip
install``, ``uv pip install``, ``npm ci``/``npm install``, ``apt-get
install``/``apt install`` or ``go install`` — while neither the step nor its
job declares ``timeout-minutes``. Unlike the unbounded-call scan this check
blocks by default (tracker t4456): the failure it prevents is a red, unlanded
release PR, so finding it in the same PR that introduces it is the point.

A deliberate exception is a row in ``install-timeout-allowlist.tsv`` (repo,
workflow path, job id, reason) next to this script's repository root; the
allowlist is the estate's shared record, so an exception is a pull request to
Automancer-Ltd/estate-checks, not a comment in the caller. A check that could
not run — PyYAML missing, a workflow that will not parse, a malformed
allowlist row — fails loudly rather than passing silently.

Reading and the finding type live in ``workflow_yaml``. Standard library plus PyYAML.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import TYPE_CHECKING

import workflow_yaml
from workflow_yaml import Finding, escape, find, items, load_yaml, workflow_files

if TYPE_CHECKING:
    from yaml.nodes import Node

DEFAULT_ALLOWLIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "install-timeout-allowlist.tsv",
)
WILDCARD = "*"

# The command set is the tracker card's (t4456): the install commands the
# estate's workflows actually run. Adding a tool here trades recall for false
# positives on every consumer, so a new tool is a deliberate change with tests.
INSTALL_COMMANDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pip install",
        re.compile(r"(?<!uv\s)\b(?:python3?(?:\.\d+)?\s+-m\s+)?pip3?\s+install\b"),
    ),
    ("uv pip install", re.compile(r"\buv\s+pip\s+install\b")),
    ("npm ci / npm install", re.compile(r"\bnpm\s+(?:ci|install)\b")),
    ("apt-get install", re.compile(r"\b(?:apt-get|apt)\s+install\b")),
    ("go install", re.compile(r"\bgo\s+install\b")),
)

# A line whose first word is a comment or an echo is not a command that runs,
# so an install mentioned in prose or a comment is not an install.
_NON_COMMAND = re.compile(r"^\s*(?:#|echo\b)")
# coreutils ``timeout`` in front of the command bounds it, options and all:
# ``timeout 300``, ``timeout 5m``, ``timeout -k 5 300``, ``timeout --signal=KILL 5m``.
_BOUNDED_BY_TIMEOUT = re.compile(
    r"(?:^|[\s;&|])timeout(?:\s+(?:-[ks]|--kill-after|--signal)\s+\S+|\s+-\S+)*\s+\d+(?:\.\d+)?[smhd]?\b")

TITLES = {
    "install-unbounded": "Step installs packages with no timeout",
    "workflow-unparsable": "Workflow could not be checked",
    "check-could-not-run": "Install timeout check could not run",
}


class AllowlistError(Exception):
    """The allowlist file is malformed, so nothing it says can be trusted."""


def _scalar(node: Node | None) -> str:
    """The scalar text of a node, else '' (installs live in plain scalars)."""
    from yaml.nodes import ScalarNode

    if isinstance(node, ScalarNode):
        return node.value
    return ""


def _declares_timeout(node: Node | None) -> bool:
    """True when a mapping declares any ``timeout-minutes`` value at all.

    An expression such as ``${{ matrix.timeout }}`` still declares a limit the
    job's owner chose; judging its value is not this check's business.
    """
    timeout, _line = find(node, "timeout-minutes")
    return timeout is not None and _scalar(timeout).strip() != ""


def _unbounded_installs(text: str) -> list[tuple[int, str]]:
    """(0-based line offset in text, command label) for every unbounded install.

    A command continued with ``\\`` is read as one line, reported on its first:
    ``timeout 300 \\`` bounds the install on the next line.
    """
    found: list[tuple[int, str]] = []
    lines = text.splitlines()
    offset = 0
    while offset < len(lines):
        start, command = offset, lines[offset]
        # A comment ending in a backslash does not continue onto the next line.
        while command.rstrip().endswith("\\") and not command.lstrip().startswith("#") \
                and offset + 1 < len(lines):
            offset += 1
            command = command.rstrip()[:-1] + " " + lines[offset]
        offset += 1
        if _NON_COMMAND.match(command) or _BOUNDED_BY_TIMEOUT.search(command):
            continue
        found.extend((start, label) for label, pattern in INSTALL_COMMANDS if pattern.search(command))
    return found


def _job_label(job_key: str) -> str:
    return f"job {job_key!r}"


def _allowlisted(
    allowlist: frozenset[tuple[str, str, str]], repository: str, path: str, job_key: str
) -> bool:
    repo_key = repository.lower()
    return any(
        (repo == WILDCARD or (repo_key != "" and repo == repo_key))
        and (workflow == WILDCARD or workflow == path)
        and (job == WILDCARD or job == job_key)
        for repo, workflow, job in allowlist
    )


def _step_label(index: int, step: Node | None) -> str:
    name, _line = find(step, "name")
    label = _scalar(name).strip()
    return f"step {label!r}" if label else f"step #{index + 1}"


def _check_jobs(
    doc: Node, path: str, repository: str, allowlist: frozenset[tuple[str, str, str]], findings: list[Finding]
) -> None:
    from yaml.nodes import SequenceNode

    jobs, _jobs_line = find(doc, "jobs")
    for job_key, job, _job_line in items(jobs):
        uses, _uses_line = find(job, "uses")
        if uses is not None:
            # A reusable-workflow caller job cannot declare ``timeout-minutes``
            # — workflow syntax bans the key there — and the called workflow
            # bounds its own jobs.
            continue
        allowed = _allowlisted(allowlist, repository, path, job_key)
        if _declares_timeout(job):
            continue
        steps, _steps_line = find(job, "steps")
        if not isinstance(steps, SequenceNode):
            continue
        for index, step in enumerate(steps.value):
            if _declares_timeout(step):
                continue
            run, _run_line = find(step, "run")
            if run is None:
                continue  # a `uses:` step installs nothing itself
            # The first content line of a block scalar (``run: |``) is the line
            # after the key; a plain scalar shares the key's line.
            block = getattr(run, "style", None) in ("|", ">")
            run_start = run.start_mark.line + 1 + (1 if block else 0)
            for offset, label in _unbounded_installs(_scalar(run)):
                if allowed:
                    continue
                findings.append(
                    Finding(
                        "install-unbounded",
                        path,
                        run_start + offset,
                        f"{_job_label(job_key)} {_step_label(index, step)} runs `{label}` "
                        "with no timeout-minutes on the step or the job. An install that "
                        "stalls holds the job until its limit (360 minutes with none "
                        "declared); the automancer-ops release check died this way on "
                        "19 Oct 2026. Add `timeout-minutes:` to the step (or the job). A "
                        "deliberate exception is a row in install-timeout-allowlist.tsv "
                        "in Automancer-Ltd/estate-checks.",
                    )
                )


def load_allowlist(path: str) -> frozenset[tuple[str, str, str]]:
    """(repo, workflow path, job id) triples from the allowlist TSV.

    Rows are ``repo <TAB> workflow <TAB> job <TAB> reason``; ``*`` wildcards any
    field. A row without a reason, or with the wrong column count, raises: an
    allowlist that cannot be read precisely must not become a silent pass.
    """
    entries: set[tuple[str, str, str]] = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        raise AllowlistError(f"allowlist file not found at {path}") from None
    except OSError as exc:
        raise AllowlistError(f"allowlist file unreadable: {exc}") from None
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        columns = [column.strip() for column in line.split("\t")]
        if len(columns) != 4 or not all(columns):
            raise AllowlistError(
                f"allowlist row {number} is not `repo<TAB>workflow<TAB>job<TAB>reason`: {stripped!r}"
            )
        repo, workflow, job, _reason = columns
        entries.add((repo.lower(), workflow, job))
    return frozenset(entries)


def check_workflow(
    path: str,
    text: str,
    repository: str,
    allowlist: frozenset[tuple[str, str, str]],
) -> list[Finding]:
    """Every finding for one workflow file, in file order."""
    yaml = load_yaml()
    if yaml is None:
        return [
            Finding(
                "check-could-not-run",
                path,
                1,
                "PyYAML is not importable here, so this workflow's install steps were not checked.",
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
                f"This workflow could not be parsed ({first}), so its install steps are "
                "not proven bounded.",
            )
        ]
    for doc in docs:
        if not isinstance(doc, MappingNode):
            findings.append(
                Finding(
                    "workflow-unparsable",
                    path,
                    doc.start_mark.line + 1,
                    "This file is not a YAML mapping, so its install steps are not proven bounded.",
                )
            )
            continue
        _check_jobs(doc, path, repository, allowlist, findings)
    return findings


def scan(
    root: str,
    workflows_dir: str,
    repository: str,
    allowlist: frozenset[tuple[str, str, str]],
) -> list[Finding]:
    return workflow_yaml.scan(root, workflows_dir, lambda path, text: check_workflow(path, text, repository, allowlist))


def emit_annotations(findings: list[Finding]) -> None:
    for finding in findings:
        title = TITLES.get(finding.rule, finding.rule)
        print(f"::error file={finding.path},line={finding.line},title={title}::{escape(finding.message)}")


def emit_summary(findings: list[Finding], summary_path: str | None) -> None:
    if not summary_path:
        return
    lines = [
        "## Install timeouts: "
        + (f"{len(findings)} unbounded install step(s)" if findings else "no unbounded installs"),
        "",
        "Failing check of ``.github/workflows``: a step that installs packages "
        "(`pip install`, `uv pip install`, `npm ci`/`npm install`, `apt-get install`, "
        "`go install`) must declare `timeout-minutes` on the step or its job. This "
        "check fails the run — an unbounded install once held the automancer-ops "
        "release check until the job was killed (19 Oct 2026). Deliberate exceptions "
        "are rows in `install-timeout-allowlist.tsv` in Automancer-Ltd/estate-checks.",
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
        lines.append("Every package install in this repository's workflows is bounded.")
    lines.append("")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail on workflow steps that install packages with no timeout."
    )
    parser.add_argument("--root", default=os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--allowlist", default=DEFAULT_ALLOWLIST)
    parser.add_argument("--json", action="store_true", help="print findings as JSON and nothing else")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    workflows_dir = os.path.join(root, workflow_yaml.WORKFLOWS_DIR)
    could_not_run = 0
    try:
        allowlist = load_allowlist(args.allowlist)
    except AllowlistError as exc:
        allowlist: frozenset[tuple[str, str, str]] = frozenset()
        could_not_run = 1
        print(f"::error title={TITLES['check-could-not-run']}::{escape(str(exc))}")
        print(f"install-timeout check could not run: {exc}", file=sys.stderr)
    findings = scan(root, workflows_dir, args.repository, allowlist)
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
        return 1 if findings else could_not_run
    emit_annotations(findings)
    emit_summary(findings, args.summary)
    print(
        f"Install timeouts: {len(findings)} unbounded install step(s) across "
        f"{len(workflow_files(workflows_dir))} workflow file(s)."
    )
    print(f"install-timeout-count={len(findings)}")
    # A gate: findings fail the run, and so does a check that could not run.
    return 1 if findings or could_not_run else 0


if __name__ == "__main__":
    sys.exit(main())
