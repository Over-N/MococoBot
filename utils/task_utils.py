import asyncio
import logging
import os
from typing import Coroutine, Any, Optional

from utils.metrics import (
    inc_bg_task_failed,
    inc_bg_task_started,
    inc_bg_task_timeout,
    set_bg_task_pending,
)


_bg_tasks: set[asyncio.Task[Any]] = set()
_bg_tasks_by_key: dict[str, asyncio.Task[Any]] = {}
_MAX_BG_TASKS = max(100, int(os.getenv("BG_TASK_LIMIT", "300")))
_DEFAULT_DRAIN_TIMEOUT_SEC = max(0.1, float(os.getenv("BG_TASK_DRAIN_TIMEOUT_SEC", "5")))
_DEFAULT_TASK_TIMEOUT_SEC = max(0.1, float(os.getenv("BG_TASK_TIMEOUT_SEC", "20")))
logger = logging.getLogger(__name__)


def _close_coro_safely(coro: Coroutine[Any, Any, Any]) -> None:
    try:
        coro.close()
    except Exception:
        logger.debug("Failed to close coroutine", exc_info=True)


def _prune_done_tasks() -> None:
    if not _bg_tasks:
        _bg_tasks_by_key.clear()
        set_bg_task_pending(0)
        return
    done = [t for t in _bg_tasks if t.done()]
    for t in done:
        _bg_tasks.discard(t)
    stale_keys = [k for k, t in _bg_tasks_by_key.items() if t.done()]
    for k in stale_keys:
        _bg_tasks_by_key.pop(k, None)
    set_bg_task_pending(len(_bg_tasks))


def fire_and_forget(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str = "bg-task",
    timeout_sec: float | None = None,
    coalesce_key: Optional[str] = None,
) -> asyncio.Task[Any]:
    _prune_done_tasks()

    replacing = False
    if coalesce_key:
        prev = _bg_tasks_by_key.get(coalesce_key)
        if prev is not None and not prev.done():
            replacing = True
            prev.cancel()
            logger.warning("Background task coalesced: key=%s name=%s", coalesce_key, name)

    if len(_bg_tasks) >= _MAX_BG_TASKS and not replacing:
        logger.error(
            "Background task limit exceeded: pending=%s limit=%s name=%s key=%s",
            len(_bg_tasks),
            _MAX_BG_TASKS,
            name,
            coalesce_key or "-",
        )
        try:
            _close_coro_safely(coro)
        except Exception:
            pass
        async def _dropped() -> None:
            return None
        return asyncio.create_task(_dropped(), name=f"{name}:dropped")

    ttl = _DEFAULT_TASK_TIMEOUT_SEC if timeout_sec is None else max(0.1, float(timeout_sec))

    async def _runner() -> Any:
        try:
            return await asyncio.wait_for(coro, timeout=ttl)
        except asyncio.TimeoutError:
            logger.warning("Background task timeout: name=%s timeout_sec=%.2f", name, ttl)
            inc_bg_task_timeout()
            _close_coro_safely(coro)
            return None
        except asyncio.CancelledError:
            _close_coro_safely(coro)
            raise
        except Exception:
            _close_coro_safely(coro)
            raise

    inc_bg_task_started()
    task = asyncio.create_task(_runner(), name=name)
    _bg_tasks.add(task)
    if coalesce_key:
        _bg_tasks_by_key[coalesce_key] = task
    set_bg_task_pending(len(_bg_tasks))

    def _done(t: asyncio.Task[Any]) -> None:
        _bg_tasks.discard(t)
        if coalesce_key and _bg_tasks_by_key.get(coalesce_key) is t:
            _bg_tasks_by_key.pop(coalesce_key, None)
        set_bg_task_pending(len(_bg_tasks))
        try:
            t.result()
        except asyncio.CancelledError:
            _close_coro_safely(coro)
            return
        except Exception:
            inc_bg_task_failed()
            logger.exception("Background task failed: %s", name)

    task.add_done_callback(_done)
    return task


async def drain_background_tasks(timeout_sec: float = _DEFAULT_DRAIN_TIMEOUT_SEC) -> int:
    _prune_done_tasks()
    if not _bg_tasks:
        return 0

    pending = list(_bg_tasks)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning("Background drain timed out; pending=%s timeout_sec=%.2f", len(pending), timeout_sec)
        for t in pending:
            if not t.done():
                t.cancel()
    finally:
        _prune_done_tasks()
    return len(_bg_tasks)


def get_background_task_stats() -> dict[str, int]:
    _prune_done_tasks()
    return {
        "pending": len(_bg_tasks),
        "coalesced_keys": len(_bg_tasks_by_key),
        "limit": _MAX_BG_TASKS,
    }
