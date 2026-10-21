# Estate Checks

Three consumer surfaces: `action.yml` warns about unbounded calls and workflow
standards, and fails a run whose workflow steps install packages with no
timeout; `tier/action.yml` decides how much CI a change needs. Terms:
[GLOSSARY.md](GLOSSARY.md). Behaviour contract: [SPEC.md](SPEC.md).

## Work and check

Run `uv sync --frozen` from a clean checkout, then `checkq -- scripts/check` on
auto-vps. It runs Ruff and strict basedpyright; actionlint also runs when installed.
Name only the affected test files, for example:
`checkq -- scripts/check tier/test_classify.py`.
CI owns the full suite, Semgrep rule fixtures and composite-action checks.
For action behaviour or release work, run
[verify-estate-checks](.agents/skills/verify-estate-checks/SKILL.md).

`pyproject.toml` configures basedpyright and its language server. All production
Python is included; `rules/` contains deliberately invalid scanner fixtures.
No generated code or build is needed for symbol navigation.

## Trace behaviour

- Why only quick CI? `tier/classify.py:classify` decides `none/core/standard`;
  `_scan` checks doc readers, selects tests and escalates unmatched Convex files.
- Which tests run for file X? `direct_tests` uses `reference_keys`, relative
  imports and neighbouring test names. `tier/action.yml` exposes `direct-tests`.
- Force full CI? `ci:full`, `full-paths` or size limits; see `tier/README.md`.
  `docs-include` names one non-runtime directory as documentation; product
  markdown stays excluded (`tier/classify.py:doc_candidates`).
- Scanner annotations/counts: `scripts/generate-summary.py`; workflow warnings:
  `scripts/workflow-standards.py`; the blocking install gate:
  `scripts/install-timeouts.py` (exceptions in `install-timeout-allowlist.tsv`).
  Rules and positive/negative fixtures: `rules/`.

## Release

Merging changes main; consumers execute the moving `v1` tag. Read the release
procedure in `README.md` before changing tags. Preserve green exact-head CI and
prove the candidate in a disposable consumer PR before moving `v1`. Keep the
previous tag SHA for rollback. `deploy.json` names the live revision read.

Last verified: 2026-10-21 on auto-vps.
