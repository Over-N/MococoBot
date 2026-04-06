import os
import time
from typing import Optional, Tuple

import httpx

from utils.request_context import add_http_ms

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    _PROM_IMPORT_OK = True
except Exception:
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    Counter = None
    Gauge = None
    Histogram = None
    generate_latest = None
    _PROM_IMPORT_OK = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


_METRICS_ENABLED = _PROM_IMPORT_OK and _env_bool("METRICS_ENABLED", True)


if _METRICS_ENABLED:
    HTTP_REQUEST_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request end-to-end latency",
        ["method", "path", "status"],
    )
    HTTP_REQUEST_TOTAL = Counter(
        "http_request_total",
        "HTTP request count",
        ["method", "path", "status"],
    )

    DB_QUERY_DURATION = Histogram(
        "db_query_duration_seconds",
        "DB query latency",
        ["op", "status"],
    )
    DB_QUERY_TOTAL = Counter(
        "db_query_total",
        "DB query count",
        ["op", "status"],
    )
    DB_ACQUIRE_DURATION = Histogram(
        "db_acquire_duration_seconds",
        "DB pool acquire latency",
        ["status"],
    )

    EXTERNAL_HTTP_DURATION = Histogram(
        "external_http_duration_seconds",
        "External HTTP latency",
        ["service", "host", "method", "status"],
    )
    EXTERNAL_HTTP_ERRORS = Counter(
        "external_http_error_total",
        "External HTTP error count",
        ["service", "host", "method", "error"],
    )

    SCHEDULER_JOB_DURATION = Histogram(
        "scheduler_job_duration_seconds",
        "Scheduler job duration",
        ["job", "status"],
    )
    SCHEDULER_JOB_TOTAL = Counter(
        "scheduler_job_total",
        "Scheduler job count",
        ["job", "status"],
    )
    SCHEDULER_JOB_OVERLAP_TOTAL = Counter(
        "scheduler_job_overlap_total",
        "Scheduler overlap detections",
        ["job"],
    )

    BG_TASK_PENDING = Gauge(
        "bg_task_pending",
        "Current background task count",
    )
    BG_TASK_STARTED_TOTAL = Counter(
        "bg_task_started_total",
        "Background task started count",
    )
    BG_TASK_FAILED_TOTAL = Counter(
        "bg_task_failed_total",
        "Background task failed count",
    )
    BG_TASK_TIMEOUT_TOTAL = Counter(
        "bg_task_timeout_total",
        "Background task timeout count",
    )

    PROCESS_RESIDENT_MEMORY_BYTES = Gauge(
        "process_resident_memory_bytes",
        "Process RSS in bytes",
    )
    DB_POOL_SIZE = Gauge("db_pool_size", "DB pool total size")
    DB_POOL_FREE = Gauge("db_pool_free", "DB pool free size")
    DB_POOL_USED = Gauge("db_pool_used", "DB pool used size")

else:
    HTTP_REQUEST_DURATION = None
    HTTP_REQUEST_TOTAL = None
    DB_QUERY_DURATION = None
    DB_QUERY_TOTAL = None
    DB_ACQUIRE_DURATION = None
    EXTERNAL_HTTP_DURATION = None
    EXTERNAL_HTTP_ERRORS = None
    SCHEDULER_JOB_DURATION = None
    SCHEDULER_JOB_TOTAL = None
    SCHEDULER_JOB_OVERLAP_TOTAL = None
    BG_TASK_PENDING = None
    BG_TASK_STARTED_TOTAL = None
    BG_TASK_FAILED_TOTAL = None
    BG_TASK_TIMEOUT_TOTAL = None
    PROCESS_RESIDENT_MEMORY_BYTES = None
    DB_POOL_SIZE = None
    DB_POOL_FREE = None
    DB_POOL_USED = None


def metrics_enabled() -> bool:
    return _METRICS_ENABLED


def observe_http_request(method: str, path: str, status: int, elapsed_ms: float) -> None:
    if not _METRICS_ENABLED:
        return
    m = (method or "GET").upper()
    p = path or "/"
    s = str(int(status or 0))
    sec = max(0.0, float(elapsed_ms or 0.0) / 1000.0)
    HTTP_REQUEST_DURATION.labels(m, p, s).observe(sec)
    HTTP_REQUEST_TOTAL.labels(m, p, s).inc()


