"""Application-level API errors with stable machine-readable codes."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra or {}


def bad_request(code: str, message: str) -> AppError:
    return AppError(400, code, message)


def unauthorized(message: str = "登录状态已失效，请重新碰触 NTAG 标签") -> AppError:
    return AppError(401, "not_authenticated", message)


def forbidden(code: str, message: str) -> AppError:
    return AppError(403, code, message)


def not_found(code: str, message: str) -> AppError:
    return AppError(404, code, message)


def conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


def too_many(message: str = "操作过于频繁，请稍后再试", retry_after: int = 60) -> AppError:
    return AppError(
        429,
        "rate_limited",
        message,
        headers={"Retry-After": str(retry_after)},
    )
