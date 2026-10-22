---
name: verify-estate-checks
description: Verify Estate Checks changes or investigate wrong CI tiers, selected tests, scanner warnings or scan failures. Drives both action manifests against synthetic consumer repositories and retains commands, outputs and logs.
---

# Verify Estate Checks

Last verified: 2026-10-16 on auto-vps.

## Launch

From a clean checkout, run `uv sync --frozen` and `checkq -- scripts/check`.
Then drive every mapped action journey:

```bash
checkq -- uv tool run --python 3.12 --from semgrep==1.178.0 --with PyYAML python scripts/verify-estate-checks.py --evidence .audit/verify-run
```

Use a new evidence directory on a repeat; the driver refuses to overwrite one.
If every scan journey says `Semgrep failed with exit code 2` and a direct `semgrep scan
--baseline-commit` prints `io_uring_queue_init: Cannot allocate memory`, the host refused
io_uring: prefix the command with `EIO_BACKEND=posix` (auto-vps, 22 Oct 2026).
The command uses an isolated cached tool environment. It leaves the host's
Semgrep installation untouched. In CI the same command runs without `checkq`.

## Doctor

The driver checks the pinned Semgrep version, reads both manifests and records
source SHA, working changes and source hashes in `doctor.json`. A clean proof has
an empty `working_changes` field. Missing tools or an incorrect version fail
before any scan. `results.json` exists only after every journey passes.

## Drive

The [feature map](features/index.md) owns the journeys and expected outcomes.
`scripts/verify-estate-checks.py` executes the Bash `run` blocks and public output
expressions from `action.yml` and `tier/action.yml`. It imports no product internals.
Each case gets its own committed Git consumer. Tier plan requests go through the
real HTTP client to an owned loopback GitHub API fixture; scan reads those files.
Semgrep itself runs against bounded and unbounded TypeScript, Python, shell and
workflow files. None of the fixture network calls executes.

Local substitutions: GitHub REST responses are synthetic, and `actions/checkout`
is represented by the owned fixture checkout. Hosted token permissions, checkout
and composite-expression execution require a real consumer PR. Before moving
`v1`, pin the candidate in a disposable consumer PR as `README.md` describes.
A local proof alone does not authorize that release step.

## Evidence

Keep the chosen evidence directory. It contains `doctor.json`, `results.json`,
the executed shell blocks, per-step logs and outputs, event payloads and summaries.
CI uploads it as `action-proof` for 14 days. Put the CI URL and relevant output in
the PR body so the review record survives artifact expiry and worktree cleanup.

To prove discrimination, copy the source into an owned disposable checkout,
change one tier or scanner decision, run this path and require the corresponding
assertion to fail. Restore the original bytes and require the path to pass.

## Cleanup

The driver removes only its temporary consumers and shuts down its loopback API
in `finally`, including on failures. It joins the server thread and checks no
fixture checkout remains. Logs and evidence stay in place. No dev server or
watcher is started. Confirm `results.json` remains after successful cleanup.
