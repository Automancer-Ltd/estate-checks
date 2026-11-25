#!/usr/bin/env python3
"""Workflow standards: the mechanical CI rules, reported as warnings.

The ci-standards skill's first four rules are structural and mechanical, so they
belong in a guard, not in 27 KB of incident prose. This check reads
``.github/workflows/*.yml`` in the repository it runs in and annotates:

1. ``job-missing-timeout`` / ``job-timeout-below-floor`` — every job declares
   ``timeout-minutes``, and none declares less than the 20-minute floor. A
   time limit only stops a stuck job; speed is a measured target, never
   enforced by cancelling (Waseem, 19 Nov 2026): a limit near the normal run
   time cancels honest cold runs and pays for re-runs. A literal is read
   directly; in an expression, each value chosen by ``&&`` or ``||`` (the
   ``cond && 30 || 20`` form) is read. Above the floor nothing is judged: an
   extended or release-proof job (30) and three times a measured cold run are
   both legitimate and invisible in YAML. A job that only calls a reusable
   workflow (``uses:``) is exempt: workflow syntax bans the key on a caller
   job, and the called workflow bounds its own jobs.
2. ``workflow-missing-concurrency`` / ``workflow-concurrency-no-group`` — every
   workflow declares a concurrency group.
3. ``push-unfiltered`` / ``push-wrong-branch`` — ``push:`` carries a branch
   filter and that filter includes the default branch. ``workflow-schedule-trigger``
   — ``on:`` does not include ``schedule``. Checks run once per change, on the
   pull request, and no workflow runs on a timer (ci-standards, 21 Oct 2026).
4. ``runner-label-literal`` — in an organisation repository, ``runs-on`` comes
   from ``vars.CI_RUNNER``, never a literal hosted label.

Warn-only (Waseem, 2026-09-27, via the estate-checks action): every finding is
an inline annotation and a job-summary line. This script never fails a job, even
when it finds something — a style violation in a workflow must not block a pull
request. A workflow that cannot be parsed is reported the same way: a warning,
because a check that could not run must never look like a clean result.

Reading and the finding type live in ``workflow_yaml``. Standard library plus PyYAML.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from typing import TYPE_CHECKING

import workflow_yaml
from workflow_yaml import Finding, escape, find, items, load_yaml, workflow_files

if TYPE_CHECKING:
    from yaml.nodes import Node

TIMEOUT_FLOOR_MINUTES = 20
# The values an expression can choose: ``cond && 30 || 20`` picks 30 or 20.
EXPRESSION_VALUE = re.compile(r"(?:&&|\|\|)\s*\(?\s*(\d+)\b")
# Personal repositories run on GitHub-hosted runners (ci-standards, rule 4).
PERSONAL_OWNERS = {"Waseemilyas"}
HOSTED_LABEL = re.compile(r"^(ubuntu|macos|windows|blacksmith)-")

OVERSIZED_RUNNER = re.compile(r"blacksmith-(?:8|16|32|64)vcpu", re.IGNORECASE)
# An expression that scales the runner to 8+ vCPU: contains "blacksmith" and a
# vcpu number of 8 or more, or a multiplication reaching it (e.g. 4vcpu * 2).
EXPRESSION_SCALED_VCPU = re.compile(
    r"blacksmith[^\"']*?(?:8|16|32|64)vcpu", re.IGNORECASE | re.DOTALL
)
DEPLOY_WORD = re.compile(r"deploy|release|publish", re.IGNORECASE)
JUSTIFICATION = re.compile(
    r"estate-checks-allow:\s*(pr-job-fanout|oversized-paid-runner|boat-missing-disk-ready|"
    r"pr-ready-for-review-trigger|unguarded-deploy-job)\b"
)

TITLES = {
    "pr-job-fanout": "More than 3 PR-eligible jobs in one workflow",
    "oversized-paid-runner": "Oversized paid runner on a PR-triggered workflow",
    "boat-missing-disk-ready": "vars.CI_RUNNER job lacks a disk-ready step",
    "pr-ready-for-review-trigger": "pull_request types include ready_for_review",
    "unguarded-deploy-job": "Deploy/release/publish job in a PR workflow without a job-level if",
    "job-missing-timeout": "Job declares no timeout-minutes",
    "job-timeout-below-floor": "Timeout below the 20-minute floor",
    "workflow-missing-concurrency": "Workflow declares no concurrency group",
    "workflow-concurrency-no-group": "Concurrency group is empty",
    "push-unfiltered": "push is not filtered to a branch",
    "push-wrong-branch": "push excludes the default branch",
    "runner-label-literal": "runs-on names a hosted runner label",
    "workflow-schedule-trigger": "Workflow declares a schedule trigger",
    "workflow-unparsable": "Workflow could not be checked",
}


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


def _triggered_on_pr(doc: Node) -> Node | None:
    """The pull_request trigger node when this workflow runs on pull requests, else None."""
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode

    triggers, _line = find(doc, "on")
    if isinstance(triggers, MappingNode):
        node, _ln = find(triggers, "pull_request")
        return node
    if isinstance(triggers, SequenceNode):
        for child in triggers.value:
            if isinstance(child, ScalarNode) and child.value == "pull_request":
                return child
        return None
    if isinstance(triggers, ScalarNode) and triggers.value == "pull_request":
        return triggers
    return None


def _pull_request_types_line(pr_node: Node | None) -> tuple[list[str], int]:
    """The on.pull_request.types values and the line of the types key, else ([], 0)."""
    if pr_node is None:
        return [], 0
    types_node, types_line = find(pr_node, "types")
    values = _scalars(types_node)
    if not values:
        # A single scalar `types: [x]` arrives as a sequence; a bare scalar too.
        return [], 0
    return values, types_line


def _job_if(job: Node) -> str:
    if_node, _line = find(job, "if")
    values = _scalars(if_node)
    return values[0] if values else ""


def _pr_excluded_by_if(condition: str) -> bool:
    """A job-level if that never runs on pull_request events (push/main-only)."""
    lowered = condition.lower().replace(" ", "")
    if not lowered or "pull_request" in lowered:
        return False  # no condition, or it mentions PRs, so it can run on one
    return "event_name=='push'" in lowered or "event_name!='pull_request'" in lowered


def _source_lines(text: str) -> list[str]:
    return text.splitlines()


def _justified(lines: list[str], rule: str, line: int) -> bool:
    """An inline justification comment on the key's own line or the two above it."""
    window = lines[max(0, line - 3):line]
    return any(JUSTIFICATION.search(raw) and rule in raw for raw in window)


