# Automancer Estate Checks

Shared mechanical CI checks for the Automancer estate, centralized in a single repository and invoked by version tag across all estate projects.

The check inspects code for **unbounded outside network and subprocess calls**—the estate's most repeated failure pattern, where an unconstrained supplier call, hanging HTTP request, or blocked subprocess halts a background job, locks an event loop, freezes a user-facing page, or consumes entire CI runner quotas.

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
  - *Fix*: Pass `timeout: <milliseconds>` in options or register a `req.setTimeout(...)` handler.

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

## Known Gaps & Precision Decisions

Under Automancer CI standards, **precision beats recall**: a check that produces false positives will be ignored or disabled. The following patterns are intentionally out of scope:

1. **Third-Party SDK Clients**: High-level SDKs (e.g. AWS SDK, Stripe, OpenAI, Anthropic, Convex) that implement internal retry policies, exponential backoffs, and client-level timeouts are not flagged.
2. **Streaming Subprocesses**: Asynchronous long-lived subprocesses started via `spawn(...)` without synchronous completion waits (such as background daemons or piped streams with custom lifecycle management) are not flagged by the sync/exec rules.
3. **Internal Helper Wrappers**: Generic wrapper functions accepting an options object (e.g. `function customFetch(url, init)`) are not flagged at caller sites when the helper encapsulates the configuration.
4. **Low-Level Socket APIs**: Direct TCP/TLS socket connections via `net.connect` or `tls.connect` with custom event listeners are not parsed.

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

### Test Files Excluded by Default
By default, test directories and test files (`*.test.*`, `*_test.py`, `tests/**`, `__tests__/**`) are excluded from scanning. Test suites are already bounded by the test runner's global timeout (e.g. Jest, Vitest, pytest), so testing subprocesses and assertions does not require duplicate manual timeouts. To customize or disable this exclusion, override `exclude-test-paths`.

---

## Adoption

Repos adopt the check via the GitHub Actions composite action.

### GitHub Actions Workflow Snippet

Add `.github/workflows/estate-checks.yml` to the adopting repository:

```yaml
name: Estate Checks

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  unbounded-calls:
    name: check unbounded outside calls
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 5
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          # fetch-depth: 0 is required so the PR baseline commit can be resolved for differential scanning
          fetch-depth: 0

      - name: Run Estate Checks
        uses: Automancer-Ltd/estate-checks@v1

  ci-gate:
    name: ci gate
    if: always()
    needs: [unbounded-calls]
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 5
    steps:
      - name: Assert gate passed
        env:
          RESULTS: ${{ toJSON(needs) }}
        run: |
          set -euo pipefail
          if printf '%s' "$RESULTS" | grep -qE '"result"[[:space:]]*:[[:space:]]*"(failure|cancelled|timed_out|action_required|startup_failure|neutral)"'; then
            echo "::error::CI gate failed: estate checks did not pass."
            exit 1
          fi
          echo "CI gate green."
```

### Action Inputs

| Input | Description | Default |
|---|---|---|
| `baseline-commit` | Baseline commit SHA to diff against on pull requests. Auto-detected from `github.event.pull_request.base.sha` on PRs. | `""` |
| `exclude-paths` | Comma- or newline-separated paths or glob patterns to exclude from scanning (e.g. browser UI folders). | `""` |
| `exclude-test-paths` | Comma- or newline-separated test paths or globs to exclude (set to empty to disable default test exclusions). | `'*.test.*, *_test.py, tests/**, __tests__/**'` |
| `fail-on-findings` | Failure policy: `auto` (fail PR, report push), `true`, or `false`. | `auto` |
| `rules-path` | Custom path to Semgrep rules file. | Bundled `rules/unbounded-calls.yml` |
| `semgrep-version` | Pinned Semgrep CLI version. | `1.178.0` |

### Pull Requests vs. Default Branch Behavior
- **Pull Requests**: The action automatically detects `github.event.pull_request.base.sha` and invokes Semgrep with `--baseline-commit`. **Only newly introduced unbounded calls fail the PR.** Existing baseline findings in the repository are ignored, enabling incremental adoption without blocking ongoing work.
  > [!IMPORTANT]
  > Differential scanning requires a resolvable base commit in git history. `actions/checkout` must be configured with `fetch-depth: 0`. If the baseline commit cannot be resolved on a pull request, the action fails immediately with an actionable error rather than falling back to a full repository scan.
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
- Releases follow Semantic Versioning (e.g. `v1.0.1`).
- Each release moves the corresponding major tag pointer (e.g. `v1` points to the latest `v1.x.x`).

### Automated Updates via Dependabot
Repos pinning specific release tags (e.g. `uses: Automancer-Ltd/estate-checks@v1.0.1`) can configure Dependabot to automatically propose version bumps.

Add to `.github/dependabot.yml` in the caller repository:

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(deps)"
```
