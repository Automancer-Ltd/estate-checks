# Estate Checks — Living Specification

Last verified: 2026-10-21 on auto-vps

> Internal living spec. Update the relevant module section in the SAME branch
> as any behaviour change. Last verified against the code: 2026-10-21.

## North star

Give repositories a shared CI check for outside network and subprocess calls that have no time limit, for the mechanical workflow rules of `testing` (CI area), and for workflow steps that install packages with no `timeout-minutes`, so all three are reported by a guard rather than remembered from prose.

## Key principles

- Report new findings on pull requests and all findings on full scans.
- Warn by default. Allow callers to make findings and scan failures blocking.
- Keep findings precise. Exclude test files by default and allow path exclusions.
- Treat a scan that could not run as a visible warning, not a clean result.
- Keep Python bytecode caches untracked and ignored. Script runs must not change tracked cache files.

## Module map

### Composite action (`action.yml`)

- What it does: Sets up Semgrep, selects a pull request baseline or a full scan, applies exclusions, and runs the rule set. It supports custom rules, strict mode, and a findings count output. In the same job it runs the workflow standards check against the caller's own workflows and exposes its warning count. `check-workflows: 'false'` skips that second check. A third step runs the install-timeout gate, which fails the run on an unbounded package install; `check-installs: 'false'` skips it, and its finding count is the `install-timeout-count` output.
- State: built
- Shaped by: Pull request differential scans, default warning behavior, caller configuration in the action inputs, and tracker t4456 for the blocking install gate.

### Unbounded call rules (`rules/unbounded-calls.yml`)

- What it does: Detects calls without a time limit in JavaScript, TypeScript, Python, shell scripts, and workflow steps. The rules cover common HTTP clients and subprocess calls.
- State: built
- Shaped by: The network and subprocess patterns in the rule fixtures and the precision limits in the README.

### Scan result processing (`scripts/generate-summary.py`)

- What it does: Rejects fatal or unexpectedly empty scan results, warns when exclusions hide recognizable server code, and deduplicates findings by rule and call site. It writes warning annotations, a job summary, and the findings count.
- State: built
- Shaped by: The action's warning and strict modes, differential scans, and the validation cases in `scripts/test-validation.py`.

### Workflow standards check (`scripts/workflow-standards.py`, `scripts/test-workflow-standards.py`; YAML reading shared in `scripts/workflow_yaml.py`)

- What it does: Reads the caller's `.github/workflows/*.yml` and annotates breaches of `testing` CI rules 1-4 — a job with no `timeout-minutes` or above the 10-minute cap, a workflow with no concurrency group, a `push:` without a branch filter or filtered away from the default branch, an organisation job whose `runs-on` is a hosted label literal instead of `vars.CI_RUNNER`, and an `on: schedule` trigger (`workflow-schedule-trigger`). A schedule has no exception (ci-standards, 21 Oct 2026). Warning only: it exits 0 with findings, and a workflow it cannot parse is reported as a warning rather than a clean result. Parses with `yaml.compose` so each annotation carries the line of the key at fault. The tests pin every rule against a fixture that must be flagged and one that must not.
- State: built
- Shaped by: The `ci-standards` rules, precision-over-recall (personal repositories, expressions, self-hosted labels, tag-only pushes and 5-10 minute timeouts are deliberately not judged), and Waseem's 2026-09-27 warn-only decision. Contradiction 7 of `/opt/automancer/auto/plans/repo-shaping-2026-10-15.md`.

### Install timeout check (`scripts/install-timeouts.py`, `scripts/test-install-timeouts.py`, `install-timeout-allowlist.tsv`; YAML reading shared in `scripts/workflow_yaml.py`)

- What it does: Reads the caller's `.github/workflows/*.yml` and fails the run when a `run:` step executes a package install — `pip install` (and `pip3`, `python -m pip`), `uv pip install`, `npm ci`/`npm install`, `apt-get install`/`apt install`, `go install` — while neither the step nor its job declares `timeout-minutes`. Deliberate exceptions are rows (`repo`, workflow path, job id, reason; `*` wildcards) in the shipped `install-timeout-allowlist.tsv`; a malformed row, a missing PyYAML or an unparsable workflow fails loudly rather than passing silently. Parses with `yaml.compose` and annotates on the install command's line.
- State: built
- Shaped by: tracker t4456 (the automancer-ops release check killed 4m38s into an unbounded `pip` install, 19 Oct 2026, run 39332563829) and precision-over-recall: comments, `echo` lines, `timeout`-wrapped commands (with options or a `\`-continued line), expression timeouts and reusable-workflow caller jobs are not findings.

### Repository CI (`.github/workflows/ci.yml`)

- What it does: One required `check` job that always reports. It runs `scripts/check` (the local quick verdict) on every script test, the Semgrep rule fixtures, the scripted path (`verify-estate-checks`, evidence uploaded), and the composite action on this repository. A disposable consumer PR proves hosted behaviour before a release.
- State: built
- Shaped by: The rule fixtures, validation tests, and the composite action's public inputs.

### CI tier action (`tier/`)

- What it does: Classifies a pull request or default-branch push as `none`, `core` or `standard` from the changed files, docs and full-suite globs, test setup a test-runner config names, and size limits; on a push it reuses the merged head's passing run (see `tier/README.md`, which owns every rule and input). Any failure resolves to `standard`. Estate and secret scans stay the consumer's unconditional steps.
- State: built
- Shaped by: Waseem's proportionate-CI decisions of 2026-10-03 (`auto/plans/ci-proportionate-2026-10-03.md`) and the cases in `tier/test_classify.py`.

## Open decisions

No open decisions are recorded in this repository.
