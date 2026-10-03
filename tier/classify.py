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
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable

NONE, CORE, STANDARD = "none", "core", "standard"
NOTICE_TITLE = "ci-tier"
API_TIMEOUT_S = 15


@dataclass(frozen=True)
class ChangedFile:
    path: str
    lines: int
    previous_path: str | None = None
    status: str = "modified"


# Estate defaults, so a repository normally declares nothing (Waseem chose
# zero-config rules, 2026-10-03). Repo inputs add to these lists; `docs-paths`
# replaces DEFAULT_DOCS, and `docs-paths: none` means nothing is docs.
DEFAULT_DOCS = ["**/*.md", "**/*.rst"]
# Markdown in these places is usually product, content or test data, not docs.
DEFAULT_DOCS_EXCLUDE = [
    "src/**", "**/src/**", "**/content/**", "**/_posts/**", "**/pages/**",
    "**/skills/**", "**/prompts/**", "**/templates/**", ".agents/**", ".claude/**",
    "**/public/**", "**/static/**", "**/fixtures/**", "**/__fixtures__/**",
    "**/testdata/**", "**/test/**", "**/tests/**", "**/__tests__/**",
]
# Inputs whose effect related-test selection cannot see.
DEFAULT_FULL = [
    ".github/**",
    "**/package.json", "**/package-lock.json", "**/npm-shrinkwrap.json", "**/pnpm-lock.yaml",
    "**/pnpm-workspace.yaml", "**/yarn.lock", "**/bun.lock", "**/bun.lockb", "**/.npmrc",
    "**/requirements*.txt", "**/pyproject.toml", "**/uv.lock", "**/poetry.lock", "**/Pipfile*",
    "**/Cargo.toml", "**/Cargo.lock", "**/go.mod", "**/go.sum", "**/Gemfile*",
    "**/.nvmrc", "**/.node-version", "**/.python-version", "**/.tool-versions",
    "**/Dockerfile*", "**/docker-compose*.yml", "**/docker-compose*.yaml", "**/compose*.yaml",
    "**/tsconfig*.json", "**/jsconfig*.json", "**/*.config.js", "**/*.config.ts",
    "**/*.config.mjs", "**/*.config.cjs", "**/*.config.mts", "**/.eslintrc*", "**/.oxlintrc*",
    "**/biome.json", "**/biome.jsonc", "**/.prettierrc*", "**/Makefile", "**/justfile",
    "**/migrations/**", "**/*.sql", "**/schema.prisma", "**/schema.ts", "**/schema.js",
    "**/convex.json", "**/vercel.json", "**/wrangler.toml", "**/wrangler.json*",
    "**/netlify.toml", "**/render.yaml", "**/fly.toml",
]
# Names too common to mean "this file" when they appear in code.
GENERIC_DOC_NAMES = {
    "readme.md", "changelog.md", "license.md", "contributing.md", "security.md",
    "code_of_conduct.md", "index.md", "agents.md", "claude.md", "spec.md", "notes.md",
    "todo.md", "plan.md", "summary.md", "report.md", "design.md",
}
DOC_SUFFIXES = (".md", ".rst")
# These list paths for a tool to skip or allow; they never read a doc's content.
NOT_READERS = {".gitignore", ".gitattributes", ".dockerignore", ".npmignore", ".prettierignore",
               ".eslintignore", ".gitleaks.toml", ".gitleaksignore", "CODEOWNERS"}
MAX_SCAN_BYTES = 2_000_000
DEFAULT_TEST_GLOBS = ["**/*.test.*", "**/*.spec.*", "**/*_test.*", "**/test_*.py", "**/__tests__/**"]
# A module with one of these names is imported by its folder: `engine/effects`.
FOLDER_MODULES = {"index", "__init__", "mod", "main"}


@dataclass
class Config:
    docs: list[str] = field(default_factory=lambda: list(DEFAULT_DOCS))
    docs_exclude: list[str] = field(default_factory=lambda: list(DEFAULT_DOCS_EXCLUDE))
    full: list[str] = field(default_factory=lambda: list(DEFAULT_FULL))
    small_max_files: int = 3
    small_max_lines: int = 50
    core_max_tests: int = 30
    test_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))

    @classmethod
    def from_inputs(cls, docs: str = "", docs_exclude: str = "", full: str = "",
                    small_max_files: str = "", small_max_lines: str = "",
                    core_max_tests: str = "", test_globs: str = "") -> "Config":
        declared = parse_list(docs)
        return cls(
            docs=[] if declared == ["none"] else (declared or list(DEFAULT_DOCS)),
            docs_exclude=DEFAULT_DOCS_EXCLUDE + parse_list(docs_exclude),
            full=DEFAULT_FULL + parse_list(full),
            small_max_files=int(small_max_files or 3),
            small_max_lines=int(small_max_lines or 50),
            core_max_tests=int(core_max_tests or 30),
            test_globs=parse_list(test_globs) or list(DEFAULT_TEST_GLOBS),
        )


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


