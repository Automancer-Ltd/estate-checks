# Estate Checks

Shared checks and test selection for repositories on the Automancer estate.

## Language

**Consumer**: A repository that invokes one of the Estate Checks actions.

**Scanner**: A check that reports unbounded outside calls or workflow-standard
warnings in a consumer. A finding warns by default.

**Install gate**: The install-timeout check, which fails a consumer's run when
a workflow step installs packages with no `timeout-minutes` on the step or its
job. Its findings never warn: the failure it prevents is a red, unlanded
release PR.

**Tier**: The amount of CI a change requires: `none` skips work already proved or
unneeded; `core` runs quick checks and direct tests; `standard` runs the full suite.
_Avoid_: quick tier, full tier (the output values are `core` and `standard`).

**Direct tests**: Tests that name changed code, plus changed tests themselves.
They do not include every test reachable through transitive imports.

**Reference key**: A path or module name by which a test identifies changed code.
A Convex generated API name is also a reference key.

**Tested tree**: The exact Git tree recorded by a successful consumer PR run.
A later default-branch push can reuse that proof only for the same tree.

**Full paths**: Consumer patterns that add to the files requiring `standard`,
even for a small change.

**Documentation include**: Optional globs that count as documentation even when
they match the built-in agent or skill excludes. They cannot reclassify product,
content, page or test markdown, and code that reads an included file still
counts as code.
