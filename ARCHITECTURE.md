# StatLine Rebase Architecture

Every Python module owns exactly one kind of source:

- **Definitions**: classes, protocols, typed records, exceptions, and models. These modules do not declare top-level functions.
- **Functions**: behavior and callable entry points. These modules do not declare top-level classes.
- **Code**: imports, public re-exports, package wiring, or executable launch code. These modules do not define classes, functions, or lambdas.

The supported runtime layers are:

- `statline.core`: adapters, datasets, scoring, shared types, and small tools.
- `statline.gateway`: HTTP, authentication, adapter discovery, and persistence.
- `statline.app`: one-shot CLI, persistent StatLine OS session/TUI, server runner, and developer maintenance entry points.
- `statline.public` / `statline.__init__`: the stable local Python API.

Concurrency follows an I/O-boundary rule rather than making the scoring core async:

- `statline.core` remains synchronous and deterministic for adapter compilation, mapping, and scoring.
- persistent clients use pooled `httpx2.AsyncClient` connections so network waits do not block the UI/application loop.
- the FastAPI score route moves synchronous dataset/scoring work to Starlette's thread pool.
- SLAPI can run multiple Uvicorn worker processes (`SLAPI_WORKERS`) for CPU isolation and parallel request handling.

Deprecated adapter schemas remain packaged for explicit local compatibility paths, but they are excluded from registry discovery, sniffing, CLI lists, and API resource catalogs.

Legacy `statline.slapi`, flat scoring/dataset modules, `statline.tui`, `statline.services`, and `statline.utils` are removed rather than forwarded. No compatibility shims are part of the rebase.
