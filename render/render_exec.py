import asyncio, os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable, Any

def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except Exception:
        return default

_CPU = max(1, (os.cpu_count() or 2))
_MAX_CONCURRENCY = min(32, _env_int("RENDER_MAX_CONCURRENCY", _CPU * 2))

_render_executor = ThreadPoolExecutor(
    max_workers=_MAX_CONCURRENCY,
    thread_name_prefix="render",
)
_render_sem = asyncio.Semaphore(_MAX_CONCURRENCY)

async def run_render(func: Callable[..., Any], *args, timeout: float | None = 30.0, **kwargs) -> Any:
    """CPU 바운드 렌더링을 스레드풀에서 실행 + 동시성 제한 + 옵션 타임아웃."""
    loop = asyncio.get_running_loop()
    async with _acquire_render_slot():
        fut = loop.run_in_executor(_render_executor, lambda: func(*args, **kwargs))
        if timeout is None:
            return await fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("렌더링 타임아웃")

@asynccontextmanager
async def _acquire_render_slot():
    await _render_sem.acquire()
    try:
        yield
    finally:
        _render_sem.release()
