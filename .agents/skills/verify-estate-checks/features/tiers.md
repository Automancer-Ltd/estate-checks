# Choose CI work

A consumer invokes `Automancer-Ltd/estate-checks/tier@v1` and uses its `tier` and
`reason` outputs to choose work. The driver executes its manifest's plan and scan
steps against a synthetic PR file API and committed checkout. Every PR journey
also requires `touched-files`, the output consumers path-filter steps on, to name
the changed file through the manifest's output expression.

- `docs`: unread documentation returns `none`.
- `agent-skill-markdown`: a large Markdown change under `.agents/skills/` stays
  `standard` until `docs-include` names that directory.
- `docs-include`: the named non-runtime directory returns `none` when no code
  reads it. `docs-include-product` keeps product Markdown on `standard`.
  `docs-include-read` returns `core` when a script reads the included file.
- `small`: one source edit returns `core`.
- `unmatched-source`: a small edit to a code file no test names returns
  `standard`: the planner cannot say which tests it affects (t20703).
- `large`: a 51-line source edit returns `standard`.
- `full-label` and `full-path`: `ci:full` and additional full-path globs each
  return `standard`, including an otherwise small or documentation-only change.
- `db-test`: a one-line change to `workers/test_cost_postgres.py` returns
  `standard`, because a quick job may let database tests skip (t4647).
- `test-setup`: a one-line change to `src/test/setup.ts`, which `vite.config.ts`
  names in `setupFiles`, returns `standard`. No test names a setup file, so core
  would run lint alone.
- `push-main`, `tag`, `dispatch` and `api-failure`: a push to the default branch, a tag,
  a dispatch and an API failure each return `standard`; nothing but a pull request
  can run less than the full suite (t20703).

Real data: before releasing, replay a consumer's recent failed pull-request runs
through `classify` and `_scan` (`pulls/<n>/files` and a `git archive` of the PR head)
and require the tests that failed to be in `direct-tests`, or the tier to be `standard`.
Pass every tier input the consumer sets, `docs-include` too.

For a doc-reader change, replay PRs too: `pulls/<n>/files` through `classify`,
then `_scan` on a `git archive` of the PR head. Then diff `docs_read_by_code`
old against new over every consumer clone under
`/opt/automancer/worktrees/ci-shape/`. Read the line behind each pair the change
drops or adds (v1.7.12: 49 dropped, none added).

Proof: the public outputs in `results.json`. Gotcha: the consumer chooses the
commands for each tier. The classifier's output alone does not run that suite.
