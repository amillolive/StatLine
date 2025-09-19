# typings/fastapi/responses.pyi
from typing import Any

class JSONResponse:
    def __init__(self, content: Any, status_code: int = 200) -> None: ...
