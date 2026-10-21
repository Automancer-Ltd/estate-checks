# CI tier — run CI in proportion to the change

Last verified: 2026-10-17 on automancer-vps

`Automancer-Ltd/estate-checks/tier@v1` decides how much CI a change needs.
Waseem's rule (2026-10-03): a docs-only change runs no CI, a small scoped fix
runs a quick core set, everything else runs the repo's normal suite. Extended
suites stay on the production-deploy path, never on pull requests. It is plain
rules, not a model: the same change always gets the same answer.

| Tier | When | The repo runs |
|---|---|---|
| `none` | every changed file is documentation that no code reads, **or** a push to the default branch whose tree is exactly what the merged PR's run passed, or differs from the PR's passed head only by such documentation | nothing but the gate |
| `core` | at most `small-max-files` non-doc files and `small-max-lines` changed lines, nothing that always needs the full suite, and at most `core-max-tests` test files naming the changed code | its quick job: lint, typecheck, and the tests in `direct-tests` (target under 2–3 minutes) |
| `standard` | everything else, any other event, and any classification failure | its normal CI |

The label `ci:full` on a pull request forces `standard`.

## Zero configuration by default

Most repositories pass no inputs. The built-in lists live in `classify.py`:

- **Documentation:** every `.md` and `.rst` file, except under folders where
  markdown is usually product, content or test data (`src/`, `content/`,
  `pages/`, `_posts/`, `skills/`, `prompts/`, `templates/`, `public/`,
  `static/`, `.agents/`, `.claude/`, fixtures and test folders). Product,
  content, page and test markdown stays excluded even if a repository passes
  `docs-include`. That input can name one non-runtime agent or skill directory
  (for example `.agents/skills/verify-dissidium/**`) so a Markdown-only change
  there is documentation. Code that reads an included file still makes it code.
- **Always the full suite:** CI files, dependency manifests and lockfiles,
  toolchain and runtime versions, TypeScript/lint/test/bundler config,
  Dockerfiles, schema files, SQL and migrations, and hosting config.
- **Database tests and their harness always run the full suite.** A path
  naming postgres, `pg`, `db` or `database` tests (`test_x_postgres.py`,
  `x.postgres.test.ts`, `x.db.test.ts`, `tests/db/`, `scripts/ci-postgres-tests.sh`)
  is `standard`, because a quick job may let database tests skip. KG PR #646
  changed one, passed core with them skipped, and reddened master (t4647).
- **Who reads the docs is detected, not declared.** When the answer depends on
  documentation, the action checks out the code and searches every non-doc file
  for each changed doc's path, its distinctive file name, or its folder in
  quotes (`"docs/reports"`). Any hit makes that doc count as code. A test that
  starts reading a doc next month is picked up without anyone editing a list.
  Records are data, not readers: `.jsonl`, `.ndjson`, `.csv`, `.tsv` and `.log`
  files are skipped, and a `.json` or `.txt` file counts only when it names the
  doc's full path, as a manifest a program loads does (t6067).

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

Convex modules also match their generated API names: `convex/feedback.ts`
matches `api.feedback` and `internal.feedback`; nested folders become dots
(`convex/pipeline/data.ts` matches `api.pipeline.data`). Direct imports still
match. If any changed Convex file has no matching test, the tier is `standard`,
even if another changed file has tests. A Convex change cannot pass on lint
and typecheck alone.

Inputs exist for the exceptions:
- `docs-paths: none` for a repo where markdown is the product (a site,
  a skills repo).
- `full-paths` for code only a non-unit CI job exercises (playtest or deploy
  scripts, shared test harness), so a small change there still runs the full
  suite.
- `docs-paths` / `docs-exclude` to replace or narrow the documentation default.
- `docs-include` to name a non-runtime agent or skill directory as
  documentation. It does not reclassify product, content, page or test
  markdown, and it does not override `full-paths` or reader detection.

## Sparse checkout for asset-heavy repositories

The optional checkout inputs pass directly to `actions/checkout` when the
classifier needs to scan files. Defaults preserve a full checkout, so a
repository whose tree is mostly assets or records should set `sparse-checkout`:
automancer-auto's unsparse tier checkout fetched 975 MB and took about 40 s on
every push that needed a scan (t6067).

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
- **Push to the default branch:** the action finds the merged PR head (a merge
  commit's second parent; for a squash or rebase merge, GitHub's link from the
  commit to its PR, which can lag the merge by seconds), reads the
  tree its passing run recorded (a `ci-tier` notice annotation on this job,
  `tested-tree=<sha>`), and compares it with the pushed commit's tree. Equal
  trees mean the exact code on main already passed, so the tier is `none`.
  Otherwise main moved while the PR waited. When the PR's head passed and is an
  ancestor of the merge, what main holds beyond that head is all that went
  untested: documentation alone (a release PR's changelog) gives `none`, and
  code joins the PR's diff, classified like a PR, which keeps the merge-skew
  check (two PRs that pass alone but clash together). With no passing PR run,
  or a squash or rebase merge, the before...after diff is classified alone.
- **Anything else** (`workflow_dispatch`, `schedule`, a force push, a new
  branch, an API error, a failed checkout, more than 300 changed files on a
  push): `standard`. The action never fails the job.

## Wiring a workflow

Use one job when the complete verdict is under about five minutes. A later push
finds the PR run's tested-tree notice on any of its jobs, the one named `job-name`
first, so passing `job-name` only saves API reads. Scanners run
even on documentation-only changes. The job has no dependency or job-level
condition, so it always reports and can be the required context.

```yaml
permissions:
  contents: read
  pull-requests: read
  actions: read
  checks: read

jobs:
  check:
    name: check
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 10
    steps:
      - id: tier
        uses: Automancer-Ltd/estate-checks/tier@v1
        with:
          job-name: check
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: Automancer-Ltd/estate-checks@v1
      # Add the repo's existing setup/lint/typecheck steps here, conditional on
      # steps.tier.outputs.tier != 'none'. For core, pass direct-tests to the
      # existing test runner as paths; for standard, run the normal suite.
```

A consumer must connect every tier to its actual commands; this snippet only
shows action placement. CI structure, including the measured exception for
parallel jobs and deploy workflows, lives in the estate's `ci-standards` skill.
