# StatLine App

Canonical application-layer source for StatLine.

This repository is mounted into the parent **StatLine** repository at `statline/app` as a Git submodule. It is source-owned separately; packaging and releases are composed by the parent repository.

## Dependency contract

App may import `statline.core`. Gateway-specific behavior must remain behind the Gateway capability so `pip install "statline[app]"` does not require the API/server stack.

Canonical installs are owned by the parent repository:

- `pip install "statline[app]"` — Core + App
- `pip install "statline[gateway]"` — Core + App + Gateway
- `pip install "statline[dev]"` — all capabilities + development/test tooling
