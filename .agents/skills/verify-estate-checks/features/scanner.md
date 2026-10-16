# Report unbounded calls

A consumer invokes `Automancer-Ltd/estate-checks@v1` after a full-history checkout.
The driver runs the actual setup and scanner shell steps in owned Git consumers.

`bounded` emits zero findings. `warn` adds one unbounded TypeScript fetch, Python
request, shell curl and workflow curl. It succeeds with four findings, each rule
named in the captured annotations. `strict` fails on those findings.
`unavailable` warns on a missing rules file; `strict-unavailable` fails visibly.
`excluded-server` scans only the two remaining unbounded calls and warns that
server files were hidden. Outputs and summary rows are captured beside the logs.

Gotchas: default test exclusions are intentional. Custom rules use `rules-path`;
its missing-file branch is exercised here. The complete thirteen-rule precision
fixtures run separately in CI with `semgrep --test --legacy rules/`. Summary
fatal/empty/missing-result and deduplication regressions remain owned by
`scripts/test-validation.py`, also executed in CI.