def _check_pr_job_fanout(doc: Node, path: str, lines: list[str], findings: list[Finding]) -> None:
    if _triggered_on_pr(doc) is None:
        return
    jobs, jobs_line = find(doc, "jobs")
    if jobs is None:
        return
    eligible: list[tuple[str, int]] = []
    for job_key, job, job_line in items(jobs):
        uses, _uses_line = find(job, "uses")
        if uses is not None:
            continue  # reusable-workflow caller: one billed start for a bounded unit
        if _pr_excluded_by_if(_job_if(job)):
            continue
        eligible.append((job_key, job_line))
    if len(eligible) <= 3:
        return
    if any(_justified(lines, "pr-job-fanout", line) for _key, line in eligible):
        return
    names = ", ".join(repr(key) for key, _line in eligible)
    findings.append(
        Finding(
            "pr-job-fanout",
            path,
            eligible[3][1] if len(eligible) > 3 else jobs_line,
            f"This pull-request workflow defines {len(eligible)} PR-eligible jobs ({names}). "
            "Every job is a billed runner start; wide fan-out exhausts the shared boat.dev "
            "daily starts cap and evicts the whole org onto paid runners. Keep PR jobs at 3 "
            "or fewer: fold matrix-less jobs into one sequential job, or justify the split "
            "inline with `# estate-checks-allow: pr-job-fanout <reason>`.",
        )
    )


