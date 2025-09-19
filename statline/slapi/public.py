# statline/slapi/public.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Mapping, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from statline.core.adapters import list_names

from . import scoring as svc
from .schemas import (
    ScoreBatchRequestModel,
    ScoreBatchResponseModel,
    ScoreRowRequestModel,
    ScoreRowResponseModel,
)

APP_TITLE = "StatLine API (SLAPI)"
APP_VERSION = "0.1.0"

# ——— Typed helpers that hide the missing-stub attribute `model_validate` ———
if TYPE_CHECKING:
    def _row_model(data: dict[str, Any]) -> ScoreRowResponseModel: ...
    def _batch_model(data: list[dict[str, Any]]) -> ScoreBatchResponseModel: ...
else:
    _row_model = ScoreRowResponseModel.model_validate  # type: ignore[attr-defined]
    _batch_model = ScoreBatchResponseModel.model_validate  # type: ignore[attr-defined]


def create_app() -> FastAPI:
    app = FastAPI(title=APP_TITLE, version=APP_VERSION)

    @app.get("/health")
    def health() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": True}

    @app.get("/adapters", response_model=List[str], summary="List available adapters")
    def adapters() -> List[str]:  # pyright: ignore[reportUnusedFunction]
        try:
            return list_names()
        except Exception as e:  # pragma: no cover
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/score", response_model=ScoreRowResponseModel)
    def score_row(payload: ScoreRowRequestModel) -> ScoreRowResponseModel:  # pyright: ignore[reportUnusedFunction]
        try:
            req = svc.ScoreRowRequest(
                adapter=payload.adapter,
                row=payload.row,
                weights_override=payload.weights_override,
                context=payload.context,
                caps_override=payload.caps_override,
            )
            result: dict[str, Any] = svc.score_row(req)
            return _row_model(result)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # pragma: no cover
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/score/batch", response_model=ScoreBatchResponseModel)
    def score_batch(payload: ScoreBatchRequestModel) -> ScoreBatchResponseModel:  # pyright: ignore[reportUnusedFunction]
        try:
            # List is invariant; cast Dict[...] -> Mapping[...] for the service layer
            rows_as_mappings = cast(List[Mapping[str, Any]], payload.rows)
            req = svc.ScoreBatchRequest(
                adapter=payload.adapter,
                rows=rows_as_mappings,
                weights_override=payload.weights_override,
                context=payload.context,
                caps_override=payload.caps_override,
            )
            results: list[dict[str, Any]] = svc.score_batch(req)
            return _batch_model(results)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # pragma: no cover
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/", include_in_schema=False)
    def root_index() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"name": APP_TITLE, "version": APP_VERSION})

    return app


app = create_app()

if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run("statline.slapi.public:app", host="0.0.0.0", port=8000, reload=True)
