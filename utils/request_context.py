import contextvars
from typing import Any, Dict, Optional


_REQUEST_CTX: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "request_ctx",
    default=None,
)


def set_request_context(ctx: Dict[str, Any]) -> contextvars.Token:
    return _REQUEST_CTX.set(ctx)


def reset_request_context(token: contextvars.Token) -> None:
    _REQUEST_CTX.reset(token)


def get_request_context() -> Optional[Dict[str, Any]]:
    return _REQUEST_CTX.get()


def _add_ms(key: str, value_ms: float) -> None:
    ctx = _REQUEST_CTX.get()
    if not ctx:
        return
    ctx[key] = float(ctx.get(key, 0.0) or 0.0) + max(0.0, float(value_ms or 0.0))


def add_db_ms(value_ms: float) -> None:
    _add_ms("db_ms", value_ms)


def add_http_ms(value_ms: float) -> None:
    _add_ms("http_ms", value_ms)


def add_body_ms(value_ms: float) -> None:
    _add_ms("body_ms", value_ms)


def add_json_ms(value_ms: float) -> None:
    _add_ms("json_ms", value_ms)


def add_auth_ms(value_ms: float) -> None:
    _add_ms("auth_ms", value_ms)

