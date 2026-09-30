# Estate Checks — Living Specification

> Internal living spec. Update the relevant module section in the SAME branch
> as any behaviour change. Last verified against the code: 2026-09-28.

## North star

Give repositories a shared CI check for outside network and subprocess calls that have no time limit.

## Key principles

- Report new findings on pull requests and all findings on full scans.
- Warn by default. Allow callers to make findings and scan failures blocking.
- Keep findings precise. Exclude test files by default and allow path exclusions.
- Treat a scan that could not run as a visible warning, not a clean result.
- Keep Python bytecode caches untracked and ignored. Script runs must not change tracked cache files.

## Module map

### Composite action (`action.yml`)

- What it does: Sets up Semgrep, selects a pull request baseline or a full scan, applies exclusions, and runs the rule set. It supports custom rules, strict mode, and a findings count output.
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

### Repository CI (`.github/workflows/ci.yml`)

- What it does: Tests Semgrep rules, validates the action and workflow, runs script self-tests, and checks warning and strict modes on pull requests.
- State: built
- Shaped by: The rule fixtures, validation tests, and the composite action's public inputs.

## Open decisions

No open decisions are recorded in this repository.
