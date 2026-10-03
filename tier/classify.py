#!/usr/bin/env python3
"""Choose how much CI a change needs: none, core or standard.

Proportionate CI (Waseem, 2026-10-03). Read by the `tier` composite action; see
tier/README.md for the contract. Standard library only, so the job needs no
setup step. Every failure resolves to `standard`: this script may make CI
cheaper only when it is sure, never by accident.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable

NONE, CORE, STANDARD = "none", "core", "standard"
NOTICE_TITLE = "ci-tier"
API_TIMEOUT_S = 15


@dataclass(frozen=True)
class ChangedFile:
    path: str
    lines: int
    previous_path: str | None = None


@dataclass
class Config:
    docs: list[str] = field(default_factory=list)
    docs_exclude: list[str] = field(default_factory=list)
    full: list[str] = field(default_factory=list)
    small_max_files: int = 3
    small_max_lines: int = 50


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Match a repo-relative path. `**` crosses directories, `*` and `?` do not.

    A pattern is anchored at the repo root: `*.md` matches only root markdown,
    `docs/**/*.md` matches markdown anywhere under docs/.
    """
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_list(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n]", raw or "") if p.strip() and not p.strip().startswith("#")]


def _matches(path: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(p.match(path) for p in patterns)


def classify(files: list[ChangedFile], cfg: Config, labels: Iterable[str] = ()) -> tuple[str, str]:
    """Return (tier, reason) for a set of changed files."""
    labels = set(labels)
    if "ci:full" in labels:
        return STANDARD, "label ci:full"
    if not files:
        return STANDARD, "no changed files reported"
    docs = [glob_to_regex(p) for p in cfg.docs]
    excluded = [glob_to_regex(p) for p in cfg.docs_exclude]
    full = [glob_to_regex(p) for p in cfg.full]

    def paths(f: ChangedFile) -> list[str]:
        return [f.path] + ([f.previous_path] if f.previous_path else [])

    for f in files:
        hit = next((p for p in paths(f) if _matches(p, full)), None)
        if hit:
            return STANDARD, f"{hit} always needs the full suite"

    def is_doc(f: ChangedFile) -> bool:
        return all(_matches(p, docs) and not _matches(p, excluded) for p in paths(f))

    code = [f for f in files if not is_doc(f)]
    if not code:
        return NONE, f"all {len(files)} changed file(s) are documentation"
    lines = sum(f.lines for f in code)
    if len(code) <= cfg.small_max_files and lines <= cfg.small_max_lines:
        return CORE, f"small change: {len(code)} file(s), {lines} line(s)"
    return STANDARD, f"{len(code)} file(s), {lines} line(s) changed"


class Api:
    def __init__(self, base: str, repo: str, token: str):
        self.base, self.repo, self.token = base.rstrip("/"), repo, token

    def get(self, path: str) -> tuple[object, dict[str, str]]:
        url = path if path.startswith("http") else f"{self.base}/repos/{self.repo}/{path}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as resp:
            return json.load(resp), dict(resp.headers)

    def paged(self, path: str, key: str | None = None, limit: int = 3000) -> list[dict]:
        items: list[dict] = []
        url: str | None = path + ("&" if "?" in path else "?") + "per_page=100"
        while url and len(items) < limit:
            body, headers = self.get(url)
            items.extend(body[key] if key else body)  # type: ignore[index]
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", "") or headers.get("link", ""))
            url = nxt.group(1) if nxt else None
        return items


def _files(raw: list[dict]) -> list[ChangedFile]:
    return [ChangedFile(f["filename"], int(f.get("additions", 0)) + int(f.get("deletions", 0)), f.get("previous_filename"))
            for f in raw]


def tree_of(api: Api, sha: str) -> str:
    body, _ = api.get(f"git/commits/{sha}")
    return body["tree"]["sha"]  # type: ignore[index]


def tested_tree_of_run(api: Api, run_id: int, job_name: str) -> str | None:
    """The tree a passing PR run recorded in its tier job's notice annotation."""
    jobs = api.paged(f"actions/runs/{run_id}/jobs?filter=latest", key="jobs")
    for job in jobs:
        if job.get("name") != job_name:
            continue
        notes = api.paged(f"check-runs/{job['id']}/annotations")
        for note in notes:
            m = re.search(r"tested-tree=([0-9a-f]{40})", note.get("message", ""))
            if note.get("title") == NOTICE_TITLE and m:
                return m.group(1)
    return None


def already_tested(api: Api, after: str, workflow_path: str, job_name: str) -> str | None:
    """If the pushed tree is exactly a tree a PR run passed, say which PR."""
    pulls, _ = api.get(f"commits/{after}/pulls")
    merged = [p for p in pulls if p.get("merged_at") and p.get("merge_commit_sha") == after]  # type: ignore[union-attr]
    if not merged:
        return None
    pr = merged[0]
    workflow = os.path.basename(workflow_path)
    runs = api.paged(f"actions/workflows/{workflow}/runs?event=pull_request&status=success&head_sha={pr['head']['sha']}",
                     key="workflow_runs", limit=20)
    pushed_tree = tree_of(api, after)
    for run in runs:
        if tested_tree_of_run(api, run["id"], job_name) == pushed_tree:
            return f"main's tree is exactly what PR #{pr['number']} passed in run {run['id']}"
    return None


def decide(event_name: str, event: dict, cfg: Config, api: Api, github_sha: str,
           workflow_path: str, job_name: str, default_branch_ref: str) -> tuple[str, str, str | None]:
    """Return (tier, reason, tested_tree). tested_tree is recorded on PR runs."""
    if event_name == "pull_request":
        pr = event["pull_request"]
        labels = [label["name"] for label in pr.get("labels", [])]
        files = _files(api.paged(f"pulls/{pr['number']}/files"))
        tier, reason = classify(files, cfg, labels)
        return tier, reason, tree_of(api, github_sha)
    if event_name == "push" and event.get("ref") == default_branch_ref:
        before, after = event.get("before", ""), event.get("after", "")
        if not before or set(before) == {"0"} or event.get("forced"):
            return STANDARD, "new or force-pushed branch", None
        skip = already_tested(api, after, workflow_path, job_name)
        if skip:
            return NONE, skip, None
        body, _ = api.get(f"compare/{before}...{after}")
        raw = body.get("files", [])  # type: ignore[union-attr]
        if len(raw) >= 300:
            return STANDARD, "300+ files changed (compare API limit)", None
        tier, reason = classify(_files(raw), cfg)
        return tier, reason, None
    return STANDARD, f"event {event_name} always runs the full suite", None


def main(argv: list[str], environ: dict[str, str] = os.environ,
         make_api: Callable[[str, str, str], Api] = Api) -> int:
    cfg = Config(
        docs=parse_list(environ.get("INPUT_DOCS_PATHS", "")),
        docs_exclude=parse_list(environ.get("INPUT_DOCS_EXCLUDE", "")),
        full=parse_list(environ.get("INPUT_FULL_PATHS", "")),
        small_max_files=int(environ.get("INPUT_SMALL_MAX_FILES") or 3),
        small_max_lines=int(environ.get("INPUT_SMALL_MAX_LINES") or 50),
    )
    tested_tree = None
    try:
        with open(environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            event = json.load(fh)
        api = make_api(environ.get("GITHUB_API_URL", "https://api.github.com"),
                       environ["GITHUB_REPOSITORY"], environ["INPUT_TOKEN"])
        default_ref = "refs/heads/" + (event.get("repository", {}).get("default_branch") or "main")
        tier, reason, tested_tree = decide(environ.get("GITHUB_EVENT_NAME", ""), event, cfg, api,
                                           environ.get("GITHUB_SHA", ""), environ.get("GITHUB_WORKFLOW_REF", "").split("@")[0],
                                           environ.get("INPUT_JOB_NAME", "tier"), default_ref)
    except Exception as exc:  # noqa: BLE001 — any surprise must fail toward running the suite
        tier, reason = STANDARD, f"could not classify ({type(exc).__name__}: {exc}); running the full suite"
        print(f"::warning title={NOTICE_TITLE}::{reason}")

    print(f"Tier: {tier} — {reason}")
    if tested_tree:
        print(f"::notice title={NOTICE_TITLE}::tier={tier} tested-tree={tested_tree}")
    out = environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"tier={tier}\nreason={reason}\n")
    summary = environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## CI tier: `{tier}`\n\n{reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
