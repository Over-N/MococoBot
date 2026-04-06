import asyncio
import hashlib
import inspect
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Sequence

import aiomysql

from utils.metrics import observe_db_acquire, observe_db_query, set_db_pool
from utils.request_context import add_db_ms


_POOL: Optional[aiomysql.Pool] = None
_POOL_INIT_LOCK: Optional[asyncio.Lock] = None
logger = logging.getLogger(__name__)

_SLOW_QUERY_MS_DEFAULT = 200.0
_QUERY_SAMPLE_RATE_DEFAULT = 0.05
_READ_ONLY_OPS = {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH"}
_CONNECTION_LOST_MARKERS = (
    "lost connection",
    "gone away",
    "server has gone away",
    "connection was killed",
    "not connected",
    "closed",
)


class DatabaseConnectionError(RuntimeError):
    pass


class DatabaseQueryError(RuntimeError):
    pass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default



def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except Exception:
        return default



def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw



def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}



def _pool_num(v: Any) -> int:
    try:
        if callable(v):
            v = v()
        return int(v or 0)
    except Exception:
        return 0



def _slow_query_ms() -> float:
    return max(1.0, _env_float("METRICS_SLOW_QUERY_MS", _SLOW_QUERY_MS_DEFAULT))



def _query_sample_rate() -> float:
    raw = _env_float("METRICS_QUERY_SAMPLE_RATE", _QUERY_SAMPLE_RATE_DEFAULT)
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return raw



def _query_op(query: str) -> str:
    q = (query or "").lstrip()
    if not q:
        return "OTHER"
    return q.split(None, 1)[0].upper()



def _query_hash(query: str) -> str:
    try:
        norm = " ".join((query or "").split())
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return "na"



def _is_read_only_query(query: str) -> bool:
    return _query_op(query) in _READ_ONLY_OPS



def _is_connection_lost_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _CONNECTION_LOST_MARKERS)