def _check_oversized_runner(doc: Node, path: str, lines: list[str], findings: list[Finding]) -> None:
    if _triggered_on_pr(doc) is None:
        return
    jobs, _jobs_line = find(doc, "jobs")
    if jobs is None:
        return
    for job_key, job, _job_line in items(jobs):
        uses, _uses_line = find(job, "uses")
        if uses is not None:
            continue
        if _pr_excluded_by_if(_job_if(job)):
            continue
        runs_on, runs_line = find(job, "runs-on")
        values: list[str] = _scalars(runs_on)
        hit: str | None = next((v for v in values if OVERSIZED_RUNNER.search(v)), None)
        if hit is None:
            hit = next(
                (v for v in values if "blacksmith" in v and EXPRESSION_SCALED_VCPU.search(v)),
                None,
            )
        if hit is None:
            continue
        if _justified(lines, "oversized-paid-runner", runs_line):
            continue
        findings.append(
            Finding(
                "oversized-paid-runner",
                path,
                runs_line,
                f"{_job_label(job_key)} runs pull-request checks on {hit.strip()!r}, an 8+ vCPU "
                "paid runner: billed minutes multiply 4x-16x against the 2vcpu default, and "
                "draft PR runs pay it too. PR checks belong on the smallest runner that fits "
                "(vars.CI_RUNNER); justify a big one inline with "
                "`# estate-checks-allow: oversized-paid-runner <reason>`.",
            )
        )


def _job_steps(job: Node) -> Node | None:
    steps, _line = find(job, "steps")
    return steps


def _step_text(step: Node) -> str:
    """The uses/run/shell-relevant text of one step mapping."""
    parts: list[str] = []
    for key in ("uses", "run"):
        node, _line = find(step, key)
        parts.extend(_scalars(node))
    return "\n".join(parts)


def _check_boat_disk_ready(doc: Node, path: str, findings: list[Finding]) -> None:
    jobs, _jobs_line = find(doc, "jobs")
    if jobs is None:
        return
    for job_key, job, job_line in items(jobs):
        uses, _uses_line = find(job, "uses")
        if uses is not None:
            continue
        runs_on, _runs_line = find(job, "runs-on")
        if not any("vars.CI_RUNNER" in v for v in _scalars(runs_on)):
            continue
        steps = _job_steps(job)
        if steps is None:
            continue
        from yaml.nodes import MappingNode, SequenceNode

        step_nodes = (
            [child for child in steps.value if isinstance(child, MappingNode)]
            if isinstance(steps, SequenceNode)
            else []
        )
        texts = [_step_text(step) for step in step_nodes]
        if any("estate-checks/disk-ready" in t or re.search(r"uses.*(^|[/\s])\./disk-ready\b", t) or "./disk-ready" in t for t in texts):
            continue
        runs_repo_commands = any(
            "actions/checkout" in t
            or re.search(r"(^|\n)\s*(?!uses\b)(\./|scripts/|bash\s|sh\s|python\d*\s|uv\s|npm\s|npx\s|make\s)", t)
            for t in texts
        )
        if not runs_repo_commands:
            continue  # a tiny gate job running no repo commands never touches the disk
        findings.append(
            Finding(
                "boat-missing-disk-ready",
                path,
                job_line,
                f"{_job_label(job_key)} reads vars.CI_RUNNER, so it can land on a boat.dev "
                "runner, but no step uses Automancer-Ltd/estate-checks/disk-ready before the "
                "work. A boat job can start while the snapshot disk is still swapping in and "
                "die with uv_cwd / 'Unable to read current working directory'. Put "
                "`- uses: Automancer-Ltd/estate-checks/disk-ready@v1` as the first step, "
                "before actions/checkout.",
            )
        )


