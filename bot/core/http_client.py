import asyncio
from typing import Optional, Any, Dict

import httpx
from core.config import API_BASE_URL, API_KEY


RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 522, 524}
RETRYABLE_ERRORS = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.TransportError,
)

class HTTPClient:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

        # 공용 설정 (필요 시 조정)
        self._timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
        self._limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=30.0,
        )
        self._base_headers = {
            "X-API-Key": API_KEY,
            "Connection": "keep-alive",
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        base_url=API_BASE_URL,
                        headers=self._base_headers,
                        limits=self._limits,
                        timeout=self._timeout,
                        http2=False,
                    )
        return self._client

    async def reset(self):
        """커넥션 풀 리셋 (깨진 소켓 재사용 방지)"""
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.aclose()
                finally:
                    self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Dict[str, Any] | None = None,
        params: Dict[str, Any] | None = None,
        headers: Dict[str, str] | None = None,
        api_key: str | None = None,
        max_attempts: int = 1,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        """
        재시도 + 커넥션풀 리셋 내장 요청
        - 일시적 네트워크/서버 오류(끊김/타임아웃/5xx/429 등)에서 자동으로 복구 시도
        - api_key: 요청별 API 키 지정 (지정하지 않으면 기본값 사용)
        """
        delay = 0.2
        attempt = 1
        
        # 헤더 병합 (요청별 API 키 지원)
        request_headers = dict(self._base_headers)
        if api_key:
            request_headers["X-API-Key"] = api_key
        if headers:
            request_headers.update(headers)

        while True:
            client = await self._ensure_client()
            try:
                resp = await client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=request_headers,  # 수정된 헤더 사용
                    timeout=timeout or self._timeout,
                )
                if resp.status_code in RETRYABLE_STATUS and attempt < max_attempts:
                    await asyncio.sleep(delay)
                    delay *= 2
                    attempt += 1
                    continue
                return resp

            except RETRYABLE_ERRORS:
                if attempt >= max_attempts:
                    raise
                # 소켓풀 리셋 후 재시도
                await self.reset()
                await asyncio.sleep(delay)
                delay *= 2
                attempt += 1

    # 편의 메서드 ----------------------------------------------------------
    async def get(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)
    
    async def patch(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    async def aclose(self) -> None:
        await self.reset()


http_client = HTTPClient()
