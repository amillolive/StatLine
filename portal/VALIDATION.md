# rc6 validation

This rc6 revision updates the Python Gateway/scoring diagnostics, portal,
launchers, release tooling, and documentation together. The authoritative
completed checks are recorded in the root `RC6_VERIFICATION.md`.

The original 118 tests and new contract, identity, attribution, CORS, packaging,
and actual joint-process tests are included. Original default scoring output is
protected by hashes from the supplied rc6 player/team fixtures. Generated
examples compare expanded compact responses with local SDK/StatPack scoring.

Browser interaction and visual QA were not completed for this revision: the
available browser could not connect to the local preview, and the local browser
binary download was unavailable. Previous browser-validation claims do not
apply to this revised build. Actual deployed permission prompts, hostname
routing, and native Windows execution require deployment/platform checks.