def observe_db_query(op: str, status: str, elapsed_ms: float) -> None:
    if not _METRICS_ENABLED:
        return
    o = (op or "OTHER").upper()
    st = (status or "ok").lower()
    sec = max(0.0, float(elapsed_ms or 0.0) / 1000.0)
    DB_QUERY_DURATION.labels(o, st).observe(sec)
    DB_QUERY_TOTAL.labels(o, st).inc()


def observe_db_acquire(status: str, elapsed_ms: float) -> None:
    if not _METRICS_ENABLED:
        return
    st = (status or "ok").lower()
    sec = max(0.0, float(elapsed_ms or 0.0) / 1000.0)
    DB_ACQUIRE_DURATION.labels(st).observe(sec)


def observe_external_http(
    service: str,
    host: str,
    method: str,
    status: int,
    elapsed_ms: float,
    *,
    error: Optional[str] = None,
) -> None:
    if not _METRICS_ENABLED:
        return
    svc = service or "external"
    h = host or "-"
    m = (method or "GET").upper()
    s = str(int(status or 0))
    sec = max(0.0, float(elapsed_ms or 0.0) / 1000.0)
    EXTERNAL_HTTP_DURATION.labels(svc, h, m, s).observe(sec)
    if error:
        EXTERNAL_HTTP_ERRORS.labels(svc, h, m, error).inc()


def observe_scheduler_job(job: str, status: str, elapsed_sec: float) -> None:
    if not _METRICS_ENABLED:
        return
    j = job or "unknown"
    st = (status or "ok").lower()
    sec = max(0.0, float(elapsed_sec or 0.0))
    SCHEDULER_JOB_DURATION.labels(j, st).observe(sec)
    SCHEDULER_JOB_TOTAL.labels(j, st).inc()


def inc_scheduler_overlap(job: str) -> None:
    if not _METRICS_ENABLED:
        return
    SCHEDULER_JOB_OVERLAP_TOTAL.labels(job or "unknown").inc()


def set_bg_task_pending(value: int) -> None:
    if not _METRICS_ENABLED:
        return
    BG_TASK_PENDING.set(max(0, int(value or 0)))


def inc_bg_task_started() -> None:
    if not _METRICS_ENABLED:
        return
    BG_TASK_STARTED_TOTAL.inc()


def inc_bg_task_failed() -> None:
    if not _METRICS_ENABLED:
        return
    BG_TASK_FAILED_TOTAL.inc()


def inc_bg_task_timeout() -> None:
    if not _METRICS_ENABLED:
        return
    BG_TASK_TIMEOUT_TOTAL.inc()


def set_process_rss(value: int) -> None:
    if not _METRICS_ENABLED:
        return
    PROCESS_RESIDENT_MEMORY_BYTES.set(max(0, int(value or 0)))


def set_db_pool(size: int, free: int, used: int) -> None:
    if not _METRICS_ENABLED:
        return
    DB_POOL_SIZE.set(max(0, int(size or 0)))
    DB_POOL_FREE.set(max(0, int(free or 0)))
    DB_POOL_USED.set(max(0, int(used or 0)))


def render_metrics_payload() -> Tuple[str, str]:
    if _METRICS_ENABLED and generate_latest is not None:
        return generate_latest().decode("utf-8"), CONTENT_TYPE_LATEST
    return "# metrics disabled\n", CONTENT_TYPE_LATEST


def instrument_async_client(client: httpx.AsyncClient, *, service: str) -> httpx.AsyncClient:
    if getattr(client, "_metrics_wrapped", False):
        return client

    original_request = client.request

    async def _wrapped_request(method, url, *args, **kwargs):
        start = time.perf_counter()
        host = "-"
        try:
            host = str(httpx.URL(url).host or "-")
        except Exception:
            host = "-"
        method_s = (method or "GET").upper()
        try:
            resp = await original_request(method, url, *args, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            add_http_ms(elapsed_ms)
            observe_external_http(service, host, method_s, int(resp.status_code), elapsed_ms)
            return resp
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            add_http_ms(elapsed_ms)
            observe_external_http(
                service,
                host,
                method_s,
                0,
                elapsed_ms,
                error=exc.__class__.__name__,
            )
            raise

    client.request = _wrapped_request
    setattr(client, "_metrics_wrapped", True)
    return client

