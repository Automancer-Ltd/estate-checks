# Select the right tests

A consumer runs the JSON paths in the tier action's `direct-tests` output.
The `small` case selects `tests/cost.test.ts` by its direct import. The `convex`
case selects `tests/access.test.ts` by `api.feedback.submit`. The `unmatched`
case returns `standard` and an empty direct-test list for an uncovered Convex
module. Exact expected lists are asserted at the manifest output.

Gotchas: these are fixture source files, not executed consumer test suites.
The real consumer proof checks actual runner wiring. Sparse checkout must retain
all sources and tests; missing files can hide references. Removed/renamed files,
relative imports, test-count limits and internal API variants have existing
coverage in `tier/test_classify.py`, executed by CI.
