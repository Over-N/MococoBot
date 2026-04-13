import asyncio
import gc
import os
import sys
import time
import httpx

try:
    import psutil
except Exception:
    psutil = None


async def build_health_payload(app) -> dict:
    now_epoch = time.time()
    uptime_seconds = max(0.0, now_epoch - getattr(app.state, "started_at_epoch", now_epoch))

    cpu = {}
    memory = {}
    disk = {}

    if hasattr(os, "getloadavg"):
        try:
            loadavg = os.getloadavg()
        except Exception:
            loadavg = None
        if loadavg:
            cpu["loadavg_1_5_15"] = loadavg

    if psutil is not None:
        try:
            cpu["percent"] = psutil.cpu_percent(interval=None)
            cpu["percpu_percent"] = psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass
        try:
            vm = psutil.virtual_memory()
            memory = {
                "total": vm.total,
                "available": vm.available,
                "used": vm.used,
                "percent": vm.percent,
            }
        except Exception:
            pass
        try:
            du = psutil.disk_usage("/")
            disk = {
                "total": du.total,
                "used": du.used,
                "free": du.free,
                "percent": du.percent,
            }
        except Exception:
            pass

    render_ping_ms = None
    render_status = None
    render_error = None
    try:
        started = time.perf_counter()
        response = await app.state.http.get("/health", timeout=httpx.Timeout(connect=0.5, read=1.5, write=0.5, pool=0.5))
        render_status = response.status_code
        render_ping_ms = (time.perf_counter() - started) * 1000.0
    except Exception:
        try:
            started = time.perf_counter()
            response = await app.state.http.get("/", timeout=httpx.Timeout(connect=0.5, read=1.5, write=0.5, pool=0.5))
            render_status = response.status_code
            render_ping_ms = (time.perf_counter() - started) * 1000.0
        except Exception as exc:
            render_error = str(exc)[:200]

    process = {"pid": os.getpid()}
    if psutil is not None:
        try:
            proc = psutil.Process(os.getpid())
            mem_info = proc.memory_info()
            process = {
                "pid": proc.pid,
                "ppid": proc.ppid(),
                "rss": mem_info.rss,
                "vms": mem_info.vms,
                "num_threads": proc.num_threads(),
                "num_fds": getattr(proc, "num_fds", lambda: None)(),
                "num_handles": getattr(proc, "num_handles", lambda: None)(),
                "open_files": len(proc.open_files() or []),
                "cpu_times": tuple(proc.cpu_times()),
            }
        except Exception:
            process = {"pid": os.getpid()}
    process["started_at_epoch"] = getattr(app.state, "started_at_epoch", None)

    app_info = {
        "title": getattr(app, "title", None),
        "version": getattr(app, "version", None),
        "routes": len(getattr(app.router, "routes", []) or []),
        "openapi_url": getattr(app, "openapi_url", None),
        "docs_url": getattr(app, "docs_url", None),
        "redoc_url": getattr(app, "redoc_url", None),
    }

    runtime = {
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 0,
        "gc_enabled": gc.isenabled(),
        "gc_gen0_allocs": (gc.get_stats()[0]["collections"] if hasattr(gc, "get_stats") else None) if hasattr(gc, "get_stats") else None,
        "asyncio_tasks": len(asyncio.all_tasks()) if hasattr(asyncio, "all_tasks") else None,
    }

    return {
        "status": "ok",
        "time_epoch": now_epoch,
        "uptime_seconds": uptime_seconds,
        "cpu": cpu or None,
        "memory": memory or None,
        "disk": disk or None,
        "render_server": {
            "status": render_status,
            "ping_ms": render_ping_ms,
            "error": render_error,
        },
        "process": process,
        "app": app_info,
        "runtime": runtime,
    }