def _paths(f: ChangedFile) -> list[str]:
    return [f.path] + ([f.previous_path] if f.previous_path else [])


def doc_candidates(files: list[ChangedFile], cfg: Config) -> list[str]:
    """Changed paths the globs call documentation, before checking who reads them."""
    docs = [glob_to_regex(p) for p in cfg.docs]
    excluded = [glob_to_regex(p) for p in cfg.docs_exclude]
    return sorted({p for f in files for p in _paths(f) if _matches(p, docs) and not _matches(p, excluded)})


def classify(files: list[ChangedFile], cfg: Config, labels: Iterable[str] = (),
             read_docs: Iterable[str] = ()) -> tuple[str, str]:
    """Return (tier, reason). A doc in `read_docs` is read by code, so it counts as code."""
    labels = set(labels)
    if "ci:full" in labels:
        return STANDARD, "label ci:full"
    if not files:
        return STANDARD, "no changed files reported"
    full = [glob_to_regex(p) for p in cfg.full]
    for f in files:
        hit = next((p for p in _paths(f) if _matches(p, full)), None)
        if hit:
            return STANDARD, f"{hit} always needs the full suite"

    docs = set(doc_candidates(files, cfg)) - set(read_docs)
    code = [f for f in files if not all(p in docs for p in _paths(f))]
    if not code:
        return NONE, f"all {len(files)} changed file(s) are documentation no code reads"
    lines = sum(f.lines for f in code)
    if len(code) <= cfg.small_max_files and lines <= cfg.small_max_lines:
        return CORE, f"small change: {len(code)} file(s), {lines} line(s)"
    return STANDARD, f"{len(code)} file(s), {lines} line(s) changed"


def reader_patterns(doc: str) -> list[tuple[str, re.Pattern[str]]]:
    """How code refers to a doc: its path, a distinctive file name, or its folder in quotes.

    Each pattern comes with the literal it must contain, so a plain substring
    test rules most files out before any regex runs. The folder must be at least
    two levels deep and directly followed by a quote (`"docs/reports"` or
    `'docs/reports/'`), which is how code lists a directory; a comment citing
    `docs/plans/x.md` does not make every plan code.
    """
    pats = [(doc, re.compile(re.escape(doc)))]
    name = doc.rsplit("/", 1)[-1]
    if "/" in doc and name.lower() not in GENERIC_DOC_NAMES:
        pats.append((name, re.compile(r"(?<![\w.-])" + re.escape(name))))
    parts = doc.split("/")[:-1]
    for depth in range(2, len(parts) + 1):
        folder = "/".join(parts[:depth])
        pats.append((folder, re.compile(re.escape(folder) + r"/?[\"'`]")))
    return pats


def docs_read_by_code(docs: list[str], root: str, tracked: list[str]) -> dict[str, str]:
    """Map each doc that a non-doc tracked file refers to onto one such file."""
    wanted = {d: reader_patterns(d) for d in docs}
    found: dict[str, str] = {}
    for rel in tracked:
        if rel.endswith(DOC_SUFFIXES) or os.path.basename(rel) in NOT_READERS or len(found) == len(wanted):
            continue
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > MAX_SCAN_BYTES:
                continue
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        text = code_lines(raw.decode("utf-8", "replace"))
        for doc, pats in wanted.items():
            if doc not in found and any(lit in text and rx.search(text) for lit, rx in pats):
                found[doc] = rel
    return found


# A line that starts with one of these is a comment in the languages the estate
# uses. A comment citing `docs/RUNBOOK.md` does not read it; `readFileSync(...)`
# or `cat docs/x.md` never starts a line with one of these.
COMMENT_PREFIXES = ("//", "#", "*", "/*", "<!--")


def code_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(COMMENT_PREFIXES))


def reference_keys(path: str) -> list[str]:
    """How a test imports `path`: `folder/name` (or `parent/folder` for an index);
    a root file or a dotfile by its full name (`deploy.json`, `ci/.eslintrc.json`),
    never a bare or empty word that would match every test."""
    parts = path.split("/")
    stem = parts[-1].split(".")[0]
    dirs = parts[:-1]
    if not dirs:
        return [parts[-1]]
    if not stem:
        return [f"{dirs[-1]}/{parts[-1]}"]
    key_parts = dirs[-2:] if stem in FOLDER_MODULES else dirs[-1:] + [stem]
    slash = "/".join(key_parts)
    return [slash, ".".join(key_parts)] if len(key_parts) > 1 else [slash]


