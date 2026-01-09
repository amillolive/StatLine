# typings/fastapi/__init__.pyi
from typing import Any, Callable, TypeVar

T = TypeVar("T")

class FastAPI:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def get(self, path: str, *args: Any, **kwargs: Any) -> Callable[[Callable[..., T]], Callable[..., T]]: ...
    def post(self, path: str, *args: Any, **kwargs: Any) -> Callable[[Callable[..., T]], Callable[..., T]]: ...
    def include_router(
        self,
        router: Any,
        *,
        prefix: str | None = ...,
        tags: list[str] | None = ...,
        dependencies: Any | None = ...,
        responses: Any | None = ...,
        default_response_class: Any | None = ...,
        callbacks: Any | None = ...,    
        ) -> None: ...


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any = ...) -> None: ...
