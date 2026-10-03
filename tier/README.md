# CI tier — run CI in proportion to the change

`Automancer-Ltd/estate-checks/tier@v1` decides how much CI a change needs.
Waseem's rule (2026-10-03): a docs-only change runs no CI, a small scoped fix
runs a quick core set, everything else runs the repo's normal suite. Extended
suites stay on the production-deploy path, never on pull requests.

| Tier | When | The repo runs |
|---|---|---|
| `none` | every changed file is declared documentation, **or** a push to the default branch whose tree is exactly what the merged PR's run passed | nothing but the gate |
| `core` | at most `small-max-files` non-doc files and `small-max-lines` changed lines, and nothing in `full-paths` | its quick job: lint, typecheck, tests related to the change (target under 2 minutes) |
| `standard` | everything else, any other event, and any classification failure | its normal CI |

The label `ci:full` on a pull request forces `standard`.

## How each tier is decided

- **Pull request:** the PR's file list from the REST API (no checkout). A rename
  counts as both its old and new path, so moving code into `docs/` is not docs.
- **Push to the default branch:** the action finds the merged PR, reads the
  tree its passing run recorded (a `ci-tier` notice annotation on this job,
  `tested-tree=<sha>`), and compares it with the pushed commit's tree. Equal
  trees mean the exact code on main already passed, so the tier is `none`.
  Otherwise the before...after diff is classified like a PR, which keeps the
  merge-skew check (two PRs that pass alone but clash together).
- **Anything else** (`workflow_dispatch`, `schedule`, a force push, a new
  branch, an API error, more than 300 changed files on a push): `standard`.

The action fails toward running tests. It never fails the job: an error is a
warning and `standard`.

## Declaring docs per repository

Markdown is shipped content in some repos (skills, agent instructions, content
pages, fixtures under `docs/`). There is no default: an empty `docs-paths`
means nothing counts as docs. Declare only paths that ship nothing and that no
test or script reads, and put any file a test does read in `docs-exclude`.
Find readers with, for example:

```sh
git grep -nE "['\"\`/]docs/" -- tests scripts src
```

Patterns are anchored at the repo root: `*.md` is root markdown only,
`docs/**/*.md` is markdown anywhere under `docs/`.

## Wiring a workflow

```yaml
permissions:
  contents: read
  pull-requests: read   # the PR's file list
  actions: read         # the merged PR's runs
  checks: read          # the tested-tree annotation

jobs:
  tier:
    name: ci tier            # must equal job-name (default 'ci tier')
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 3
    outputs:
      tier: ${{ steps.tier.outputs.tier }}
    steps:
      - id: tier
        uses: Automancer-Ltd/estate-checks/tier@v1
        with:
          docs-paths: |
            docs/**/*.md
            *.md
          docs-exclude: docs/schema-classification.md
          full-paths: |
            pnpm-lock.yaml
            .github/**

  quick:                      # the core tier's job
    needs: tier
    if: needs.tier.outputs.tier == 'core'
    # lint, typecheck, related tests

  test:                       # the existing suite
    needs: tier
    if: needs.tier.outputs.tier == 'standard'

  ci-gate:
    name: ci gate
    if: always()
    needs: [tier, quick, test]
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 5
    steps:
      - name: Assert the jobs this tier requires passed
        env:
          RESULTS: ${{ toJSON(needs) }}
          TIER: ${{ needs.tier.outputs.tier }}
          REQUIRED_none: ''
          REQUIRED_core: 'quick'
          REQUIRED_standard: 'test'
        run: |
          set -euo pipefail
          printf '%s\n' "$RESULTS"
          RESULTS="$RESULTS" TIER="$TIER" node - <<'NODE'
          const needs = JSON.parse(process.env.RESULTS);
          const tier = process.env.TIER;
          const required = process.env[`REQUIRED_${tier}`];
          if (needs.tier?.result !== "success" || required === undefined) {
            console.error(`::error::CI gate failed: tier job ${needs.tier?.result}, tier '${tier}'.`);
            process.exit(1);
          }
          const must = required.split(/\s+/).filter(Boolean);
          for (const [name, { result }] of Object.entries(needs)) {
            const ok = must.includes(name) ? result === "success" : ["success", "skipped"].includes(result);
            if (!ok) {
              console.error(`::error::CI gate failed: ${name} was ${result} on tier '${tier}'.`);
              process.exit(1);
            }
          }
          console.log(`CI gate green on tier '${tier}' (required: ${must.join(", ") || "nothing"}).`);
          NODE
```

The gate requires exactly the jobs the tier names to succeed and every other
job to have passed or been skipped. A required job that was skipped fails it,
so a broken `if:` cannot quietly turn CI off.

The tier job is the one standalone job allowed besides the gate and
`estate-checks`: it bills one runner-minute and saves the whole suite on
docs-only changes and on already-tested merges.