RELATIVE_IMPORT = re.compile(r"""["'](\.{1,2}/[^"'\s]+)["']""")


def _imports(test: str, text: str, targets: set[str]) -> bool:
    """Does `test` import a target by a relative path (`./build`, `../lib/x.mjs`)?"""
    base = os.path.dirname(test)
    for spec in RELATIVE_IMPORT.findall(text):
        resolved = os.path.normpath(os.path.join(base, spec))
        if resolved in targets or os.path.splitext(resolved)[0] in targets:
            return True
    return False


def direct_tests(changed: list[str], tracked: list[str], test_globs: list[str], root: str) -> list[str]:
    """The tests that name a changed file, plus changed tests.

    A test names a file by folder and name (`lib/costEnvelope`), by a relative
    import that resolves to it (`./build`), or by sitting beside it as
    `<name>.test.*`. Following every import (vitest related, jest
    --findRelatedTests) selects most of a suite when a small change touches a
    widely imported module: in dungeon-master a one-line change to
    convex/lib/costEnvelope.ts ran past six minutes on one runner (2026-10-03).
    Typecheck covers every other caller, and the full suite runs on the next
    standard change.
    """
    globs = [glob_to_regex(g) for g in test_globs]
    tests = [t for t in tracked if any(g.match(t) for g in globs)]
    test_set = set(tests)
    chosen = {c for c in changed if c in test_set}
    sources = [c for c in changed if c not in chosen]
    patterns = [(k, re.compile(r"(?<![\w-])" + re.escape(k) + r"(?![\w-])"))
                for c in sources for k in reference_keys(c)]
    targets = {os.path.splitext(c)[0] for c in sources} | set(sources)
    targets |= {os.path.dirname(c) for c in sources if os.path.basename(c).split(".")[0] in FOLDER_MODULES}
    beside = {(os.path.dirname(c), os.path.basename(c).split(".")[0] + ".") for c in sources
              if os.path.basename(c).split(".")[0]}
    for t in tests:
        if t in chosen or not sources:
            continue
        t_dir, t_name = os.path.dirname(t), os.path.basename(t)
        if any(t_dir in (d, f"{d}/__tests__".lstrip("/")) and t_name.startswith(s) for d, s in beside):
            chosen.add(t)
            continue
        try:
            path = os.path.join(root, t)
            if os.path.getsize(path) > MAX_SCAN_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        if any(k in text and rx.search(text) for k, rx in patterns) or _imports(t, text, targets):
            chosen.add(t)
    return sorted(chosen)


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
    return [ChangedFile(f["filename"], int(f.get("additions", 0)) + int(f.get("deletions", 0)),
                        f.get("previous_filename"), f.get("status", "modified"))
            for f in raw]


def tree_of(api: Api, sha: str) -> str:
    body, _ = api.get(f"git/commits/{sha}")
    return body["tree"]["sha"]  # type: ignore[index]


def tested_tree_of_run(api: Api, run_id: int, job_name: str) -> str | None:
    """The tree a passing PR run recorded in its tier job's notice annotation.

    All attempts: re-running failed jobs copies the tier job into the new attempt
    without its annotations, so the record lives on the attempt that ran it."""
    jobs = api.paged(f"actions/runs/{run_id}/jobs?filter=all", key="jobs")
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


@dataclass
class Decision:
    tier: str
    reason: str
    tested_tree: str | None = None  # recorded on PR runs for the later push to main
    files: list[ChangedFile] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    direct_tests: list[str] = field(default_factory=list)  # core tier only


def decide(event_name: str, event: dict, cfg: Config, api: Api, github_sha: str,
           workflow_path: str, job_name: str, default_branch_ref: str) -> Decision:
    if event_name == "pull_request":
        pr = event["pull_request"]
        labels = [label["name"] for label in pr.get("labels", [])]
        files = _files(api.paged(f"pulls/{pr['number']}/files"))
        return Decision(*classify(files, cfg, labels), tree_of(api, github_sha), files, labels)
    if event_name == "push" and event.get("ref") == default_branch_ref:
        before, after = event.get("before", ""), event.get("after", "")
        if not before or set(before) == {"0"} or event.get("forced"):
            return Decision(STANDARD, "new or force-pushed branch")
        skip = already_tested(api, after, workflow_path, job_name)
        if skip:
            return Decision(NONE, skip)
        body, _ = api.get(f"compare/{before}...{after}")
        raw = body.get("files", [])  # type: ignore[union-attr]
        if len(raw) >= 300:
            return Decision(STANDARD, "300+ files changed (compare API limit)")
        files = _files(raw)
        return Decision(*classify(files, cfg), files=files)
    return Decision(STANDARD, f"event {event_name} always runs the full suite")


