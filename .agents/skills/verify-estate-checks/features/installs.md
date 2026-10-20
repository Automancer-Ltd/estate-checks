# Fail unbounded installs

A consumer invokes `Automancer-Ltd/estate-checks@v1`; the driver runs the
actual composite-action shell steps, including the install-timeout gate.

`install-unbounded` writes a workflow whose step runs `pip install` and
`npm ci` with no `timeout-minutes` on the step or the job. The action exits 1
with an `install-unbounded` error annotation on each install line, a failing
job-summary row, and `install-timeout-count=2` — the run is red, which is the
check's purpose (tracker t4456: the automancer-ops release check was killed
4m38s into an unbounded `pip` install on 19 Oct 2026).

`install-bounded` adds `timeout-minutes: 3` to the same step and exits 0 with
`install-timeout-count=0`. `install-skip` keeps the unbounded workflow and
sets `check-installs: 'false'`; the step is skipped and the run stays green.

Gotchas: the gate ignores `fail-on-findings` — it blocks by default — and
PyYAML availability is probed the same way the workflow-standards step probes
it. Wildcards and the malformed-row failure are owned by
`scripts/test-install-timeouts.py`, which also runs in CI.
