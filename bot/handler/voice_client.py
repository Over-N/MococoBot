from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import discord
import httpx


logger = logging.getLogger("tts.voice")

VOICE_CONNECT_TIMEOUT = 20.0
VOICE_MOVE_TIMEOUT = 15.0
VOICE_DISCONNECT_TIMEOUT = 10.0

AfterCallback = Callable[[Optional[Exception]], None]


class BaseVoiceClient:
    mode: str = "pycord"
    supports_dave: bool = False

    def is_alive(self, session: Optional[Any]) -> bool:
        raise NotImplementedError

    def get_session(self, guild: Optional[discord.Guild], guild_id: int) -> Optional[Any]:
        raise NotImplementedError

    def connected_guild_ids(self) -> list[int]:
        raise NotImplementedError

    def clear_cached_session(self, guild_id: int) -> None:
        raise NotImplementedError

    def clear_connect_lock(self, guild_id: int) -> None:
        raise NotImplementedError

    def is_playing(self, session: Optional[Any]) -> bool:
        raise NotImplementedError

    def play(self, session: Any, source: discord.AudioSource, after: AfterCallback) -> None:
        raise NotImplementedError

    def stop(self, session: Optional[Any]) -> None:
        raise NotImplementedError

    def channel_id_of(self, session: Optional[Any]) -> Optional[int]:
        raise NotImplementedError

    def channel_mention_of(self, session: Optional[Any]) -> str:
        cid = self.channel_id_of(session)
        return f"<#{cid}>" if cid else "알 수 없는 채널"

    async def join(self, channel: discord.VoiceChannel, guild_id: int) -> Tuple[Optional[Any], Optional[discord.Embed]]:
        raise NotImplementedError

    async def disconnect(self, guild_id: int, guild: Optional[discord.Guild] = None, reason: str = "disconnect") -> None:
        raise NotImplementedError


