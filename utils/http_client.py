import asyncio
import os
from typing import Optional

import httpx

from utils.metrics import instrument_async_client


_client: Optional[httpx.AsyncClient] = None
_lock = asyncio.Lock()


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


async def init_http_client() -> httpx.AsyncClient:
    global _client
    if _client is not None and not _client.is_closed:
        return _client

    async with _lock:
        if _client is not None and not _client.is_closed:
            return _client

        limits = httpx.Limits(
            max_keepalive_connections=_env_int("HTTP_MAX_KEEPALIVE", 64),
            max_connections=_env_int("HTTP_MAX_CONNECTIONS", 256),
            keepalive_expiry=_env_float("HTTP_KEEPALIVE_EXPIRY", 30.0),
        )
        timeout = httpx.Timeout(
            connect=_env_float("HTTP_TIMEOUT_CONNECT", 5.0),
            read=_env_float("HTTP_TIMEOUT_READ", 15.0),
            write=_env_float("HTTP_TIMEOUT_WRITE", 5.0),
            pool=_env_float("HTTP_TIMEOUT_POOL", 5.0),
        )
        _client = httpx.AsyncClient(
            http2=True,
            limits=limits,
            timeout=timeout,
            follow_redirects=True,
        )
        instrument_async_client(_client, service="shared_http")
        return _client


async def get_http_client() -> httpx.AsyncClient:
    return await init_http_client()


async def close_http_client():
    global _client
    c = _client
    _client = None
    if c is not None and not c.is_closed:
        await c.aclose()
