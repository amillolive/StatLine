from typing import Any, ClassVar, Dict, Generic, TypeVar

from .functions import ConfigDict as ConfigDict
from .functions import Field as Field

T = TypeVar("T")

class BaseModel:
    model_config: ClassVar[Dict[str, Any]]
    __root__: Any

class RootModel(Generic[T], BaseModel):
    root: T
