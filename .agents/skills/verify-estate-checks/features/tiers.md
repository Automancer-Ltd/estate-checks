# Choose CI work

A consumer invokes `Automancer-Ltd/estate-checks/tier@v1` and uses its `tier` and
`reason` outputs to choose work. The driver executes its manifest's plan and scan
steps against a synthetic PR file API and committed checkout.

- `docs`: unread documentation returns `none`.
- `agent-skill-markdown`: a large Markdown change under `.agents/skills/` stays
  `standard` until `docs-include` names that directory.
- `docs-include`: the named non-runtime directory returns `none` when no code
  reads it. `docs-include-product` keeps product Markdown on `standard`.
  `docs-include-read` returns `core` when a script reads the included file.
- `small`: one source edit returns `core`.
- `unmatched-source`: a small non-Convex edit without a matching test stays
  `core` with an empty test list; the consumer still runs lint and types.
- `large`: a 51-line source edit returns `standard`.
- `full-label` and `full-path`: `ci:full` and additional full-path globs each
  return `standard`, including an otherwise small or documentation-only change.
- `tested-push`: identical recorded and pushed Git trees return `none`.
- `merge-skew`: main moved by code since the PR's head passed, so that code
  joins the PR's large diff and returns `standard`.
- `docs-moved`: the pushed tree differs from the tree the PR run passed only by
  a changelog, so the push returns `none`.
- `db-test`: a one-line change to `workers/test_cost_postgres.py` returns
  `standard`, because a quick job may let database tests skip (t4647).
- `test-setup`: a one-line change to `src/test/setup.ts`, which `vite.config.ts`
  names in `setupFiles`, returns `standard`. No test names a setup file, so core
  would run lint alone.
- `api-failure` and `dispatch`: API errors and manual events off the default
  branch return `standard`. A dispatch on the default branch classifies from its
  newest proven ancestor (`DispatchOnMain` in `tier/test_classify.py`).

Real data: before releasing a push-path change, replay a consumer's recent push
SHAs through `decide` read-only, with `Api(<api url>, <owner/repo>, <token from
GH_TOKEN, never argv>)`, the consumer's tier inputs as `Config.from_inputs`,
`before` = the commit's first parent, and the `job-name` the consumer's workflow
actually passes (default `ci tier`), not its job's real name. Confirm the old
code reproduces each live run's `Tier:` line before trusting the new one. On
21 Oct 2026 a replay that passed ops' real job name hid the bug v1.7.11 fixed:
six consumers' pushes all ran `standard` because their job is not `ci tier`.
Pass every tier input the consumer sets, `docs-include` too: without auto's
`plans/**`, its prompts/ drift was not documentation and t6084 looked unfixed.

For a doc-reader change, replay PRs too: `pulls/<n>/files` through `classify`,
then `_scan` on a `git archive` of the PR head. Then diff `docs_read_by_code`
old against new over every consumer clone under
`/opt/automancer/worktrees/ci-shape/`. Read the line behind each pair the change
drops or adds (v1.7.12: 49 dropped, none added).

Proof: the public outputs in `results.json`. Gotcha: the consumer chooses the
commands for each tier. The classifier's output alone does not run that suite.
