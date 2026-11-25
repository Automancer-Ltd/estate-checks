# Automancer Estate Checks

Last verified: 2026-10-20 on automancer-vps

Shared mechanical CI checks for the Automancer estate, centralized in a single repository and invoked by version tag across all estate projects.

The check inspects code for **unbounded outside network and subprocess calls**—the estate's most repeated failure pattern, where an unconstrained supplier call, hanging HTTP request, or blocked subprocess halts a background job, locks an event loop, freezes a user-facing page, or consumes entire CI runner quotas.

The repository also ships [`tier`](tier/README.md), a second action that decides how much CI a change needs (none, core or standard) so docs-only changes and already-tested merges skip the suite.

The same action runs a second, pure-Python check: **workflow standards**, the mechanical rules 1-4 of the `testing` skill (CI area) plus rule 3's ban on a `schedule` trigger, read from this repository's own `.github/workflows` and reported as inline annotations. See [Workflow standards](#workflow-standards-testing-ci-rules-1-4).

It runs a third check, **install timeouts**: a workflow step that installs packages must declare `timeout-minutes` on the step or its job, and an unbounded install fails the run. See [Install timeouts](#install-timeouts-workflow-steps-that-install-packages).

---

## What the Check Catches

Rules are defined in [`rules/unbounded-calls.yml`](rules/unbounded-calls.yml) and tested via `semgrep --test`:

### JavaScript / TypeScript
- **`fetch-no-signal`**: Calls to `fetch(...)`, `window.fetch(...)`, or `globalThis.fetch(...)` with no `signal`.
  - *Fix*: Pass `signal: AbortSignal.timeout(ms)` or an `AbortController.signal` in the request options.
- **`axios-no-timeout`**: Direct `axios` calls (`axios(...)`, `axios.get`, `axios.post`, etc.) and client initializations (`axios.create()`) without `timeout` or `signal`.
  - *Fix*: Provide `timeout: <milliseconds>` (or `signal: AbortSignal.timeout(ms)`) in request config or client defaults.
- **`got-no-timeout`**: `got(...)` calls and instances (`got.extend()`) without `timeout` or `signal`.
  - *Fix*: Pass `timeout: { request: <ms> }` or `timeout: <ms>` in the options argument.
- **`child-process-no-timeout`**: Synchronous or execution subprocess calls (`child_process.exec`, `execFile`, `spawnSync`) without `timeout` or `signal`.
  - *Fix*: Pass `timeout: <milliseconds>` (or `signal`) in the options object.
- **`node-http-no-timeout`**: `http.request`, `https.request`, `http.get`, or `https.get` without `timeout` or `signal`.
  - *Fix*: Pass `timeout: <milliseconds>` or `signal` in the request options. The rule does not recognize a later `req.setTimeout(...)` call as a suppression (verified 2026-10-05).

### Python
- **`python-requests-no-timeout`**: `requests.*` HTTP methods without `timeout=` or with explicit `timeout=None`.
  - *Fix*: Add `timeout=<seconds>` (e.g. `timeout=10` or a connect/read tuple `(5, 30)`).
- **`python-httpx-no-timeout`**: Direct `httpx.*` HTTP calls or `httpx.Client()` / `httpx.AsyncClient()` initializations without `timeout=` or with `timeout=None`.
  - *Fix*: Pass `timeout=<seconds>` in the request call or client constructor.
- **`python-urllib-no-timeout`**: `urllib.request.urlopen` calls without a timeout argument or with `timeout=None`.
  - *Fix*: Pass `timeout=<seconds>`.
- **`python-subprocess-no-timeout`**: `subprocess.run`, `subprocess.check_output`, and `subprocess.check_call` without `timeout=` or with `timeout=None`.
  - *Fix*: Add `timeout=<seconds>` (e.g. `timeout=30`).

### Shell Scripts & CI Workflows
- **`shell-curl-no-timeout`**: Shell script `curl` invocations without `--max-time <sec>` or `-m <sec>`.
  - *Fix*: Add `--max-time <seconds>` or `-m <seconds>` (or pass via a named timeout variable/array).
- **`shell-wget-no-timeout`**: Shell script `wget` invocations without `--timeout=<sec>` or `-T <sec>`.
  - *Fix*: Add `--timeout=<seconds>` or `-T <seconds>`.
- **`workflow-curl-no-timeout`**: GitHub Actions `run:` steps invoking `curl` without `--max-time` or `-m`.
  - *Fix*: Add `--max-time <seconds>` or `-m <seconds>`.
- **`workflow-wget-no-timeout`**: GitHub Actions `run:` steps invoking `wget` without `--timeout` or `-T`.
  - *Fix*: Add `--timeout=<seconds>` or `-T <seconds>`.

---

## Workflow standards (`testing` CI rules 1-4)

`testing` (CI area) states four structural rules. They are mechanical, so this repository carries them
as a check instead of as prose: it reads the caller repository's own `.github/workflows/*.yml` and
annotates every breach, **warn-only** (Waseem, 2026-09-27) — a finding never fails the job,
whatever `fail-on-findings` says for the unbounded-call scan.

| Annotation | Rule | What is wrong |
| --- | --- | --- |
| `job-missing-timeout` | 1 | a job declares no `timeout-minutes` (GitHub's default is 360 minutes) |
| `job-timeout-below-floor` | 1 | a job's `timeout-minutes` (a literal, or a value an expression can choose with `&&`/`\|\|`) is below the 20-minute floor |
| `workflow-missing-concurrency` | 2 | a workflow declares no `concurrency:` group |
| `workflow-concurrency-no-group` | 2 | a concurrency block carries no non-empty `group` |
| `push-unfiltered` | 3 | `push:` carries no `branches`/`branches-ignore` filter, so every branch push bills the whole matrix twice |
| `push-wrong-branch` | 3 | `push.branches` does not include the default branch |
| `runner-label-literal` | 4 | an organisation job's `runs-on` names a hosted label (`ubuntu-*`, `macos-*`, `windows-*`, `blacksmith-*`) instead of `vars.CI_RUNNER` |
| `workflow-schedule-trigger` | 3 | `on:` includes `schedule`, so the workflow runs on a timer. Checks run once per change, on the pull request |
| `workflow-unparsable` | — | the workflow could not be parsed, so it was not checked (never a silent pass) |

### CI cost guards (November 2026 audit)

Five further warn-only findings pin the anti-patterns behind ~$400+/mo of avoidable Blacksmith
and GitHub Actions overflow in the 24–25 Nov 2026 spend audit. Each is annotated the same way —
a warning, never a failure.

| Annotation | What is wrong |
| --- | --- |
| `pr-job-fanout` | a `pull_request` workflow defines more than 3 PR-eligible jobs. Every job is a billed runner start; wide fan-out exhausts the shared boat.dev daily starts cap and evicts the whole org onto paid runners. Reusable-workflow callers and jobs gated to `push` events by their `if:` do not count |
| `oversized-paid-runner` | a PR-eligible job's `runs-on` names (or an expression scales to) `blacksmith-8vcpu` or larger: billed minutes multiply 4x–16x, and draft PR runs pay it too |
| `boat-missing-disk-ready` | a job reading `vars.CI_RUNNER` can land on boat.dev, but no step uses `Automancer-Ltd/estate-checks/disk-ready` (or the local `./disk-ready`) before checkout — the uv_cwd / disk-swap failure that previously forced repos off boat. Tiny gate jobs that run no repo commands are exempt |
| `pr-ready-for-review-trigger` | `on.pull_request.types` includes `ready_for_review`, so marking a draft PR ready pays a second full run even though the draft already ran the required checks |
| `unguarded-deploy-job` | in a PR-triggered workflow, a job whose id or name indicates deploy/release/publish declares no job-level `if:` — a paid runner still starts on every PR only to skip its steps |

Two of the five accept an inline justification comment on the key line or the two lines above,
because a legitimate exception exists and lives with the workflow: `# estate-checks-allow:
pr-job-fanout <reason>` and `# estate-checks-allow: oversized-paid-runner <reason>`.

The two rules that need judgement stay in the skill, not here: the five-minute target for a warm run is
measured over a whole run and never enforced by cancelling (this check only reads the declared limits), and a deploy scheduler keeping its own
group with `cancel-in-progress: false` is correct while a test workflow needs the standard block.

What the check deliberately does not judge, because a warning that is wrong gets ignored:

- **Personal repositories.** `Waseemilyas` repositories run on GitHub-hosted runners, so a literal
  `ubuntu-latest` there is correct and is not reported. Every organisation repository is.
- **Expressions and self-hosted labels.** `runs-on: ${{ matrix.os }}`, `runs-on: [self-hosted, linux]`
  and a literal that is not a hosted label are left alone; only a hosted-label literal is reported.
- **Timeouts at or above the floor.** A time limit only stops a stuck job (Waseem, 19 Nov 2026: speed
  is a measured target, never enforced by cancelling; a limit near the normal run time cancels honest
  cold runs and pays for re-runs). 20 is the floor; 30 for an extended or release-proof job and three
  times a measured cold run are both legitimate and not visible in YAML, so nothing above 20 is
  judged. An expression with no literal value (`${{ fromJSON(vars.X) }}`) is not judged either.
- **`branches-ignore`, tag-only `push:`, and concurrency keys.** A `push:` with only `tags:` does not
  double-bill branch work, and a different group key may be right for a deploy workflow.
- **The wrong-branch refinement needs the default branch.** It comes from the event payload; when it
  is unavailable, only the presence of a branch filter is checked.
- **A schedule that is not a trigger.** The word `schedule` in a comment, a job name or a step is
  not a timer. Dependabot's own `schedule:` in `.github/dependabot.yml` is not a workflow trigger
  and is not read.

`on: schedule` has no exception: `testing`'s CI rule bans scheduled CI in every repository (Waseem,
21 Oct 2026), and a comment in the workflow does not silence the warning. The same holds for the
three cost guards with no justification form: a real exception is a redesign of the workflow, not
a comment.

Disable the check entirely on a repository that needs to deviate, and say why in that repository:

```yaml
      - name: Run Estate Checks
        uses: Automancer-Ltd/estate-checks@v1
        with:
          check-workflows: 'false'
```

The number of workflow warnings is on the action's `workflow-warnings-count` output. Its tests
(`scripts/test-workflow-standards.py`) pin each rule against a fixture that must be flagged and one
that must not, so the check is proven to discriminate rather than to exist.

---

## Install timeouts (workflow steps that install packages)

A package install is the one CI step that talks to an index for minutes on end, so an install with
no time limit is the one step that can hold a job until GitHub cancels it. The automancer-ops
release check died exactly there on 19 Oct 2026 (run 39332563829, job 118334064821): killed 4m38s
in while a `pip` install was still running, the release PR red and unlanded. The estate had
already bounded its own installs (semgrep in #38, the gitleaks download in #40, pi-harness in #52)
— but nothing stopped the *next* repository adding an unbounded one.

This check reads the caller repository's `.github/workflows/*.yml` and **fails the run** when a
`run:` step executes a package install — `pip install` (including `pip3` and `python -m pip`),
`uv pip install`, `npm ci`/`npm install`, `apt-get install`/`apt install` or `go install` — while
neither the step nor its job declares `timeout-minutes`:

| Annotation | What is wrong |
| --- | --- |
| `install-unbounded` | a step runs a package install with no `timeout-minutes` on the step or the job |
| `workflow-unparsable` | the workflow could not be parsed, so its installs are not proven bounded (never a silent pass) |

Unlike the unbounded-call scan and the workflow-standards warnings this check **blocks by
default** (tracker t4456): the failure it prevents is a red, unlanded release PR, so catching it
in the pull request that introduces it is the point. `fail-on-findings` does not apply to it; a
check that cannot run (PyYAML missing, a workflow that will not parse, a malformed allowlist row)
fails loudly for the same reason.

The fix is the job's own `timeout-minutes`, which every job declares anyway (rule 1) and which
bounds every step in it:

```yaml
  build:
    timeout-minutes: 20
    steps:
      - run: pip install --quiet -r requirements.txt
```

A step limit is optional. If you add one, it guards a hang and sits at several times the install's
slowest honest time (a cold cache, a slow mirror), never near its normal time: an install cut off
while it is still downloading fails a correct change and pays for a re-run.

### Deliberate exceptions

An exception is a row in [`install-timeout-allowlist.tsv`](install-timeout-allowlist.tsv) —
`repo<TAB>workflow path<TAB>job id<TAB>reason`, with `*` wildcards — added by a pull request to
this repository. The allowlist is the estate's shared record: a comment in the caller's workflow
silences nothing, and a row without a reason fails the check rather than passing silently. The
list ships empty; every row needs a reason a reviewer can check.

### Precision decisions

- **A bounded command is not an install.** A command wrapped in coreutils `timeout 300 …`
  (or `timeout 5m …`, `timeout -k 5 300 …`, `timeout --signal=KILL 5m …`, or `timeout 300 \`
  with the install on the next line) is bounded and passes without touching the workflow.
- **Comments and echoes are not commands.** `# pip install …` and `echo "npm install …"` do not
  run an install, so they are not findings.
- **Reusable-workflow caller jobs are exempt.** Workflow syntax bans `timeout-minutes` on a job
  that calls a reusable workflow; the callee bounds its own jobs.
- **`uses:` steps install nothing.** Marketplace actions own their own time limits.
- **Expressions count as declarations.** `timeout-minutes: ${{ matrix.minutes }}` declares a
  limit its author chose; judging values is the `testing` CI budget rule's business, not this
  check's.
- **The command set is fixed.** `yarn`, `pnpm`, `cargo`, `uv sync` and friends are not scanned;
  adding a tool trades recall for false positives on every consumer, so it is a deliberate change
  with tests, not a drive-by.

The finding count is on the action's `install-timeout-count` output. Disable the check on a
repository that needs to deviate, and say why there:

```yaml
      - name: Run Estate Checks
        uses: Automancer-Ltd/estate-checks@v1
        with:
          check-installs: 'false'
```

The tests (`scripts/test-install-timeouts.py`) pin every rule against a fixture that must be
flagged and one that must not, including the allowlist wildcards and the exit codes, and the
verify driver drives the real composite action red on an unbounded fixture and green on the
bounded and skipped ones.

---

## Known Gaps & Precision Decisions

Under Automancer CI standards, **precision beats recall**: a check that produces false positives will be ignored or disabled. The following patterns are intentionally out of scope:

1. **Third-Party SDK Clients**: High-level SDKs (e.g. AWS SDK, Stripe, OpenAI, Anthropic, Convex) that implement internal retry policies, exponential backoffs, and client-level timeouts are not flagged.
2. **Streaming Subprocesses**: Asynchronous long-lived subprocesses started via `spawn(...)` without synchronous completion waits (such as background daemons or piped streams with custom lifecycle management) are not flagged by the sync/exec rules.
3. **Internal Helper Wrappers**: Generic wrapper functions accepting an options object (e.g. `function customFetch(url, init)`) are not flagged at caller sites when the helper encapsulates the configuration.
4. **Low-Level Socket APIs**: Direct TCP/TLS socket connections via `net.connect` or `tls.connect` with custom event listeners are not parsed.

For the workflow standards check the precision decisions are listed in
[its own section](#workflow-standards-testing-ci-rules-1-4).

---

## Scope: Server vs. Browser & Test Files

### Priority on Server, Queues, and Workflows
The estate checks primarily protect **backend servers, queue workers, background schedulers, and CI workflows**. An unshielded call in these environments risks indefinite hangs, frozen processing loops, blocked deployment queues, or consumed runner budgets.

### Browser UI Code & Scoping
In frontend client applications (e.g. React/Vite single-page apps, authoring canvases, or internal dashboards), calls to `fetch()` frequently query local development backends or same-origin APIs. While bounding browser fetches is good practice, repositories can cleanly scope out browser-only directories using the `exclude-paths` input without needing inline suppression comments on every UI call:

```yaml
- uses: Automancer-Ltd/estate-checks@v1
  with:
    exclude-paths: 'tools/**, src/ui/**, frontend/**'
```

### Warnings, not failures
By default (Waseem, 2026-09-27) the check never fails a job. Each finding is an inline warning annotation on the PR diff, plus a row in the job summary. A check that could not run (Semgrep crashed, the baseline was missing, the scan result was invalid) is a loud "could not run" warning, never a silent pass. Address the warnings in the PR or a follow-up. A repo can opt into blocking with `fail-on-findings: 'true'`, which also makes "could not run" fail.

### Exclusions must not hide server code
When `exclude-paths` covers a file that is recognisably server-side (a Next.js `app/api/` or `pages/api/` route, a `route.ts` under `app/`, a file declaring `"use server"`, or a `convex/`, `server/`, `workers/` or `functions/` folder), the check raises a warning naming the file. Exclude browser-only code, never server code.

### Test Files Excluded by Default
By default, test directories and test files (`*.test.*`, `*_test.py`, `tests/**`, `__tests__/**`) are excluded from scanning. Test suites are already bounded by the test runner's global timeout (e.g. Jest, Vitest, pytest), so testing subprocesses and assertions does not require duplicate manual timeouts. To customize or disable this exclusion, override `exclude-test-paths`.

---

## Adoption

Repos adopt the check via the GitHub Actions composite action.

### GitHub Actions Workflow Snippet

Add the scanner as a step in the existing CI job, after its checkout, including
for docs-only changes. It needs full history for differential scanning:

```yaml
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - name: Run Estate Checks
        uses: Automancer-Ltd/estate-checks@v1
```

Keep the caller's existing required job. The scanner needs no job or gate of its
own. CI trigger, runner and timeout rules live in the estate's `testing` skill (CI area).

### Action Inputs

| Input | Description | Default |
|---|---|---|
| `baseline-commit` | Baseline commit SHA to diff against on pull requests. Auto-detected from `github.event.pull_request.base.sha` on PRs. | `""` |
| `check-installs` | Set to `'false'` to skip the install-timeout gate (a step installing packages with no `timeout-minutes` on the step or its job fails the run; see [Install timeouts](#install-timeouts-workflow-steps-that-install-packages)). | `'true'` |
| `check-workflows` | Set to `'false'` to skip the workflow standards warnings (`testing` CI rules 1-4, warn-only). | `'true'` |
| `exclude-paths` | Comma- or newline-separated paths or glob patterns to exclude from scanning (e.g. browser UI folders). | `""` |
| `exclude-test-paths` | Comma- or newline-separated test paths or globs to exclude (set to empty to disable default test exclusions). | `'*.test.*, *_test.py, tests/**, __tests__/**'` |
| `fail-on-findings` | `false` (default): warnings only. `true`: findings and "could not run" fail the job. `auto` is a legacy alias for `false`. | `false` |
| `rules-path` | Custom path to Semgrep rules file. | Bundled `rules/unbounded-calls.yml` |
| `semgrep-version` | Pinned Semgrep CLI version. | `1.178.0` |

### Pull Requests vs. Default Branch Behavior
- **Pull Requests**: The action automatically detects `github.event.pull_request.base.sha` and invokes Semgrep with `--baseline-commit`. **Only newly introduced unbounded calls are reported, as warnings on the PR.** Existing baseline findings in the repository are ignored, enabling incremental adoption without blocking ongoing work.
  > [!IMPORTANT]
  > Differential scanning requires a resolvable base commit in git history. `actions/checkout` must be configured with `fetch-depth: 0`. If the baseline commit cannot be resolved on a pull request, the action raises a "could not run" warning with the fix, rather than falling back to a full repository scan.
- **Default Branch (`main`)**: The action scans the entire codebase and publishes a report to the GitHub Actions Job Summary without failing the build (`fail-on-findings: false`).

---

## Suppressions

For rare cases where an outside call is deliberately unbounded—such as an intentional server-sent event (SSE) stream or a long-poll managed by an external watchdog timer—suppress the rule inline with an explanatory reason:

### JavaScript / TypeScript
```javascript
// nosemgrep: fetch-no-signal - intentional long-poll stream with external abort watchdog
const response = await fetch("https://stream.example.com/events");
```

### Python
```python
# nosemgrep: python-requests-no-timeout - legacy migration worker with outer process watchdog
response = requests.get("https://internal.service/export")
```

### Shell & Workflows
```bash
# nosemgrep: shell-curl-no-timeout - watchdog service running health poll
curl -sS "http://localhost:8080/health"
```

---

## Release Process & Updates

### Releases and Tagging

A merge updates source; consumers execute `v1`. Before changing it:

1. Confirm the candidate merge tree passed this repo's required `check` job.
2. In an owned disposable consumer PR, pin both actions to the candidate SHA.
   Prove tier outputs and scanner annotations in the runner, then close the PR
   unmerged. Keep its CI link in the release notes. A consumer without a tier
   step (inbox-labeller since its #36) needs one added in that PR, and its
   workflow `permissions:` need `pull-requests`, `actions` and `checks: read`,
   or the tier reads HTTP 403 and falls back to `standard` (inbox-labeller#37).
3. Record the old `v1` SHA with `git ls-remote --tags origin refs/tags/v1`.
4. Create the next semver tag at that candidate, publish its GitHub release, and
   move `v1` to it. Read both remote tags back and require the candidate SHA.
   The estate git guard refuses the forced tag push; set
   `GIT_GUARD_ALLOW=1 GIT_GUARD_REASON='<release, rollback SHA>'` on that push only.
5. Invoke `v1` in the disposable consumer once more. A release is verified when
   the runner downloaded the new SHA and its checks behaved correctly.

Rollback: move `v1` back to the recorded previous SHA and read it back. Immutable
semver tags remain in place. `deploy.json` exposes the live `v1` revision for Guardian
at `https://api.github.com/repos/Automancer-Ltd/estate-checks/commits/v1` (`.sha`).

The checkout belongs to tracker project `estate-ops` as an additional repository.
That project's primary repo stays `Automancer-Ltd/automancer-ops`.

Web-only estate-watch deploy checks do not apply. They stay visible as warnings
until a web surface exists, and that visibility is the record:

- no Sentry project, because nothing serves errors to a browser
- no UptimeRobot monitor, because there is no public URL
- no `projects.json` or obs `apps.json` registry entry, so `deploy.start` warns
  that the product is not in the registry

Do not register a fake web product to clear those warnings.

## disk-ready: wait for the real disk on boat (t23520)

A job on a boat.dev `ci-base` VM can start while boat is still swapping in the snapshot disk; a step then loses its working directory (`uv_cwd`, "Unable to read current working directory"). Put this as the first step of every job that can run on boat:

```yaml
steps:
  - uses: Automancer-Ltd/estate-checks/disk-ready@v1
  - uses: actions/checkout@v7
```

It runs the same test as `boat-job` (t10986): `/var/lib/ascii-lazy` absent, or `hydration-done` present, no `active` marker, and the workspace filesystem not fuse. It waits up to `timeout` seconds (default 180) and then fails the job. Where `/var/lib/ascii-lazy` does not exist (GitHub-hosted, Blacksmith) it returns at once. Roll back by removing the step.
