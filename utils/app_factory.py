import hashlib
import os
import time
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.security import APIKeyHeader

from routers import botsync, character, discord, enhance, fixedraid, friends, party, quiz, raid, siblings, subscription, tts, verification as be_verification
from utils.app_middlewares import RequestMetricsMiddleware, SecurityHeadersMiddleware, SecurityMiddleware
from utils.app_settings import settings
from utils.health_utils import build_health_payload
from utils.http_client import close_http_client, init_http_client
from utils.metrics import instrument_async_client, metrics_enabled, render_metrics_payload
from utils.render_proxy import proxy_png
from utils.request_context import add_auth_ms
from utils.task_utils import drain_background_tasks

_LIMITS = httpx.Limits(max_keepalive_connections=50, max_connections=100)
_TIMEOUT = httpx.Timeout(connect=1.0, read=180.0, write=5.0, pool=5.0)
_api_keys_cache = None
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at_monotonic = time.monotonic()
    app.state.started_at_epoch = time.time()
    app.state.http = instrument_async_client(
        httpx.AsyncClient(base_url=settings.render_base, limits=_LIMITS, timeout=_TIMEOUT),
        service="render_proxy",
    )
    await init_http_client()
    print("[Startup] 모코코 봇 API 시작")
    try:
        yield
    finally:
        try:
            remaining = await drain_background_tasks()
            if remaining:
                print(f"[Shutdown] background tasks remaining={remaining}")
        except Exception:
            print("[Shutdown] background task drain failed")
        await app.state.http.aclose()
        await close_http_client()
        print("[Shutdown] 모코코 봇 API 종료")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def get_valid_api_keys() -> dict[str, set[str]]:
    global _api_keys_cache
    if _api_keys_cache is None:
        dev_keys = {hash_api_key(key.strip()) for key in os.getenv("API_KEYS_DEVELOPMENT", "").split(",") if key.strip()}
        prod_keys = {hash_api_key(key.strip()) for key in os.getenv("API_KEYS_PRODUCTION", "").split(",") if key.strip()}
        _api_keys_cache = {
            "development": dev_keys,
            "production": prod_keys,
        }
    return _api_keys_cache


def verify_api_key(api_key: str = Depends(api_key_header)):
    started = time.perf_counter()
    try:
        if not api_key:
            raise HTTPException(status_code=401, detail="API Key required")
        valid_keys = get_valid_api_keys().get(settings.environment, set())
        if hash_api_key(api_key) not in valid_keys:
            raise HTTPException(status_code=401, detail="Invalid API Key")
        return api_key
    finally:
        add_auth_ms((time.perf_counter() - started) * 1000.0)


def _build_openapi(app: FastAPI) -> dict:
    spec = deepcopy(get_openapi(title=app.title, version=app.version, routes=app.routes))
    used_tags = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        route_tags = getattr(route, "tags", None) or []
        for tag in route_tags:
            used_tags.add(tag)
    if "tags" in spec:
        spec["tags"] = [tag for tag in spec["tags"] if tag.get("name") in used_tags]
    return spec


def register_middlewares(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestMetricsMiddleware)


def register_routers(app: FastAPI) -> None:
    protected = [Depends(verify_api_key)]
    app.include_router(raid.router, prefix="/raid", tags=["레이드 관리"], dependencies=protected)
    app.include_router(discord.router, prefix="/discord", tags=["디스코드 서버 관리"], dependencies=protected)
    app.include_router(party.router, prefix="/party", tags=["레이드 파티 관리"], dependencies=protected)
    app.include_router(tts.router, prefix="/tts", tags=["TTS 채널 관리"], dependencies=protected)
    app.include_router(character.router, prefix="/character", tags=["사용자 캐릭터 관리"], dependencies=protected)
    app.include_router(siblings.router, prefix="/siblings", tags=["사용자 원정대 관리"], dependencies=protected)
    app.include_router(be_verification.router, prefix="/verify", tags=["로스트아크 인증 관리"], dependencies=protected)
    app.include_router(friends.router, prefix="/friends", tags=["친구 찾기"], dependencies=protected)
    app.include_router(botsync.router, prefix="/botsync", tags=["봇 속해있는 서버 관리"], dependencies=protected)
    app.include_router(subscription.router, prefix="/subscription", tags=["구독"], dependencies=protected)
    app.include_router(quiz.router, prefix="/quiz", tags=["퀴즈"], dependencies=protected)
    app.include_router(enhance.router, prefix="/enhance", tags=["강화"], dependencies=protected)
    app.include_router(fixedraid.router, prefix="/fixedraid", tags=["고정공격대"], dependencies=protected)


def register_builtin_routes(app: FastAPI) -> None:
    @app.get("/invite")
    async def bot_invite():
        return Response(status_code=307, headers={"Location": settings.invite_redirect_url})

    @app.get("/")
    async def bot_install():
        return Response(status_code=307, headers={"Location": settings.install_redirect_url})

    @app.get("/render/profile")
    async def render_profile(nickname: str = Query(..., min_length=1), user_id: str = Query(...), _: str = Depends(verify_api_key)):
        return await proxy_png(app, "/render/profile", params={"nickname": nickname, "user_id": user_id})

    @app.get("/render/mini-profile")
    async def render_mini_profile(nickname: str = Query(..., min_length=1), _: str = Depends(verify_api_key)):
        return await proxy_png(app, "/render/mini-profile", params={"nickname": nickname})

    @app.get("/render/party/{party_id}")
    async def render_party(party_id: int, _: str = Depends(verify_api_key)):
        return await proxy_png(app, f"/render/party/{party_id}")

    @app.get("/render/lounge/{party_id}")
    async def render_lounge(party_id: int, _: str = Depends(verify_api_key)):
        return await proxy_png(app, f"/render/lounge/{party_id}")

    @app.get("/openapi-be.json", include_in_schema=False)
    def openapi_be():
        return JSONResponse(_build_openapi(app))

    @app.get("/be/docs", include_in_schema=False)
    def docs_be():
        return get_swagger_ui_html(openapi_url="/openapi-be.json", title="Backend Docs")

    @app.get("/health")
    async def health(_: str = Depends(verify_api_key)):
        try:
            return JSONResponse(await build_health_payload(app))
        except Exception as exc:
            return JSONResponse({"status": "error", "detail": str(exc)[:200]}, status_code=500)

    async def internal_metrics(_: str = Depends(verify_api_key)):
        payload, content_type = render_metrics_payload()
        return Response(content=payload, media_type=content_type)

    if settings.metrics_http_endpoint_enabled and metrics_enabled():
        app.add_api_route(
            "/internal/metrics",
            internal_metrics,
            methods=["GET"],
            include_in_schema=False,
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_title,
        description=settings.app_description,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None,
    )
    register_middlewares(app)
    register_routers(app)
    register_builtin_routes(app)
    return app
