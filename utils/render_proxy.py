import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse


async def pipe_png(resp: httpx.Response) -> StreamingResponse:
    media_type = resp.headers.get("content-type", "image/png")

    async def _aiter():
        async for chunk in resp.aiter_bytes():
            yield chunk

    return StreamingResponse(_aiter(), media_type=media_type)


def translate_render_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 404:
            return HTTPException(status_code=404, detail="Not found from render service")
        return HTTPException(status_code=502, detail="Render upstream error")
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        return HTTPException(status_code=504, detail="Render service timeout")
    return HTTPException(status_code=502, detail="Render API error")


async def proxy_png(app, upstream_path: str, *, params: dict | None = None):
    try:
        response = await app.state.http.get(upstream_path, params=params)
        response.raise_for_status()
        return await pipe_png(response)
    except Exception as exc:
        raise translate_render_error(exc)
