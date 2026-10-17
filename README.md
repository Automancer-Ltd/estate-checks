# Automancer Estate Checks

Last verified: 2026-10-16 on automancer-vps

Shared mechanical CI checks for the Automancer estate, centralized in a single repository and invoked by version tag across all estate projects.

The check inspects code for **unbounded outside network and subprocess calls**—the estate's most repeated failure pattern, where an unconstrained supplier call, hanging HTTP request, or blocked subprocess halts a background job, locks an event loop, freezes a user-facing page, or consumes entire CI runner quotas.

The repository also ships [`tier`](tier/README.md), a second action that decides how much CI a change needs (none, core or standard) so docs-only changes and already-tested merges skip the suite.

The same action runs a second, pure-Python check: **workflow standards**, the mechanical rules 1-4 of the `ci-standards` skill plus rule 3's ban on a `schedule` trigger, read from this repository's own `.github/workflows` and reported as inline annotations. See [Workflow standards](#workflow-standards-ci-standards-rules-1-4).

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

## Workflow standards (ci-standards rules 1-4)

`ci-standards` states four structural rules. They are mechanical, so this repository carries them
as a check instead of as prose: it reads the caller repository's own `.github/workflows/*.yml` and
annotates every breach, **warn-only** (Waseem, 2026-09-27) — a finding never fails the job,
whatever `fail-on-findings` says for the unbounded-call scan.

| Annotation | Rule | What is wrong |
| --- | --- | --- |
| `job-missing-timeout` | 1 | a job declares no `timeout-minutes` (GitHub's default is 360 minutes) |
| `job-timeout-over-cap` | 1 | a job declares more than the 30-minute absolute cap |
| `workflow-missing-concurrency` | 2 | a workflow declares no `concurrency:` group |
| `workflow-concurrency-no-group` | 2 | a concurrency block carries no non-empty `group` |
| `push-unfiltered` | 3 | `push:` carries no `branches`/`branches-ignore` filter, so every branch push bills the whole matrix twice |
| `push-wrong-branch` | 3 | `push.branches` does not include the default branch |
| `runner-label-literal` | 4 | an organisation job's `runs-on` names a hosted label (`ubuntu-*`, `macos-*`, `windows-*`, `blacksmith-*`) instead of `vars.CI_RUNNER` |
| `workflow-schedule-trigger` | 3 | `on:` includes `schedule`, so the workflow runs on a timer. Checks run once per change, on the pull request |
| `workflow-unparsable` | — | the workflow could not be parsed, so it was not checked (never a silent pass) |

The two rules that need judgement stay in the skill, not here: the ten-minute budget is measured
over a whole run (this check only reads the declared limits), and a deploy scheduler keeping its own
group with `cancel-in-progress: false` is correct while a test workflow needs the standard block.

What the check deliberately does not judge, because a warning that is wrong gets ignored:

- **Personal repositories.** `Waseemilyas` repositories run on GitHub-hosted runners, so a literal
  `ubuntu-latest` there is correct and is not reported. Every organisation repository is.
- **Expressions and self-hosted labels.** `runs-on: ${{ matrix.os }}`, `runs-on: [self-hosted, linux]`
  and a literal that is not a hosted label are left alone; only a hosted-label literal is reported.
- **Timeouts between 10 and 30 minutes.** Whether a workflow is an extended suite is not visible in
  its YAML, so only the absence of a limit and a value above the 30-minute cap are reported.
- **`branches-ignore`, tag-only `push:`, and concurrency keys.** A `push:` with only `tags:` does not
  double-bill branch work, and a different group key may be right for a deploy workflow.
- **The wrong-branch refinement needs the default branch.** It comes from the event payload; when it
  is unavailable, only the presence of a branch filter is checked.
- **A schedule that is not a trigger.** The word `schedule` in a comment, a job name or a step is
  not a timer. Dependabot's own `schedule:` in `.github/dependabot.yml` is not a workflow trigger
  and is not read.

`on: schedule` is allowed only for the documented ci-standards exception: a hosted monitor with no
local dependencies (no checkout, no `uses: ./`, no self-hosted runner). That workflow must be named
in `HOSTED_MONITOR_SCHEDULES` inside `scripts/workflow-standards.py` as
`owner/repo:.github/workflows/name.yml`. A comment in the workflow does not silence the warning.
The list is empty: the 15–16 Oct 2026 campaign moved operations to auto-vps timers.

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

## Known Gaps & Precision Decisions

Under Automancer CI standards, **precision beats recall**: a check that produces false positives will be ignored or disabled. The following patterns are intentionally out of scope:

1. **Third-Party SDK Clients**: High-level SDKs (e.g. AWS SDK, Stripe, OpenAI, Anthropic, Convex) that implement internal retry policies, exponential backoffs, and client-level timeouts are not flagged.
2. **Streaming Subprocesses**: Asynchronous long-lived subprocesses started via `spawn(...)` without synchronous completion waits (such as background daemons or piped streams with custom lifecycle management) are not flagged by the sync/exec rules.
3. **Internal Helper Wrappers**: Generic wrapper functions accepting an options object (e.g. `function customFetch(url, init)`) are not flagged at caller sites when the helper encapsulates the configuration.
4. **Low-Level Socket APIs**: Direct TCP/TLS socket connections via `net.connect` or `tls.connect` with custom event listeners are not parsed.

For the workflow standards check the precision decisions are listed in
[its own section](#workflow-standards-ci-standards-rules-1-4).

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
own. CI trigger, runner and timeout rules live in the estate's `ci-standards` skill.

### Action Inputs

| Input | Description | Default |
|---|---|---|
| `baseline-commit` | Baseline commit SHA to diff against on pull requests. Auto-detected from `github.event.pull_request.base.sha` on PRs. | `""` |
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
   unmerged. Keep its CI link in the release notes.
3. Record the old `v1` SHA with `git ls-remote --tags origin refs/tags/v1`.
4. Create the next semver tag at that candidate, publish its GitHub release, and
   move `v1` to it. Read both remote tags back and require the candidate SHA.
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

### Automated Updates via Dependabot
Repos pinning specific release tags (e.g. `uses: Automancer-Ltd/estate-checks@v1.0.1`) can configure Dependabot to automatically propose version bumps.

Add to `.github/dependabot.yml` in the caller repository:

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "monthly"
    commit-message:
      prefix: "chore(deps)"
```

## waseem-guard (17 Oct 2026)

`Automancer-Ltd/estate-checks/waseem-guard@v1` fails a pull request that reverts, disables or flips a setting (config files, ENV_STYLE flags) added by a PR Waseemilyas authored or merged in the last 30 days, unless the PR body or a comment says `Waseem-approved: <tracker card id>`. Add it as a job in the repo CI and list it in the `ci gate` needs. Same script runs locally: `python3 waseem-guard/guard.py check --repo ORG/REPO --pr N` (exit 1 refused, 2 undecided = fails closed). Limit: helpers share his GitHub identity, so a PR cannot prove who decided; the merge wrapper in chief-of-staff also verifies the card against the tracker.
