# StatLine Gateway

Canonical Gateway source for StatLine.

The canonical source lives in the **StatLine** monorepo at `statline/gateway`. The standalone **StatLine-gateway** repository is a generated subtree mirror; development should occur in the monorepo.

## Dependency contract

Gateway may import `statline.core`. App integration must remain optional: the Gateway dependency set does not include the App dependency set, and either capability must remain usable without the other.

Canonical installs are owned by the parent repository:

- `pip install "statline[gateway]"` — Core + Gateway
- `pip install "statline[all]"` — Core + App + Gateway
- `pip install "statline[dev]"` — all capabilities + development/test tooling
