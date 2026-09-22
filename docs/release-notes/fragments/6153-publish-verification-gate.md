## New `publish_verification` quality gate

A step could claim it publishes while also claiming its own verification
was skipped or only possible remotely -- an explicit admission that
nothing actually checked the publish claim. No other gate looked at that
combination, so it passed through unchallenged.

Add `publish_verification` to a pipeline's gate list to block a changed
`publish.json` that declares `skip_verification` or `remote_only`. A stub
verification function (`raise NotImplementedError(...)`) elsewhere in the
same changeset is reported alongside the finding as corroborating detail,
but never triggers one on its own (#6153).
