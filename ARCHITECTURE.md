# StatLine Rebase Architecture

Every Python module owns exactly one kind of source:

- **Definitions**: classes, protocols, typed records, exceptions, and models. These modules do not declare top-level functions.
- **Functions**: behavior and callable entry points. These modules do not declare top-level classes.
- **Code**: imports, public re-exports, package wiring, or executable launch code. These modules do not define classes, functions, or lambdas.

The supported runtime layers are:

- `statline.core`: adapters, datasets, scoring, shared types, and small tools.
- `statline.gateway`: HTTP, authentication, adapter discovery, and persistence.
- `statline.app`: CLI, server runner, optional TUI, and developer maintenance entry points.
- `statline.public` / `statline.__init__`: the stable local Python API.

Legacy `statline.slapi`, flat scoring/dataset modules, `statline.tui`, `statline.services`, and `statline.utils` are removed rather than forwarded. No compatibility shims are part of the rebase.