def _normalize_placeholders(query: str, params: Optional[Sequence[Any]]) -> str:
    if params is None or "?" not in query:
        return query

    out: List[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(query)

    while i < n:
        ch = query[i]

        if ch == "'" and not in_double:
            if in_single and i + 1 < n and query[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
            i += 1
            continue

        if ch == '"' and not in_single:
            if in_double and i + 1 < n and query[i + 1] == '"':
                out.append('""')
                i += 2
                continue
            in_double = not in_double
            out.append(ch)
            i += 1
            continue

        if ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1

    return "".join(out)


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


async def _ensure_pool() -> aiomysql.Pool:
    global _POOL, _POOL_INIT_LOCK

    if _POOL is not None and not _POOL.closed:
        return _POOL

    if _POOL_INIT_LOCK is None:
        _POOL_INIT_LOCK = asyncio.Lock()

    async with _POOL_INIT_LOCK:
        if _POOL is not None and not _POOL.closed:
            return _POOL

        host = _env_str("DB_HOST", "127.0.0.1")
        port = _env_int("DB_PORT", 3306)
        user = _env_str("DB_USER", "root")
        password = _env_str("DB_PASSWORD", "")
        db = _env_str("DB_NAME", "")

        maxsize = max(1, _env_int("DB_POOL_SIZE", 20))
        minsize = max(0, _env_int("DB_POOL_MINSIZE", 2))
        if minsize > maxsize:
            minsize = maxsize

        pool_recycle = max(30, _env_int("DB_POOL_RECYCLE", 1800))
        autocommit = _env_bool("DB_AUTOCOMMIT", False)

        _POOL = await aiomysql.create_pool(
            host=host,
            port=port,
            user=user,
            password=password,
            db=db,
            autocommit=autocommit,
            minsize=minsize,
            maxsize=maxsize,
            pool_recycle=pool_recycle,
            charset=_env_str("DB_CHARSET", "utf8mb4"),
            cursorclass=aiomysql.DictCursor,
        )
        size = _pool_num(getattr(_POOL, "size", 0))
        free = _pool_num(getattr(_POOL, "freesize", 0))
        used = max(0, size - free)
        set_db_pool(size=size, free=free, used=used)
        return _POOL


async def close_db_pool() -> None:
    global _POOL

    pool = _POOL
    if pool is None:
        return

    _POOL = None
    try:
        pool.close()
        wait_closed = getattr(pool, "wait_closed", None)
        if wait_closed is not None:
            await _maybe_await(wait_closed())
    except Exception:
        logger.exception("Database pool close error")
    finally:
        set_db_pool(size=0, free=0, used=0)



def get_pool_stats() -> Dict[str, int]:
    pool = _POOL
    if pool is None or getattr(pool, "closed", False):
        stats = {"size": 0, "free": 0, "used": 0, "maxsize": 0, "minsize": 0}
        set_db_pool(size=0, free=0, used=0)
        return stats

    size = _pool_num(getattr(pool, "size", 0))
    free = _pool_num(getattr(pool, "freesize", 0))
    used = max(0, size - free)
    maxsize = _pool_num(getattr(pool, "maxsize", 0))
    minsize = _pool_num(getattr(pool, "minsize", 0))

    set_db_pool(size=size, free=free, used=used)
    return {
        "size": size,
        "free": free,
        "used": used,
        "maxsize": maxsize,
        "minsize": minsize,
    }


class DatabaseManager:
    def __init__(self):
        self.conn: Optional[aiomysql.Connection] = None
        self.cursor: Optional[aiomysql.DictCursor] = None
        self._connected = False
        self._discard_conn = False
        self.lastrowid = 0
        self.rowcount = 0
        self._pool: Optional[aiomysql.Pool] = None

    async def __aenter__(self):
        ok = await self.connect()
        if not ok:
            raise DatabaseConnectionError("Database connection unavailable")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                await self.rollback()
            except Exception:
                logger.exception("Database rollback on context exit failed")
        await self.close()

    async def _ensure_cursor(self) -> bool:
        if self.conn is None:
            return False
        if self.cursor is None or getattr(self.cursor, "closed", False):
            try:
                self.cursor = await self.conn.cursor()
            except Exception:
                self.cursor = None
                return False
        return True

    async def _reset_session(self) -> None:
        if self.conn is None:
            return
        await self.conn.ping()
        if not _env_bool("DB_AUTOCOMMIT", False):
            await self.conn.rollback()

    async def connect(self) -> bool:
        if self._connected and self.conn is not None:
            try:
                await self._reset_session()
                if await self._ensure_cursor():
                    return True
            except Exception:
                self._connected = False
                self._discard_conn = True
                await self.close()

        acquire_started = time.perf_counter()
        try:
            pool = await _ensure_pool()
            acquire_timeout = max(0.1, _env_float("DB_POOL_TIMEOUT", 5.0))
            acquire_started = time.perf_counter()
            conn = await asyncio.wait_for(pool.acquire(), timeout=acquire_timeout)
            observe_db_acquire("ok", (time.perf_counter() - acquire_started) * 1000.0)

            self._pool = pool
            self.conn = conn
            self.cursor = await conn.cursor()
            self._connected = True
            self._discard_conn = False
            self.lastrowid = 0
            self.rowcount = 0

            try:
                await self._reset_session()
            except Exception:
                self._discard_conn = True
                await self.close()
                raise

            get_pool_stats()
            return True
        except Exception:
            observe_db_acquire("error", (time.perf_counter() - acquire_started) * 1000.0)
            logger.exception("Database connection error")
            self._connected = False
            self._discard_conn = True
            return False

    async def _execute_with_metrics(self, query: str, params: Optional[Sequence[Any]], *, fetch: str):
        if not self._connected and not await self.connect():
            raise DatabaseConnectionError("Database connection unavailable")
        if self.conn is None or not await self._ensure_cursor():
            if not await self.connect():
                raise DatabaseConnectionError("Database connection unavailable")

        op = _query_op(query)
        qhash = _query_hash(query)
        started = time.perf_counter()
        status = "ok"

        try:
            sql = _normalize_placeholders(query, params)
            if params is not None:
                await self.cursor.execute(sql, params)
            else:
                await self.cursor.execute(sql)

            self.lastrowid = int(getattr(self.cursor, "lastrowid", 0) or 0)
            self.rowcount = int(getattr(self.cursor, "rowcount", 0) or 0)

            if fetch == "all":
                rows = await self.cursor.fetchall()
                return rows or []
            if fetch == "one":
                return await self.cursor.fetchone()
            return []
        except Exception as exc:
            status = "error"
            if _is_connection_lost_error(exc):
                self._connected = False
                self._discard_conn = True
                logger.warning(
                    "Database connection lost during query op=%s hash=%s read_only=%s",
                    op,
                    qhash,
                    _is_read_only_query(query),
                )
            logger.exception("Database query error")
            raise DatabaseQueryError(str(exc)) from exc
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            add_db_ms(elapsed_ms)
            observe_db_query(op=op, status=status, elapsed_ms=elapsed_ms)
            if elapsed_ms >= _slow_query_ms() and random.random() <= _query_sample_rate():
                logger.warning(
                    "db_slow_query op=%s status=%s elapsed_ms=%.3f rows=%s hash=%s",
                    op,
                    status,
                    elapsed_ms,
                    self.rowcount,
                    qhash,
                )
            get_pool_stats()

    async def execute(self, query: str, params: Optional[Sequence[Any]] = None):
        try:
            return await self._execute_with_metrics(query, params, fetch="all" if _is_read_only_query(query) else "none")
        except DatabaseQueryError:
            if _is_read_only_query(query):
                self._connected = False
                self._discard_conn = True
                await self.close()
                if await self.connect():
                    return await self._execute_with_metrics(query, params, fetch="all")
            raise

    async def fetch_all(self, query: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
        rows = await self._execute_with_metrics(query, params, fetch="all")
        return rows or []

    async def fetch_one(self, query: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
        try:
            row = await self._execute_with_metrics(query, params, fetch="one")
            return row
        except DatabaseQueryError:
            self._connected = False
            self._discard_conn = True
            await self.close()
            if await self.connect():
                return await self._execute_with_metrics(query, params, fetch="one")
            raise

    async def commit(self) -> None:
        if self.conn is None or not self._connected:
            raise DatabaseConnectionError("Database connection unavailable")
        try:
            await self.conn.commit()
        except Exception as exc:
            self._discard_conn = True
            logger.exception("Database commit error")
            raise DatabaseQueryError(str(exc)) from exc

    async def rollback(self) -> None:
        if self.conn is None or not self._connected:
            return
        try:
            await self.conn.rollback()
        except Exception as exc:
            self._discard_conn = True
            logger.exception("Database rollback error")
            raise DatabaseQueryError(str(exc)) from exc

    async def close(self) -> None:
        if self.cursor is not None:
            try:
                await _maybe_await(self.cursor.close())
            except Exception:
                logger.debug("Database cursor close failed", exc_info=True)
            self.cursor = None

        if self.conn is not None:
            conn = self.conn
            self.conn = None
            pool = self._pool
            self._pool = None

            try:
                if not _env_bool("DB_AUTOCOMMIT", False):
                    try:
                        await conn.rollback()
                    except Exception:
                        self._discard_conn = True
                        logger.debug("Database rollback during close failed", exc_info=True)
                if self._discard_conn or getattr(conn, "closed", False):
                    await _maybe_await(conn.close())
                else:
                    if pool is not None and not pool.closed:
                        await _maybe_await(pool.release(conn))
                    else:
                        await _maybe_await(conn.close())
            except Exception:
                try:
                    await _maybe_await(conn.close())
                except Exception:
                    logger.debug("Database connection close failed", exc_info=True)

        self._connected = False
        self._discard_conn = False
        get_pool_stats()


@asynccontextmanager
async def get_db():
    db = DatabaseManager()
    try:
        ok = await db.connect()
        if not ok:
            raise DatabaseConnectionError("Database connection unavailable")
        yield db
    finally:
        await db.close()
