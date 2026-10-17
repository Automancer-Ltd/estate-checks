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
- `merge-skew`: different trees require work; its large diff returns `standard`.
- `api-failure` and `dispatch`: API errors and manual events return `standard`.

Proof: the public outputs in `results.json`. Gotcha: the consumer chooses the
commands for each tier. The classifier's output alone does not run that suite.
