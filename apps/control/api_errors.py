"""One error shape for the website's API: {"message": "...", "code": "..."} (what web/src/lib/api.ts reads)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra, self.headers = status, code, message, extra or {}, headers


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, e: ApiError) -> JSONResponse:
        return JSONResponse({"message": e.message, "code": e.code, **e.extra}, status_code=e.status,
                            headers={"Cache-Control": "no-store", **(e.headers or {})})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, e: StarletteHTTPException) -> JSONResponse:
        # internal endpoints keep FastAPI's {"detail": ...}; the public API speaks {message, code}
        if request.url.path.startswith("/api/"):
            message = e.detail if isinstance(e.detail, str) else "Request failed."
            return JSONResponse({"message": message, "code": f"http_{e.status_code}"}, status_code=e.status_code)
        return JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, e: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"message": "Some details are missing or invalid.", "code": "invalid_request"},
                                status_code=422)
        return JSONResponse({"detail": jsonable_encoder(e.errors())}, status_code=422)
