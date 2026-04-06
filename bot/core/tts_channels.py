"""TTS 채널 관리 모듈"""
from core.http_client import http_client
from typing import Optional, Dict

class TTSChannelManager:
    def __init__(self):
        self.tts_channels = {}  # {guild_id: channel_id}
        self.override_channels: Dict[int, int] = {} # {guild_id: channel_id} (임시 설정: /join)
        self.override_meta: Dict[int, Dict] = {}    # {guild_id: {"by": user_id}}

    async def load_all_channels(self):
        """봇 시작시 모든 TTS 채널 로드"""
        try:
            response = await http_client.get("/tts/list")
            if response.status_code == 200:
                data = response.json().get("data", [])
                for item in data:
                    guild_id = int(item["guild_id"])
                    channel_id = int(item["tts_channel_id"])
                    self.tts_channels[guild_id] = channel_id
                print(f"TTS 채널 로드 완료: {len(self.tts_channels)}개 서버")
            else:
                print(f"TTS 채널 로드 실패: {response.status_code}")
        except Exception as e:
            print(f"TTS 채널 로드 오류: {e}")
    
    async def set_channel(self, guild_id: int, channel_id: int):
        """TTS 채널 설정(영구)"""
        try:
            if guild_id in self.tts_channels:
                response = await http_client.patch(f"/tts/{guild_id}", json={"tts_channel_id": channel_id})
            else:
                response = await http_client.post(f"/tts/{guild_id}", json={"tts_channel_id": channel_id})
            if response.status_code in [200, 201]:
                self.tts_channels[guild_id] = channel_id
                return True
            else:
                print(f"TTS 채널 설정 실패: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"TTS 채널 설정 오류: {e}")
            return False

    async def remove_channel(self, guild_id: int):
        """TTS 채널 삭제(영구 해제)"""
        try:
            response = await http_client.delete(f"/tts/{guild_id}")
            if response.status_code == 200:
                self.tts_channels.pop(guild_id, None)
                return True
            else:
                return False
        except Exception as e:
            print(f"TTS 채널 삭제 오류: {e}")
            return False
    
    # === 신규: 임시 채널 우선 기능 ===
    def set_override(self, guild_id: int, channel_id: int, by_user_id: Optional[int] = None) -> None:
        """임시 TTS 채널 설정 (/join). 영구 설정보다 우선."""
        self.override_channels[guild_id] = channel_id
        if by_user_id:
            self.override_meta[guild_id] = {"by": by_user_id}

    def clear_override(self, guild_id: int) -> None:
        """임시 TTS 채널 해제 (/leave 또는 봇 퇴장시 자동)."""
        self.override_channels.pop(guild_id, None)
        self.override_meta.pop(guild_id, None)

    def clear_all_overrides(self) -> None:
        """모든 임시 TTS 채널 해제 (봇 종료/초기화용)."""
        self.override_channels.clear()
        self.override_meta.clear()

    def get_effective_channel(self, guild_id: int) -> Optional[int]:
        """실제로 적용되는 TTS 채널(임시 > 영구)."""
        return self.override_channels.get(guild_id) or self.tts_channels.get(guild_id)

    def get_channel(self, guild_id: int):
        """영구 TTS 채널 조회(기존과 호환 유지용)"""
        return self.tts_channels.get(guild_id)

    def is_tts_channel(self, guild_id: int, channel_id: int) -> bool:
        """해당 채널이 현재 '읽기 대상'인지 확인 (임시 > 영구)."""
        override = self.override_channels.get(guild_id)
        if override is not None:
            return override == channel_id
        return self.tts_channels.get(guild_id) == channel_id

# 전역 인스턴스
tts_channel_manager = TTSChannelManager()