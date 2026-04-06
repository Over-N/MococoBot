from typing import Dict

try:
    from core.http_client import http_client
except Exception:
    http_client = None


class TTSEngineManager:
    def __init__(self) -> None:
        self.user_engines: Dict[int, str] = {}

    async def load_all(self) -> None:
        """전체 사용자 엔진 설정을 서버에서 불러와 캐시에 저장합니다."""
        if http_client is None:
            return
        try:
            response = await http_client.get("/tts/engine/all")
            if response.status_code == 200:
                payload = response.json() or {}
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                if isinstance(data, dict):
                    for k, v in data.items():
                        try:
                            uid = int(k)
                            if isinstance(v, str):
                                self.user_engines[uid] = v
                        except Exception:
                            continue
        except Exception as e:
            print(f"[TTSEngineManager] Failed to load engines: {e}")

    def get_engine(self, user_id: int) -> str:
        """
        사용자별 엔진 ID 조회.
        설정되지 않은 경우 기본값 'engine1' 을 반환합니다.
        """
        return self.user_engines.get(user_id, "engine1")

    def set_engine(self, user_id: int, engine_id: str) -> None:
        """
        내부 캐시에 사용자 엔진을 설정합니다. 서버에 반영은 별도 API 호출로 수행해야 합니다.
        """
        self.user_engines[user_id] = engine_id


# 전역 인스턴스
tts_engine_manager = TTSEngineManager()