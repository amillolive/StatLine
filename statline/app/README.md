# StatLine App

Canonical application-layer source for StatLine.

The canonical source lives in the **StatLine** monorepo at `statline/app`. The standalone **StatLine-app** repository is a generated subtree mirror; development should occur in the monorepo.

## Dependency contract

App may import `statline.core`. Gateway-specific behavior must remain behind the Gateway capability so `pip install "statline[app]"` does not require the API/server stack.

Canonical installs are owned by the parent repository:

- `pip install "statline[app]"` — Core + App
- `pip install "statline[all]"` — Core + App + Gateway
- `pip install "statline[dev]"` — all capabilities + development/test tooling
