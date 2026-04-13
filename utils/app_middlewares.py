import logging
import time
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from utils.app_settings import settings
from utils.metrics import observe_http_request
from utils.request_context import add_auth_ms, add_body_ms, add_json_ms, get_request_context, reset_request_context, set_request_context

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""


def _request_key(request: Request) -> str:
    keys = ("user_id", "room_id", "guild_id", "party_id", "match_id")
    path_params = getattr(request, "path_params", {}) or {}
    query_params = request.query_params
    parts = [f"{request.method} {request.url.path}"]
    for key in keys:
        value = path_params.get(key)
        if value is None:
            value = query_params.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return " ".join(parts)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        if path in settings.exempt_paths:
            return await call_next(request)

        client_ip = _get_client_ip(request)
        user_agent = request.headers.get("user-agent", "").lower()

        if any(path.startswith(prefix) for prefix in settings.be_doc_paths):
            if client_ip not in settings.be_admin_ips:
                return JSONResponse({"detail": "Access denied (BE docs)"}, status_code=403)

        if not user_agent or len(user_agent) < 10:
            return Response(status_code=403, content="")
        if any(blocked in user_agent for blocked in settings.blocked_user_agents):
            return Response(status_code=403, content="")
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        token = set_request_context({
            "db_ms": 0.0,
            "http_ms": 0.0,
            "body_ms": 0.0,
            "json_ms": 0.0,
            "auth_ms": 0.0,
        })
        status_code = 500

        original_body = request.body
        original_json = request.json

        async def _timed_body():
            body_started = time.perf_counter()
            try:
                return await original_body()
            finally:
                add_body_ms((time.perf_counter() - body_started) * 1000.0)

        async def _timed_json():
            json_started = time.perf_counter()
            try:
                return await original_json()
            finally:
                add_json_ms((time.perf_counter() - json_started) * 1000.0)

        request.body = _timed_body
        request.json = _timed_json

        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            route = request.scope.get("route")
            path_label = getattr(route, "path", None) or request.url.path
            method = request.method.upper()
            observe_http_request(method, path_label, status_code, elapsed_ms)

            req_ctx = get_request_context() or {}
            logger.info(
                "request_summary method=%s path=%s status=%s elapsed_ms=%.3f db_ms=%.3f http_ms=%.3f body_ms=%.3f json_ms=%.3f auth_ms=%.3f key=%s",
                method,
                path_label,
                status_code,
                elapsed_ms,
                float(req_ctx.get("db_ms", 0.0) or 0.0),
                float(req_ctx.get("http_ms", 0.0) or 0.0),
                float(req_ctx.get("body_ms", 0.0) or 0.0),
                float(req_ctx.get("json_ms", 0.0) or 0.0),
                float(req_ctx.get("auth_ms", 0.0) or 0.0),
                _request_key(request),
            )
            reset_request_context(token)