def _plan(environ: dict[str, str], cfg: Config, make_api: Callable[[str, str, str], Api]) -> Decision:
    with open(environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        event = json.load(fh)
    api = make_api(environ.get("GITHUB_API_URL", "https://api.github.com"),
                   environ["GITHUB_REPOSITORY"], environ["INPUT_TOKEN"])
    default_ref = "refs/heads/" + (event.get("repository", {}).get("default_branch") or "main")
    return decide(environ.get("GITHUB_EVENT_NAME", ""), event, cfg, api,
                  environ.get("GITHUB_SHA", ""), environ.get("GITHUB_WORKFLOW_REF", "").split("@")[0],
                  environ.get("INPUT_JOB_NAME") or "ci tier", default_ref)


def _scan(state: dict, cfg: Config, root: str) -> Decision:
    """Second phase, run in a checkout: docs that code refers to count as code, and
    a core change gets its tests named — or the full suite if too many name it."""
    files = [ChangedFile(**f) for f in state["files"]]
    tracked = [t for t in subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True,
                                         timeout=60).stdout.decode("utf-8", "replace").split("\0") if t]
    read = docs_read_by_code(doc_candidates(files, cfg), root, tracked)
    tier, reason = classify(files, cfg, state["labels"], read)
    if read:
        reason += "; docs read by code: " + ", ".join(f"{d} ({by})" for d, by in sorted(read.items()))
    tests: list[str] = []
    if tier == CORE:
        tests = direct_tests([f.path for f in files if f.status != "removed"], tracked, cfg.test_globs, root)
        if len(tests) > cfg.core_max_tests:
            tier, reason = STANDARD, (f"{reason}, but {len(tests)} test files name the changed code "
                                      f"(quick limit {cfg.core_max_tests}); running the full suite")
            tests = []
        else:
            reason += f"; {len(tests)} test file(s) name the changed code"
    return Decision(tier, reason, state["tested_tree"], files, state["labels"], tests)


def main(argv: list[str], environ: dict[str, str] = os.environ,
         make_api: Callable[[str, str, str], Api] = Api) -> int:
    """Phase `plan` decides from the API alone. A small change, or one that hangs on
    whether code reads a changed doc, saves its state and asks the action for a
    checkout; phase `scan` finishes there."""
    phase = environ.get("INPUT_PHASE") or "plan"
    state_path = os.path.join(environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "ci-tier-state.json")
    out = environ.get("GITHUB_OUTPUT")
    try:
        cfg = Config.from_inputs(environ.get("INPUT_DOCS_PATHS", ""), environ.get("INPUT_DOCS_EXCLUDE", ""),
                                 environ.get("INPUT_FULL_PATHS", ""), environ.get("INPUT_SMALL_MAX_FILES", ""),
                                 environ.get("INPUT_SMALL_MAX_LINES", ""), environ.get("INPUT_CORE_MAX_TESTS", ""),
                                 environ.get("INPUT_TEST_GLOBS", ""))
        if phase == "scan":
            with open(state_path, encoding="utf-8") as fh:
                d = _scan(json.load(fh), cfg, environ.get("GITHUB_WORKSPACE") or os.getcwd())
        else:
            d = _plan(environ, cfg, make_api)
            if d.tier == CORE or (d.tier == NONE and doc_candidates(d.files, cfg)):
                with open(state_path, "w", encoding="utf-8") as fh:
                    json.dump({"files": [asdict(f) for f in d.files], "labels": d.labels,
                               "tested_tree": d.tested_tree}, fh)
                print(f"Provisional tier {d.tier}; checking the code for doc readers and tests.")
                if out:
                    with open(out, "a", encoding="utf-8") as fh:
                        fh.write("needs-scan=true\n")
                return 0
    except Exception as exc:  # noqa: BLE001 — any surprise must fail toward running the suite
        d = Decision(STANDARD, f"could not classify ({type(exc).__name__}: {exc}); running the full suite")
        print(f"::warning title={NOTICE_TITLE}::{d.reason}")

    tier, reason = d.tier, d.reason
    print(f"Tier: {tier} — {reason}")
    if d.tested_tree:
        print(f"::notice title={NOTICE_TITLE}::tier={tier} tested-tree={d.tested_tree}")
    # Existing paths only: a deleted file has nothing to lint or test.
    changed = sorted({f.path for f in d.files if f.status != "removed"})
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"needs-scan=false\ntier={tier}\nreason={reason}\nchanged-files={json.dumps(changed)}\n"
                     f"direct-tests={json.dumps(d.direct_tests)}\n")
    summary = environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## CI tier: `{tier}`\n\n{reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
