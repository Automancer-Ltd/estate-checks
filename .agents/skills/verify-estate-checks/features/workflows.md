# Report workflow standards

The scanner action also checks the consumer's workflow files and exposes
`workflow-warnings-count`. The `warn` case has an unfiltered push, no concurrency,
a literal organisation runner and no job timeout. It succeeds with four workflow
warnings. `bounded` reports zero. `skip-workflows` uses `check-workflows: false`
and reports zero while its unbounded-call scanner still reports four findings.

The count, annotations and job summary prove the result. Workflow warnings stay
warn-only; `fail-on-findings` controls only the unbounded-call scanner. YAML parse
errors, anchors, reusable jobs and precision exceptions are independently guarded
by the existing `scripts/test-workflow-standards.py`, run in CI.
