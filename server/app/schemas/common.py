"""统一 API 响应结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

CODE_OK = 0
CODE_AUTH_ERROR = 1001
CODE_PARAM_ERROR = 1002
CODE_NOT_FOUND = 1003
CODE_CONFLICT = 1004
CODE_RATE_LIMIT = 1005
CODE_SERVER_ERROR = 1006


class ApiResponse(BaseModel, Generic[T]):
    code: int = CODE_OK
    message: str = "ok"
    data: T | None = None


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": CODE_OK, "message": message, "data": data}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}
