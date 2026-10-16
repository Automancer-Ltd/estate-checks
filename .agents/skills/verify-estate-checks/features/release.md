# Publish a shared action

The release journey is the README's release procedure. Consumers running `@v1`
execute the pointed commit; source on main alone changes nothing for them.

Prove the candidate in an owned consumer PR with both actions pinned to its SHA.
Assert the emitted tier, selected tests and scanner findings in the runner.
Close the proof PR unmerged. Publish a semver release and move `v1` only after
this repo's CI and that consumer proof pass. Read both tags back, then run the
consumer proof with `@v1` and confirm the downloaded SHA in its logs.

Retain the consumer CI links in the release and PR. Keep the previous `v1` SHA
for rollback. Gotcha: the local driver substitutes the GitHub API and checkout;
it cannot prove hosted permissions or the action downloader.
