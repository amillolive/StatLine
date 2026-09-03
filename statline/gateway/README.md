# StatLine Gateway

Canonical Gateway source for StatLine.

This repository is mounted into the parent **StatLine** repository at `statline/gateway` as a Git submodule. It is source-owned separately; packaging and releases are composed by the parent repository.

## Dependency contract

Gateway may import `statline.core` and may integrate with `statline.app` where the Gateway capability supports it. The canonical Gateway extra is a superset of the App dependency set.

Canonical installs are owned by the parent repository:

- `pip install "statline[gateway]"` — Core + App + Gateway
- `pip install "statline[dev]"` — all capabilities + development/test tooling
