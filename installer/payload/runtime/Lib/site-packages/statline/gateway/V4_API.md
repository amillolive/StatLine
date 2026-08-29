# StatLine Gateway API v4

StatLine v4 reduces adapter discovery, dataset access, and scoring to a small resource-oriented API. The server no longer exposes separate mapping, PRI, row-score, and batch-score endpoints.

## Core routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/v4/health` | Public health and adapter-cache status |
| `GET` | `/v4/adapters` | List canonical adapters |
| `GET` | `/v4/adapters/{adapter}` | Read one complete adapter document |
| `POST` | `/v4/adapters/sniff` | Match adapters from CSV/object headers |
| `GET` | `/v4/datasets` | List packaged CSV datasets |
| `GET` | `/v4/datasets/{dataset}` | Read a packaged CSV as paginated JSON |
| `POST` | `/v4/score` | Map, filter, and score one row, many rows, or a packaged dataset |

Interactive documentation is available at `/docs`; ReDoc is available at `/redoc`; the OpenAPI document is available at `/openapi.json`.

## Authentication

Protected routes accept a portable StatLine API key:

```http
Authorization: Bearer api_...
```

Device enrollment and API-key ownership routes use the documented `X-SL-*` proof headers.

## Two-step dataset scoring

### 1. Read the CSV as JSON

```http
GET /v4/datasets/DEMO/demo.csv?offset=0&limit=100
Authorization: Bearer api_...
```

The response includes `columns`, pagination metadata, and `rows`.

### 2. Submit those rows to the scorer

```http
POST /v4/score
Authorization: Bearer api_...
Content-Type: application/json

{
  "adapter": "demo",
  "rows": [
    {"row_type": "player", "player_id": "..."}
  ],
  "include_mapped": true
}
```

The response reports the adapter/version, source type, input/mapped/scored counts, scored `results`, and optionally the internally mapped rows.

## Direct packaged-dataset scoring

The same operation can load and score a packaged dataset server-side:

```json
{
  "adapter": "demo",
  "dataset": "DEMO/demo.csv",
  "dataset_limit": 100
}
```

## Unified score request

Provide exactly one source:

- `row`: one object
- `rows`: an array of objects
- `dataset`: a path returned by `GET /v4/datasets`

Raw input is the default and follows `raw -> map -> mapped filters -> score`. Set `input_kind` to `mapped` only when the request already contains adapter metric rows. `caps_mode` supports shared batch context (`batch`) or independent row scoring (`row`).

## v3 route migration

| Former route family | v4 replacement |
| --- | --- |
| `/v3/map/row`, `/v3/map/batch` | `POST /v4/score` with `include_mapped: true` |
| `/v3/calc/pri*` | `POST /v4/score` with `input_kind: "mapped"` |
| `/v3/pri*`, `/v3/score*` | `POST /v4/score` with raw `row` or `rows` |
| `/v3/adapter/{adapter}/weights`, `/inputs`, `/filters`, `/dimensions`, `/spec`, etc. | `GET /v4/adapters/{adapter}` |
| `/v3/datasets` | `GET /v4/datasets` |

The CLI keeps its existing command vocabulary, but its HTTP transport translates those commands to the v4 resource model. The server itself does not register v3 compatibility routes.

## Adapter cache lifecycle

At FastAPI startup, StatLine discovers every packaged adapter, parses each source specification once, compiles each adapter once, and stores both objects in the process-wide core registry. Normal metadata and scoring requests only resolve cached objects. `POST /v4/admin/adapters/refresh` performs an explicit atomic reload.
