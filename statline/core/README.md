# StatLine Core

Canonical Core source for StatLine.

The canonical source lives in the **StatLine** monorepo at `statline/core`. The standalone **StatLine-core** repository is a generated subtree mirror; development should occur in the monorepo.

## Dependency contract

Core is the bottom of the StatLine dependency graph. Code in this component **must not import `statline.app` or `statline.gateway`**.

Canonical installs are owned by the parent repository:

- `pip install statline` — Core
- `pip install "statline[app]"` — Core + App capability
- `pip install "statline[gateway]"` — Core + Gateway capability
- `pip install "statline[all]"` — Core + App + Gateway capabilities
- `pip install "statline[dev]"` — all capabilities + development/test tooling
