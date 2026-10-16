# Estate Checks — Living Specification

Last verified: 2026-10-16 on automancer-vps

> Internal living spec. Update the relevant module section in the SAME branch
> as any behaviour change. Last verified against the code: 2026-10-16.

## North star

Give repositories a shared CI check for outside network and subprocess calls that have no time limit, and for the mechanical workflow rules of `ci-standards`, so both are reported by a guard rather than remembered from prose.

## Key principles

- Report new findings on pull requests and all findings on full scans.
- Warn by default. Allow callers to make findings and scan failures blocking.
- Keep findings precise. Exclude test files by default and allow path exclusions.
- Treat a scan that could not run as a visible warning, not a clean result.
- Keep Python bytecode caches untracked and ignored. Script runs must not change tracked cache files.

## Module map

### Composite action (`action.yml`)

- What it does: Sets up Semgrep, selects a pull request baseline or a full scan, applies exclusions, and runs the rule set. It supports custom rules, strict mode, and a findings count output. In the same job it runs the workflow standards check against the caller's own workflows and exposes its warning count. `check-workflows: 'false'` skips that second check.
- State: built
- Shaped by: Pull request differential scans, default warning behavior, and caller configuration in the action inputs.

### Unbounded call rules (`rules/unbounded-calls.yml`)

- What it does: Detects calls without a time limit in JavaScript, TypeScript, Python, shell scripts, and workflow steps. The rules cover common HTTP clients and subprocess calls.
- State: built
- Shaped by: The network and subprocess patterns in the rule fixtures and the precision limits in the README.

### Scan result processing (`scripts/generate-summary.py`)

- What it does: Rejects fatal or unexpectedly empty scan results, warns when exclusions hide recognizable server code, and deduplicates findings by rule and call site. It writes warning annotations, a job summary, and the findings count.
- State: built
- Shaped by: The action's warning and strict modes, differential scans, and the validation cases in `scripts/test-validation.py`.

### Workflow standards check (`scripts/workflow-standards.py`, `scripts/test-workflow-standards.py`)

- What it does: Reads the caller's `.github/workflows/*.yml` and annotates breaches of `ci-standards` rules 1-4 — a job with no `timeout-minutes` or above the 30-minute cap, a workflow with no concurrency group, a `push:` without a branch filter or filtered away from the default branch, an organisation job whose `runs-on` is a hosted label literal instead of `vars.CI_RUNNER`, and an `on: schedule` trigger (`workflow-schedule-trigger`). A schedule is silent only when the workflow is on `HOSTED_MONITOR_SCHEDULES` and is a hosted monitor with no local dependencies (no checkout, no local action, no self-hosted runner). Warning only: it exits 0 with findings, and a workflow it cannot parse is reported as a warning rather than a clean result. Parses with `yaml.compose` so each annotation carries the line of the key at fault. The tests pin every rule against a fixture that must be flagged and one that must not.
- State: built
- Shaped by: The `ci-standards` rules, precision-over-recall (personal repositories, expressions, self-hosted labels, tag-only pushes and 10-30 minute timeouts are deliberately not judged), and Waseem's 2026-09-27 warn-only decision. Contradiction 7 of `/opt/automancer/auto/plans/repo-shaping-2026-10-15.md`.

### Repository CI (`.github/workflows/ci.yml`)

- What it does: One required `check` job runs Ruff, strict basedpyright on every production Python script, actionlint, Semgrep rule fixtures, script tests and the actual composite action. Warning and strict modes are exercised on pull requests. `scripts/check` is the local quick verdict; it runs only test files named by the caller. Tool versions and PyYAML stubs are locked in `uv.lock`. The project-local `verify-estate-checks` skill executes both action manifests against isolated synthetic consumers and retains their public outputs, annotations and logs. CI runs it and uploads its evidence. Hosted checkout and token behavior are separately proved in a disposable consumer PR before release.
- State: built
- Shaped by: The rule fixtures, validation tests, and the composite action's public inputs.

### CI tier action (`tier/`)

- What it does: Classifies a pull request or default-branch push as `none`, `core` or `standard` from the changed files, per-repo docs and full-suite globs, and size limits. On a default-branch push it skips when the pushed tree equals the tree the merged PR's passing run recorded. Any failure resolves to `standard`. Tests for Convex modules are also selected by generated `api.<path>` and `internal.<path>` references. If any changed Convex file has no matching test, the tier is `standard`, including when other changed files have tests. Optional `sparse-checkout` and `sparse-checkout-cone-mode` inputs control its conditional checkout. Defaults keep a full checkout with cone mode enabled. Consumers must retain all code, tests, docs and configuration the classifier reads.
- State: built
- Shaped by: Waseem's proportionate-CI decisions of 2026-10-03 (`auto/plans/ci-proportionate-2026-10-03.md`) and the cases in `tier/test_classify.py`.

## Open decisions

No open decisions are recorded in this repository.