def _check_ready_for_review(doc: Node, path: str, findings: list[Finding]) -> None:
    pr = _triggered_on_pr(doc)
    if pr is None:
        return
    from yaml.nodes import MappingNode

    if not isinstance(pr, MappingNode):
        return  # bare `on: pull_request` or sequence form: all default types, no types key
    types, types_line = _pull_request_types_line(pr)
    if "ready_for_review" not in types:
        return
    findings.append(
        Finding(
            "pr-ready-for-review-trigger",
            path,
            types_line,
            "`on.pull_request.types` includes ready_for_review, so marking a draft PR ready "
            "pays a second full run even though the draft already ran the required checks "
            "(opened/synchronize cover every pushed commit). Drop ready_for_review from the "
            "list, or drop types entirely for the default set.",
        )
    )


def _check_unguarded_deploy(doc: Node, path: str, findings: list[Finding]) -> None:
    if _triggered_on_pr(doc) is None:
        return
    jobs, _jobs_line = find(doc, "jobs")
    if jobs is None:
        return
    for job_key, job, job_line in items(jobs):
        uses, _uses_line = find(job, "uses")
        if uses is not None:
            continue
        name_node, _name_line = find(job, "name")
        names = [job_key] + _scalars(name_node)
        if not any(DEPLOY_WORD.search(n) for n in names):
            continue
        if_node, _if_line = find(job, "if")
        if if_node is not None and _scalars(if_node):
            continue
        findings.append(
            Finding(
                "unguarded-deploy-job",
                path,
                job_line,
                f"{_job_label(job_key)} looks like a deploy/release/publish job in a workflow "
                "that runs on pull requests, but declares no job-level `if:`. The runner "
                "still starts on every PR — paying for a runner that only skips its steps. "
                "Guard the job itself, e.g. `if: github.event_name == 'push' && "
                "github.ref == refs/heads/main'`.",
            )
        )


def _check_jobs(doc: Node, path: str, owner: str, findings: list[Finding]) -> None:
    jobs, _jobs_line = find(doc, "jobs")
    if jobs is None:
        return
    for job_key, job, job_line in items(jobs):
        uses, _uses_line = find(job, "uses")
        if uses is not None:
            # A reusable-workflow caller job cannot declare ``timeout-minutes``
            # or ``runs-on`` — workflow syntax bans both keys there — and the
            # called workflow bounds its own jobs, so neither rule applies.
            continue
        timeout, timeout_line = find(job, "timeout-minutes")
        if timeout is None:
            findings.append(
                Finding(
                    "job-missing-timeout",
                    path,
                    job_line,
                    f"{_job_label(job_key)} declares no timeout-minutes. Add "
                    "`timeout-minutes:` to stop a stuck job: 20, 30 for an extended or "
                    "release-proof job, or three times the measured cold run when that is "
                    "more; with none declared GitHub allows 360 minutes.",
                )
            )
        else:
            values = _scalars(timeout)
            raw = (values[0] if values else "").strip()
            if raw.isdigit():
                chosen = [int(raw)]
            elif "${{" in raw:
                chosen = [int(v) for v in EXPRESSION_VALUE.findall(raw)]
            else:
                chosen = []
            low = [v for v in chosen if v < TIMEOUT_FLOOR_MINUTES]
            if low:
                findings.append(
                    Finding(
                        "job-timeout-below-floor",
                        path,
                        timeout_line,
                        f"{_job_label(job_key)} can set timeout-minutes to {min(low)}, below "
                        f"the {TIMEOUT_FLOOR_MINUTES}-minute floor. A time limit only stops a "
                        "stuck job and sits well above the slowest honest run, cold cache "
                        "included: 20, 30 for an extended or release-proof job, or three times "
                        "the measured cold run when that is more. Five minutes is a target for "
                        "a warm run, measured, never enforced by cancelling.",
                    )
                )

        runs_on, runs_line = find(job, "runs-on")
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
    concurrency, line = find(doc, "concurrency")
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
    group, group_line = find(concurrency, "group")
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

    triggers, _line = find(doc, "on")
    if triggers is None:
        return
    event_names = []
    if isinstance(triggers, MappingNode):
        event_names = [key for key, _value, _line in items(triggers)]
        push, push_line = find(triggers, "push")
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
    keys = [key for key, _value, _line in items(push)]
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
    branches_node, branches_line = find(push, "branches")
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


