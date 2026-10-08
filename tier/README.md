# CI tier — run CI in proportion to the change

Last verified: 2026-10-05 on automancer-vps

`Automancer-Ltd/estate-checks/tier@v1` decides how much CI a change needs.
Waseem's rule (2026-10-03): a docs-only change runs no CI, a small scoped fix
runs a quick core set, everything else runs the repo's normal suite. Extended
suites stay on the production-deploy path, never on pull requests. It is plain
rules, not a model: the same change always gets the same answer.

| Tier | When | The repo runs |
|---|---|---|
| `none` | every changed file is documentation that no code reads, **or** a push to the default branch whose tree is exactly what the merged PR's run passed | nothing but the gate |
| `core` | at most `small-max-files` non-doc files and `small-max-lines` changed lines, nothing that always needs the full suite, and at most `core-max-tests` test files naming the changed code | its quick job: lint, typecheck, and the tests in `direct-tests` (target under 2–3 minutes) |
| `standard` | everything else, any other event, and any classification failure | its normal CI |

The label `ci:full` on a pull request forces `standard`.

## Zero configuration by default

Most repositories pass no inputs. The built-in lists live in `classify.py`:

- **Documentation:** every `.md` and `.rst` file, except under folders where
  markdown is usually product, content or test data (`src/`, `content/`,
  `pages/`, `_posts/`, `skills/`, `prompts/`, `templates/`, `public/`,
  `static/`, `.agents/`, `.claude/`, fixtures and test folders).
- **Always the full suite:** CI files, dependency manifests and lockfiles,
  toolchain and runtime versions, TypeScript/lint/test/bundler config,
  Dockerfiles, schema files, SQL and migrations, and hosting config.
- **Who reads the docs is detected, not declared.** When the answer depends on
  documentation, the action checks out the code and searches every non-doc file
  for each changed doc's path, its distinctive file name, or its folder in
  quotes (`"docs/reports"`). Any hit makes that doc count as code. A test that
  starts reading a doc next month is picked up without anyone editing a list.

**The quick job's tests are named, not traced.** For a core change the action
lists the test files that refer to a changed file by folder and name
(`lib/costEnvelope`, `lib.costEnvelope`, or `engine/effects` for an index
module), plus any changed test files, as the `direct-tests` output. Following
every import (`vitest related`) selects most of a suite when a small change
touches a widely imported module: in dungeon-master a one-line change to
`convex/lib/costEnvelope.ts` ran past six minutes on one runner. Typecheck
covers every other caller, and the full suite runs on the next standard
change. If more than `core-max-tests` (default 30) test files name the change,
it is not a low-risk change and the tier is `standard`.

Inputs exist for the exceptions:
- `docs-paths: none` for a repo where markdown is the product (a site,
  a skills repo).
- `full-paths` for code only a non-unit CI job exercises (playtest or deploy
  scripts, shared test harness), so a small change there still runs the full
  suite.
- `docs-paths` / `docs-exclude` to replace or narrow the documentation default.

## Sparse checkout for asset-heavy repositories

The optional checkout inputs pass directly to `actions/checkout` when the
classifier needs to scan files. Defaults preserve a full checkout.

| Input | Default | Meaning |
|---|---|---|
| `sparse-checkout` | empty | Newline-separated directories (cone mode) or patterns (non-cone mode). Empty checks out the full repository. |
| `sparse-checkout-cone-mode` | `'true'` | Use cone mode. Set `'false'` for gitignore-style patterns. |

Retain every file the classifier reads: code, tests, docs and configuration.
Exclude only binary asset trees that cannot contain source, tests or doc
readers. Missing readers or tests can produce an incorrect tier or an incomplete
`direct-tests` output. The classifier cannot detect that a consumer omitted them.

For example, a repository whose classifier-readable files are all in the
listed directories or at the root can use:

```yaml
- id: tier
  uses: Automancer-Ltd/estate-checks/tier@v1
  with:
    sparse-checkout: |
      .github
      src
      tests
      docs
      config
```

Cone mode also includes root files. Adapt the list to the repository; include
all other source, test, documentation and configuration directories. To exclude
an asset directory with patterns, set `sparse-checkout-cone-mode: 'false'`.
These inputs affect only the tier action's conditional checkout. They do not
configure checkout in other jobs or change history depth or tier selection.

## How each tier is decided

- **Pull request:** the PR's file list from the REST API. A rename counts as
  both its old and new path, so moving code into `docs/` is not docs.
- **Push to the default branch:** the action finds the merged PR, reads the
  tree its passing run recorded (a `ci-tier` notice annotation on this job,
  `tested-tree=<sha>`), and compares it with the pushed commit's tree. Equal
  trees mean the exact code on main already passed, so the tier is `none`.
  Otherwise the before...after diff is classified like a PR, which keeps the
  merge-skew check (two PRs that pass alone but clash together).
- **Anything else** (`workflow_dispatch`, `schedule`, a force push, a new
  branch, an API error, a failed checkout, more than 300 changed files on a
  push): `standard`. The action never fails the job.

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
      changed-files: ${{ steps.tier.outputs.changed-files }}
      direct-tests: ${{ steps.tier.outputs.direct-tests }}
    steps:
      - id: tier
        uses: Automancer-Ltd/estate-checks/tier@v1

  quick:                      # the core tier's job
    needs: tier
    if: needs.tier.outputs.tier == 'core'
    # lint, typecheck, then the tests in fromJSON(needs.tier.outputs.direct-tests)
    # (may be empty: then lint and typecheck are the check)

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
