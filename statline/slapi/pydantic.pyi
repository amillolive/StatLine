from typing import Any, ClassVar, Dict, Generic, TypeVar

T = TypeVar("T")

class BaseModel:
    model_config: ClassVar[Dict[str, Any]]
    __root__: Any


T_co = TypeVar("T_co", covariant=True)

def Field(*args: Any, **kwargs: Any) -> Any: ...
def ConfigDict(**kwargs: Any) -> Dict[str, Any]: ...

class RootModel(Generic[T], BaseModel):
    root: T