class PycordVoiceClient(BaseVoiceClient):
    mode = "pycord"
    supports_dave = False

    def __init__(self) -> None:
        self._voice_clients: Dict[int, discord.VoiceClient] = {}
        self._voice_connect_locks: Dict[int, asyncio.Lock] = {}

    def _get_voice_connect_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._voice_connect_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._voice_connect_locks[guild_id] = lock
        return lock

    def is_alive(self, session: Optional[Any]) -> bool:
        vc = session
        if vc is None:
            return False
        try:
            if not vc.is_connected():
                return False
            if getattr(vc, "channel", None) is None:
                return False
            if getattr(vc, "guild", None) is None:
                return False
            return True
        except Exception:
            return False

    def get_session(self, guild: Optional[discord.Guild], guild_id: int) -> Optional[Any]:
        vc = self._voice_clients.get(guild_id)
        if self.is_alive(vc):
            return vc
        self._voice_clients.pop(guild_id, None)
        if guild is not None:
            try:
                gvc = getattr(guild, "voice_client", None)
                if self.is_alive(gvc):
                    self._voice_clients[guild_id] = gvc
                    return gvc
            except Exception:
                pass
        return None

    def connected_guild_ids(self) -> list[int]:
        return list(self._voice_clients.keys())

    def clear_cached_session(self, guild_id: int) -> None:
        self._voice_clients.pop(guild_id, None)

    def clear_connect_lock(self, guild_id: int) -> None:
        self._voice_connect_locks.pop(guild_id, None)

    def is_playing(self, session: Optional[Any]) -> bool:
        vc = session
        if vc is None:
            return False
        try:
            return vc.is_playing()
        except Exception:
            return False

    def play(self, session: Any, source: discord.AudioSource, after: AfterCallback) -> None:
        vc: discord.VoiceClient = session
        vc.play(source, after=after)

    def stop(self, session: Optional[Any]) -> None:
        vc = session
        if vc is None:
            return
        try:
            vc.stop()
        except Exception:
            pass

    def channel_id_of(self, session: Optional[Any]) -> Optional[int]:
        vc = session
        ch = getattr(vc, "channel", None) if vc is not None else None
        return getattr(ch, "id", None)

    async def _safe_force_disconnect(self, vc: Optional[discord.VoiceClient], guild_id: int, reason: str) -> None:
        if vc is None:
            return
        try:
            if vc.is_playing():
                vc.stop()
        except Exception:
            pass
        try:
            await asyncio.wait_for(vc.disconnect(force=True), timeout=VOICE_DISCONNECT_TIMEOUT)
        except Exception as e:
            logger.debug("[TTS] force disconnect skipped/failed (guild_id=%s, reason=%s): %s", guild_id, reason, e)
        try:
            vc.cleanup()
        except Exception:
            pass

    async def _recover_connected_voice_client(
        self,
        guild: Optional[discord.Guild],
        guild_id: int,
        target_channel: Optional[discord.VoiceChannel] = None,
        *,
        try_move: bool = False,
    ) -> Optional[discord.VoiceClient]:
        if guild is None:
            return None

        raw_vc = getattr(guild, "voice_client", None)
        if raw_vc is None:
            return None

        if not self.is_alive(raw_vc):
            await self._safe_force_disconnect(raw_vc, guild_id, reason="recover_not_alive")
            self._voice_clients.pop(guild_id, None)
            return None

        if target_channel is not None:
            try:
                current_channel = getattr(raw_vc, "channel", None)
                if current_channel is not None and current_channel.id != target_channel.id and try_move:
                    await asyncio.wait_for(raw_vc.move_to(target_channel), timeout=VOICE_MOVE_TIMEOUT)
            except Exception as e:
                logger.warning("[TTS] Voice recover move failed (guild_id=%s): %s", guild_id, e)
                await self._safe_force_disconnect(raw_vc, guild_id, reason="recover_move_failed")
                self._voice_clients.pop(guild_id, None)
                return None

        self._voice_clients[guild_id] = raw_vc
        return raw_vc

    async def join(self, channel: discord.VoiceChannel, guild_id: int) -> Tuple[Optional[Any], Optional[discord.Embed]]:
        lock = self._get_voice_connect_lock(guild_id)
        async with lock:
            guild = channel.guild
            current_vc = self.get_session(guild, guild_id)
            if current_vc is not None:
                try:
                    if getattr(current_vc, "channel", None) is not None and current_vc.channel.id == channel.id:
                        self._voice_clients[guild_id] = current_vc
                        return current_vc, None
                except Exception as e:
                    logger.warning("[TTS] VoiceClient 상태 확인 실패 (guild_id=%s): %s", guild_id, e)
                    self._voice_clients.pop(guild_id, None)
                    current_vc = None

            if current_vc is not None:
                try:
                    await asyncio.wait_for(current_vc.move_to(channel), timeout=VOICE_MOVE_TIMEOUT)
                    self._voice_clients[guild_id] = current_vc
                    return current_vc, None
                except asyncio.TimeoutError:
                    logger.warning("[TTS] Voice move timed out (guild_id=%s)", guild_id)
                except Exception as e:
                    logger.warning("[TTS] Voice move failed (guild_id=%s): %s", guild_id, e)
                try:
                    await asyncio.wait_for(current_vc.disconnect(force=True), timeout=VOICE_DISCONNECT_TIMEOUT)
                except Exception as e:
                    logger.warning("[TTS] Voice disconnect failed after move error (guild_id=%s): %s", guild_id, e)
                try:
                    current_vc.cleanup()
                except Exception:
                    pass
                self._voice_clients.pop(guild_id, None)
                await asyncio.sleep(0.5)

            me = getattr(guild, "me", None)
            if me is not None:
                perms = channel.permissions_for(me)
                if not (perms.connect and perms.speak):
                    return None, None

            recovered_vc = await self._recover_connected_voice_client(guild, guild_id, channel, try_move=True)
            if recovered_vc is not None:
                return recovered_vc, None

            try:
                new_vc = await asyncio.wait_for(channel.connect(), timeout=VOICE_CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[TTS] Voice connect timed out (guild_id=%s)", guild_id)
                await asyncio.sleep(0.4)
                recovered_vc = await self._recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                if recovered_vc is not None:
                    return recovered_vc, None
                return None, None
            except discord.ClientException as e:
                logger.warning("[TTS] Voice connect failed (guild_id=%s): %s", guild_id, e)
                if "already connected" in str(e).lower():
                    recovered_vc = await self._recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                    if recovered_vc is not None:
                        return recovered_vc, None
                return None, None
            except Exception as e:
                logger.exception("[TTS] Voice connect error (guild_id=%s): %s", guild_id, e)
                recovered_vc = await self._recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                if recovered_vc is not None:
                    return recovered_vc, None
                return None, None

            self._voice_clients[guild_id] = new_vc
            embed = discord.Embed(
                title="🎤 Mococo TTS",
                description=f"{channel.mention} 음성 채널에 참가했어요!\n이 채널에서 보내는 메시지를 읽어드릴게요!",
                color=0x2ECC71,
            )
            embed.set_thumbnail(url="https://i.namu.wiki/i/u253q5zv58zJ1twYkeea-czVz8SQsvX-a1jVZ8oYsTVDH_TRC8-bpcVa4aKYQs5lI55B9srLYF9JJFUPbkI8MA.webp")
            return new_vc, embed

    async def disconnect(self, guild_id: int, guild: Optional[discord.Guild] = None, reason: str = "disconnect") -> None:
        vc = self.get_session(guild, guild_id)
        if vc is not None:
            try:
                if vc.is_playing():
                    vc.stop()
            except Exception as e:
                logger.warning("[TTS] vc.stop failed (guild_id=%s, reason=%s): %s", guild_id, reason, e)

            try:
                await asyncio.wait_for(vc.disconnect(force=True), timeout=VOICE_DISCONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[TTS] vc.disconnect timed out (guild_id=%s, reason=%s)", guild_id, reason)
            except Exception as e:
                logger.warning("[TTS] vc.disconnect failed (guild_id=%s, reason=%s): %s", guild_id, reason, e)

            try:
                vc.cleanup()
            except Exception:
                pass

        self._voice_clients.pop(guild_id, None)


@dataclass(slots=True)
class DaveBridgeSession:
    guild_id: int
    channel_id: int


class DaveBridgeVoiceClient(BaseVoiceClient):
    """
    DAVE-capable backend adapter via external voice bridge.

    The bridge service is expected to handle Discord DAVE voice transport
    (for example via Lavalink 4.2+ or a native DAVE stack) and expose:
    - POST /voice/join
    - POST /voice/play_pcm
    - POST /voice/stop
    - POST /voice/disconnect
    """

    mode = "dave"
    supports_dave = True

    def __init__(self, base_url: str, token: str = "", timeout_sec: float = 35.0, max_audio_mb: int = 8) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = max(5.0, float(timeout_sec))
        self._max_audio_bytes = max(1, int(max_audio_mb)) * 1024 * 1024

        headers: Dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        self._http = httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=self._timeout)
        self._sessions: Dict[int, DaveBridgeSession] = {}
        self._connect_locks: Dict[int, asyncio.Lock] = {}
        self._play_tasks: Dict[int, asyncio.Task] = {}
        self._playing: set[int] = set()

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._connect_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._connect_locks[guild_id] = lock
        return lock

    async def _post(self, path: str, payload: Dict[str, Any]) -> tuple[bool, str]:
        try:
            res = await self._http.post(path, json=payload)
        except Exception as e:
            return False, str(e)
        if 200 <= res.status_code < 300:
            return True, ""
        txt = (res.text or "").strip()
        return False, f"{res.status_code} {txt[:200]}"

    def is_alive(self, session: Optional[Any]) -> bool:
        if not isinstance(session, DaveBridgeSession):
            return False
        known = self._sessions.get(session.guild_id)
        return known is not None and known.channel_id == session.channel_id

    def get_session(self, guild: Optional[discord.Guild], guild_id: int) -> Optional[Any]:
        return self._sessions.get(guild_id)

    def connected_guild_ids(self) -> list[int]:
        return list(self._sessions.keys())

    def clear_cached_session(self, guild_id: int) -> None:
        self._sessions.pop(guild_id, None)
        self._playing.discard(guild_id)
        task = self._play_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def clear_connect_lock(self, guild_id: int) -> None:
        self._connect_locks.pop(guild_id, None)

    def channel_id_of(self, session: Optional[Any]) -> Optional[int]:
        if isinstance(session, DaveBridgeSession):
            return session.channel_id
        return None

    def is_playing(self, session: Optional[Any]) -> bool:
        if not isinstance(session, DaveBridgeSession):
            return False
        return session.guild_id in self._playing

    async def join(self, channel: discord.VoiceChannel, guild_id: int) -> Tuple[Optional[Any], Optional[discord.Embed]]:
        lock = self._get_lock(guild_id)
        async with lock:
            guild = channel.guild
            me = getattr(guild, "me", None)
            if me is not None:
                perms = channel.permissions_for(me)
                if not (perms.connect and perms.speak):
                    return None, None

            current = self._sessions.get(guild_id)
            if current is not None and current.channel_id == channel.id:
                return current, None

            ok, err = await self._post("/voice/join", {"guild_id": guild_id, "channel_id": channel.id})
            if not ok:
                logger.warning("[TTS] DAVE bridge join failed (guild_id=%s): %s", guild_id, err)
                return None, None

            session = DaveBridgeSession(guild_id=guild_id, channel_id=channel.id)
            self._sessions[guild_id] = session
            embed = discord.Embed(
                title="🎤 Mococo TTS",
                description=f"{channel.mention} 음성 채널에 참가했어요!\n이 채널에서 보내는 메시지를 읽어드릴게요!",
                color=0x2ECC71,
            )
            embed.set_thumbnail(url="https://i.namu.wiki/i/u253q5zv58zJ1twYkeea-czVz8SQsvX-a1jVZ8oYsTVDH_TRC8-bpcVa4aKYQs5lI55B9srLYF9JJFUPbkI8MA.webp")
            return session, embed

    async def _play_pcm(self, session: DaveBridgeSession, pcm: bytes, source: discord.AudioSource, after: AfterCallback) -> None:
        err: Optional[Exception] = None
        try:
            if len(pcm) > self._max_audio_bytes:
                raise RuntimeError(f"PCM payload too large ({len(pcm)} bytes)")
            payload = {
                "guild_id": session.guild_id,
                "channel_id": session.channel_id,
                "sample_rate": 48000,
                "channels": 2,
                "pcm_s16le_b64": base64.b64encode(pcm).decode("ascii"),
            }
            ok, msg = await self._post("/voice/play_pcm", payload)
            if not ok:
                raise RuntimeError(msg or "bridge rejected playback")
        except Exception as e:
            err = e
        finally:
            self._playing.discard(session.guild_id)
            cur = self._play_tasks.get(session.guild_id)
            if cur is asyncio.current_task():
                self._play_tasks.pop(session.guild_id, None)
            try:
                source.cleanup()
            except Exception:
                pass
            try:
                after(err)
            except Exception:
                pass

    def play(self, session: Any, source: discord.AudioSource, after: AfterCallback) -> None:
        if not isinstance(session, DaveBridgeSession):
            raise discord.ClientException("Invalid DAVE bridge session")

        pcm = bytearray()
        while True:
            chunk = source.read()
            if not chunk:
                break
            pcm.extend(chunk)

        old_task = self._play_tasks.get(session.guild_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()

        self._playing.add(session.guild_id)
        t = asyncio.create_task(self._play_pcm(session, bytes(pcm), source, after))
        self._play_tasks[session.guild_id] = t

    def stop(self, session: Optional[Any]) -> None:
        if not isinstance(session, DaveBridgeSession):
            return
        gid = session.guild_id
        task = self._play_tasks.pop(gid, None)
        if task is not None and not task.done():
            task.cancel()
        self._playing.discard(gid)
        asyncio.create_task(self._post("/voice/stop", {"guild_id": gid}))

    async def disconnect(self, guild_id: int, guild: Optional[discord.Guild] = None, reason: str = "disconnect") -> None:
        session = self._sessions.get(guild_id)
        task = self._play_tasks.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._playing.discard(guild_id)
        self._sessions.pop(guild_id, None)

        if session is None:
            return
        ok, err = await self._post("/voice/disconnect", {"guild_id": guild_id, "reason": reason})
        if not ok:
            logger.warning("[TTS] DAVE bridge disconnect failed (guild_id=%s): %s", guild_id, err)


def _build_voice_client() -> BaseVoiceClient:
    mode = os.getenv("VOICE_CLIENT_MODE", "pycord").strip().lower()
    if mode in {"dave", "lavalink"}:
        bridge_url = os.getenv("DAVE_BRIDGE_URL", "").strip()
        if not bridge_url:
            logger.warning("[TTS] VOICE_CLIENT_MODE=%s but DAVE_BRIDGE_URL is empty. Fallback to pycord.", mode)
            return PycordVoiceClient()
        token = os.getenv("DAVE_BRIDGE_TOKEN", "").strip()
        timeout = float(os.getenv("DAVE_BRIDGE_TIMEOUT_SEC", "35"))
        max_mb = int(os.getenv("DAVE_BRIDGE_MAX_AUDIO_MB", "8"))
        logger.info("[TTS] Using DAVE bridge voice client (%s).", bridge_url)
        return DaveBridgeVoiceClient(base_url=bridge_url, token=token, timeout_sec=timeout, max_audio_mb=max_mb)
    return PycordVoiceClient()


voice_client = _build_voice_client()
