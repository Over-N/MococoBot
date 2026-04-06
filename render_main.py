from contextlib import asynccontextmanager
import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from render_routers.character_image import router as character_render_router
from render_routers.party_image import router as party_render_router
from render_routers.lounge import router as lounge_router
from utils.http_client import init_http_client, close_http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_http_client()
        print("[Startup] Render warmup complete")
    except Exception as e:
        print(f"[Startup] Warmup skipped: {e}")

    yield

    await close_http_client()
    print("[Shutdown] Render service stopped")


app = FastAPI(title="Mococo Render Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(party_render_router, prefix="/render")
app.include_router(character_render_router, prefix="/render")
app.include_router(lounge_router, prefix="/render")

if __name__ == "__main__":
    cpu_count = os.cpu_count() or 2
    default_workers = max(1, min(4, cpu_count // 2))
    workers = int(os.getenv("RENDER_WORKERS", str(default_workers)))
    limit_concurrency = int(os.getenv("RENDER_LIMIT_CONCURRENCY", "64"))

    uvicorn.run(
        "render_main:app",
        host="0.0.0.0",
        port=9001,
        workers=workers,
        limit_concurrency=limit_concurrency,
        access_log=False,
    )