def _schedule_line(triggers: Node | None) -> int | None:
    """Line of an ``on: schedule`` trigger, else None. Comments are not triggers."""
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode

    if isinstance(triggers, MappingNode):
        node, line = find(triggers, "schedule")
        return line if node is not None else None
    if isinstance(triggers, SequenceNode):
        for child in triggers.value:
            if isinstance(child, ScalarNode) and child.value == "schedule":
                return child.start_mark.line + 1
        return None
    if isinstance(triggers, ScalarNode) and triggers.value == "schedule":
        return triggers.start_mark.line + 1
    return None


def _check_schedule(doc: Node, path: str, findings: list[Finding]) -> None:
    triggers, _line = find(doc, "on")
    line = _schedule_line(triggers)
    if line is None:
        return
    message = (
        "`on: schedule` runs this workflow on a timer. ci-standards (21 Oct 2026): "
        "no scheduled CI for any repository; checks run once per change, on the pull "
        "request. Move the work to `workflow_dispatch`; operations that must run on a "
        "clock (uptime, backups) belong on a host timer or UptimeRobot, not in CI."
    )
    findings.append(Finding("workflow-schedule-trigger", path, line, message))


def check_workflow(
    path: str,
    text: str,
    owner: str,
    default_branch: str,
) -> list[Finding]:
    """Every finding for one workflow file, in file order."""
    yaml = load_yaml()
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
        lines = _source_lines(text)
        _check_jobs(doc, path, owner, findings)
        _check_concurrency(doc, path, findings)
        _check_push(doc, path, default_branch, findings)
        _check_schedule(doc, path, findings)
        _check_pr_job_fanout(doc, path, lines, findings)
        _check_oversized_runner(doc, path, lines, findings)
        _check_boat_disk_ready(doc, path, findings)
        _check_ready_for_review(doc, path, findings)
        _check_unguarded_deploy(doc, path, findings)
    return findings


def scan(root: str, workflows_dir: str, owner: str, default_branch: str) -> list[Finding]:
    return workflow_yaml.scan(root, workflows_dir, lambda path, text: check_workflow(path, text, owner, default_branch))


def emit_annotations(findings: list[Finding]) -> None:
    for finding in findings:
        title = TITLES.get(finding.rule, finding.rule)
        print(f"::warning file={finding.path},line={finding.line},title={title}::{escape(finding.message)}")


def emit_summary(findings: list[Finding], summary_path: str | None) -> None:
    if not summary_path:
        return
    lines = [
        "## Workflow standards: "
        + (f"{len(findings)} warning(s)" if findings else "no warnings"),
        "",
        (
            "Warn-only check of ci-standards rules 1-4 (job timeouts, concurrency groups, a "
            "filtered `push:`, `runs-on` from `vars.CI_RUNNER`, and no `on: schedule`). "
            "It never fails this job."
        ),
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
        lines.append("Every workflow in this repository satisfies the mechanical rules.")
    lines.append("")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Warn about ci-standards workflow rules in this repo's workflows."
    )
    parser.add_argument("--root", default=os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    parser.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    parser.add_argument("--default-branch", default="")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    workflows_dir = os.path.join(root, workflow_yaml.WORKFLOWS_DIR)
    findings = scan(root, workflows_dir, args.owner, args.default_branch)
    emit_annotations(findings)
    emit_summary(findings, args.summary)
    print(
        f"Workflow standards: {len(findings)} warning(s) across "
        f"{len(workflow_files(workflows_dir))} workflow file(s)."
    )
    # Machine-readable count for the action step's output.
    print(f"workflow-standards-count={len(findings)}")
    return 0  # warn-only: this check never fails the job


if __name__ == "__main__":
    sys.exit(main())
