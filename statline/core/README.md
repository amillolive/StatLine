# StatLine Core

Canonical Core source for StatLine.

This repository is mounted into the parent **StatLine** repository at `statline/core` as a Git submodule. It is intentionally source-owned separately while the parent repository remains the release-composition and `pip install statline...` surface.

## Dependency contract

Core is the bottom of the StatLine dependency graph. Code in this component **must not import `statline.app` or `statline.gateway`**.

Canonical installs are owned by the parent repository:

- `pip install statline` — Core
- `pip install "statline[app]"` — Core + App capability
- `pip install "statline[gateway]"` — Core + App + Gateway capability
- `pip install "statline[dev]"` — all capabilities + development/test tooling
