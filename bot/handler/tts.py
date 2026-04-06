from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import discord
import httpx

from core.tts_channels import tts_channel_manager
from core.tts_engine_manager import tts_engine_manager

logger = logging.getLogger("tts")

tts_queues: Dict[int, asyncio.Queue] = {}
tts_locks: Dict[int, asyncio.Lock] = {}
voice_clients: Dict[int, Any] = {}
voice_connect_locks: Dict[int, asyncio.Lock] = {}
playback_tasks: Dict[int, asyncio.Task] = {}
tts_gen_sems: Dict[int, asyncio.Semaphore] = {}
guild_active_tasks: Dict[int, set[asyncio.Task]] = {}

MAX_MESSAGE_LENGTH = 200
MAX_QUEUE_SIZE = 10

_CPU = os.cpu_count() or 4
_MAX_FFMPEG = max(2, min(8, _CPU))
_FFMPEG_SEM = asyncio.Semaphore(_MAX_FFMPEG)
FFMPEG_TIMEOUT = 30.0

PLAYBACK_TIMEOUT = 30.0
VOICE_CONNECT_TIMEOUT = 20.0
VOICE_MOVE_TIMEOUT = 15.0
VOICE_DISCONNECT_TIMEOUT = 10.0
TASK_CANCEL_TIMEOUT = 3.0

SILENCE_TRIM_FILTER = (
    "silenceremove="
    "start_periods=0:"
    "stop_periods=1:"
    "stop_duration=0.25:"
    "stop_threshold=-50dB"
)

EDGE_VOICE_MAP: Dict[str, str] = {
    "engine2": "ko-KR-SunHiNeural",
    "engine7": "ko-KR-HyunsuMultilingualNeural",
    "engine9": "ko-KR-InJoonNeural",
}
TIKTOK_VOICE_MAP: Dict[str, str] = {
    "engine3": "kr_002",
    "engine4": "kr_003",
    "engine5": "kr_004",
}

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_BASE_PATH = os.path.join(os.path.dirname(_MODULE_DIR), "utils", "tts")

CUSTOM_AUDIO_CONFIG: Dict[str, Dict[str, object]] = {
    "기분좋은향기": {"file": "mokoko.mp3", "volume": 0.8, "aliases": ["[기분좋은향기]"]},
    "오셨군요": {"file": "welcome.mp3", "volume": 1.0, "aliases": ["[오셨군요]"]},
    "라일라이": {"file": "hello.mp3", "volume": 0.4, "aliases": ["[라일라이]"]},
    "탈출의노래": {"file": "escape.mp3", "volume": 0.2, "aliases": ["[탈출의노래]"]},
}

_alias_to_key: Dict[str, str] = {}


@dataclass
class PlayItem:
    mode: str
    payload: bytes | str
    title: str


class _LRU:
    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self.data: OrderedDict[str, bytes] = OrderedDict()

    def get(self, k: str) -> Optional[bytes]:
        v = self.data.get(k)
        if v is None:
            return None
        self.data.move_to_end(k)
        return v

    def set(self, k: str, v: bytes) -> None:
        self.data[k] = v
        self.data.move_to_end(k)
        if len(self.data) > self.maxsize:
            self.data.popitem(last=False)


_FILE_PCM_CACHE = _LRU(128)
_TEXT_PCM_CACHE = _LRU(256)
_TEXT_MP3_CACHE = _LRU(256)


def _rebuild_alias_map() -> None:
    _alias_to_key.clear()
    for key, cfg in CUSTOM_AUDIO_CONFIG.items():
        for _alias in cfg.get("aliases", []):
            s = str(_alias)
            if s.startswith("[") and s.endswith("]"):
                _alias_to_key[s.lower()] = key


_rebuild_alias_map()


def _voice_backend() -> str:
    raw = (os.getenv("TTS_VOICE_BACKEND") or "pycord").strip().lower()
    return "lavalink" if raw == "lavalink" else "pycord"




def _is_benign_proactor_pipe_reset(context: dict[str, Any]) -> bool:
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror != 10054 and "10054" not in repr(exc):
        return False
    parts = [
        str(context.get("message") or ""),
        repr(context.get("handle")),
        repr(context.get("transport")),
        repr(context.get("protocol")),
        repr(context.get("source_traceback")),
    ]
    blob = " ".join(parts)
    return "_ProactorBasePipeTransport" in blob or "PipeTransport" in blob or "Proactor" in blob


def _ensure_asyncio_exception_filter() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if getattr(loop, "_mococo_tts_exception_filter_installed", False):
        return
    previous_handler = loop.get_exception_handler()

    def _handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_benign_proactor_pipe_reset(context):
            logger.debug("[TTS] Ignored benign Windows Proactor pipe reset: %r", context.get("exception"))
            return
        if previous_handler is not None:
            previous_handler(loop, context)
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    setattr(loop, "_mococo_tts_exception_filter_installed", True)

def add_custom_sound(key: str, file: str, volume: float = 0.8, aliases: Optional[List[str]] = None) -> None:
    if not key or not file:
        raise ValueError("Key and file must be provided")
    alias = f"[{key}]"
    CUSTOM_AUDIO_CONFIG[key] = {"file": file, "volume": volume, "aliases": [alias]}
    _rebuild_alias_map()


def remove_custom_sound(key: str) -> None:
    CUSTOM_AUDIO_CONFIG.pop(key, None)
    _rebuild_alias_map()


def clear_custom_sounds() -> None:
    CUSTOM_AUDIO_CONFIG.clear()
    _rebuild_alias_map()


def get_available_custom_sounds() -> List[dict]:
    items: List[dict] = []
    base = AUDIO_BASE_PATH
    exists = os.path.exists
    join = os.path.join
    for key, cfg in CUSTOM_AUDIO_CONFIG.items():
        path = join(base, str(cfg.get("file", "")))
        if exists(path):
            items.append({"key": key, "file": cfg.get("file"), "aliases": cfg.get("aliases", [])})
    return items


def guild_dir_path(guild_id: int) -> str:
    return os.path.join(AUDIO_BASE_PATH, str(guild_id))


def ensure_dir_for_save(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def rmdir_if_empty(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except Exception as e:
        logger.debug("[TTS] rmdir_if_empty failed (path=%s): %s", path, e)


_guild_custom_cache: Dict[int, Dict[str, Any]] = {}


def _get_guild_index_path(guild_id: int) -> str:
    return os.path.join(guild_dir_path(guild_id), "custom_sounds.json")


def _load_guild_index(guild_id: int) -> Dict[str, Dict[str, Any]]:
    cached = _guild_custom_cache.get(guild_id)
    if cached and isinstance(cached.get("index"), dict):
        return cached["index"]
    path = _get_guild_index_path(guild_id)
    index: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                index = json.load(f) or {}
        except Exception as e:
            logger.warning("[TTS] Failed to load guild custom index (guild_id=%s): %s", guild_id, e)
            index = {}
    _guild_custom_cache[guild_id] = {"index": index}
    return index


def _save_guild_index(guild_id: int, index: Dict[str, Dict[str, Any]]) -> None:
    _guild_custom_cache[guild_id] = {"index": index}
    ensure_dir_for_save(guild_dir_path(guild_id))
    path = _get_guild_index_path(guild_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[TTS] Failed to save guild custom index (guild_id=%s): %s", guild_id, e)
    try:
        if not index:
            rmdir_if_empty(guild_dir_path(guild_id))
    except Exception as e:
        logger.debug("[TTS] cleanup guild_dir_path failed (guild_id=%s): %s", guild_id, e)


def get_guild_custom_sounds(guild_id: int) -> List[Dict[str, Any]]:
    idx = _load_guild_index(guild_id)
    items: List[Dict[str, Any]] = []
    for key, meta in idx.items():
        items.append({"key": key, "file": meta.get("file"), "aliases": meta.get("aliases", [])})
    return items


def add_guild_custom_sound(guild_id: int, key: str, filename: str, aliases: Optional[List[str]] = None) -> None:
    key = str(key).strip()
    if not key or not filename:
        raise ValueError("invalid key or filename")
    bracket_alias = f"[{key}]"
    idx = _load_guild_index(guild_id)
    idx[key] = {"file": filename, "aliases": [bracket_alias]}
    _save_guild_index(guild_id, idx)


def remove_guild_custom_sound(guild_id: int, key: str) -> None:
    key = str(key).strip()
    idx = _load_guild_index(guild_id)
    meta = idx.pop(key, None)
    _save_guild_index(guild_id, idx)
    if meta and meta.get("file"):
        base_dir = os.path.realpath(guild_dir_path(guild_id))
        base_prefix = base_dir + os.sep
        fpath = os.path.realpath(os.path.join(base_dir, str(meta["file"])))
        try:
            if fpath.startswith(base_prefix) and os.path.exists(fpath):
                os.remove(fpath)
        except Exception as e:
            logger.warning("[TTS] Failed to remove guild custom file (guild_id=%s, file=%s): %s", guild_id, fpath, e)
    rmdir_if_empty(guild_dir_path(guild_id))


def check_guild_custom_audio_trigger(guild_id: int, text: str) -> Optional[str]:
    if not text:
        return None
    tl = text.lower()
    idx = _load_guild_index(guild_id)
    for key, meta in idx.items():
        for a in (meta.get("aliases", []) or []):
            s = str(a)
            if s.startswith("[") and s.endswith("]"):
                if s.lower() in tl:
                    return key
    return None


class TTSAudioSource(discord.AudioSource):
    def __init__(self, audio_data: bytes) -> None:
        self._bio = io.BytesIO(audio_data)
        self._closed = False

    def read(self) -> bytes:
        if self._closed:
            return b""
        frame_size = 3840
        chunk = self._bio.read(frame_size)
        if not chunk:
            return b""
        if len(chunk) < frame_size:
            chunk += b"\x00" * (frame_size - len(chunk))
        return chunk

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._bio.close()
        except Exception as e:
            logger.debug("[TTS] TTSAudioSource.cleanup failed: %s", e)


_RE_URL = re.compile(r"https?://\S+")
_RE_MENTION = re.compile(r"<[@#:][!&]?[^>]+>")
_RE_EMOJI = re.compile(r"<a?:\w+:\d+>")
_RE_MD = re.compile(r"[\*_`~|]")
_RE_SP = re.compile(r"\s+")
_RE_KK = re.compile(r"[ㅋ]+", flags=re.IGNORECASE)

_KOREAN_INITIALISM_MAP: Dict[str, str] = {
    "ㅎㅇ": "하이",
    "ㅂㅂ": "바이",
    "ㄴㄴ": "노노",
    "ㅇㅇ": "응응",
    "ㅇㅋ": "오케이",
    "ㄱㄱ": "고고",
    "ㅇㅈ": "인정",
    "ㄹㅇ": "리얼",
    "ㅈㅅ": "죄송",
    "ㅊㅋ": "축하",
    "ㅅㄱ": "수고",
    "ㅅㄱㅇ": "수고요",
    "ㄱㅅ": "감사",
    "ㄱㅊ": "괜찮아",
    "ㅁㅈ": "맞아",
    "ㄷㄷ": "덜덜",
    "ㅎㄷㄷ": "후덜덜",
    "ㅎㅎ": "헤헤",
    "ㅈㅂ": "제발",
    "ㅈㅈ": "지지",
    "ㅈㄱ": "지금",
    "ㄱㄷ": "기달",
    "ㅇㄴ": "아니",
    "ㅇㄱ": "이거",
    "ㅎㅇㅌ": "화이팅",
    "ㅇㄱㄹㅇ": "이거 리얼",
    "ㅂㅅ": "바보",
}

_RE_KO_INITIALISM = re.compile(r"(?<![0-9A-Za-z가-힣])([ㄱ-ㅎ]{2,})(?![0-9A-Za-z가-힣])")


def _normalize_korean_initialisms(text: str) -> str:
    if not text:
        return ""

    def _repl(m: re.Match) -> str:
        key = m.group(1)
        return _KOREAN_INITIALISM_MAP.get(key, key)

    out = _RE_KO_INITIALISM.sub(_repl, text)
    if re.fullmatch(r"[ㄱ-ㅎ\s]+", out):
        return ""
    return out


def _normalize_laughter(text: str) -> str:
    return _RE_KK.sub("크크크", text)


def clean_message_for_tts(text: str) -> str:
    if not text or not text.strip():
        return ""
    text = _RE_URL.sub("링크", text)
    text = _RE_MENTION.sub("", text)
    text = _RE_EMOJI.sub("", text)
    text = _RE_MD.sub("", text)
    text = _RE_SP.sub(" ", text.strip())
    text = _normalize_laughter(text)
    text = _normalize_korean_initialisms(text)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH] + "..."
    return text


def check_custom_audio_trigger(text: str) -> Optional[str]:
    if not text:
        return None
    tl = text.lower()
    for alias, key in _alias_to_key.items():
        if alias.startswith("[") and alias.endswith("]"):
            if alias in tl:
                return key
    return None


def get_audio_file_path(sound_key: str) -> Tuple[Optional[str], float]:
    cfg = CUSTOM_AUDIO_CONFIG.get(sound_key)
    if not cfg:
        return None, 0.8
    return os.path.join(AUDIO_BASE_PATH, str(cfg.get("file", ""))), float(cfg.get("volume", 0.8))



def _normalize_pcm_output(stdout: bytes) -> bytes:
    if stdout and (len(stdout) % 4 != 0):
        stdout += b"\x00" * (4 - (len(stdout) % 4))
    return stdout


def _run_ffmpeg_from_bytes_sync(mp3_bytes: bytes, volume: float, trim_tail: bool) -> Tuple[Optional[bytes], Optional[str]]:
    if not mp3_bytes:
        return None, "오디오 데이터가 비었습니다."
    af = f"volume={volume}"
    if trim_tail:
        af = f"{af},{SILENCE_TRIM_FILTER}"
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-nostdin",
        "-f", "mp3", "-i", "pipe:0",
        "-vn", "-sn", "-dn",
        "-af", af,
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=mp3_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return None, "FFmpeg를 찾을 수 없습니다."
    except subprocess.TimeoutExpired:
        return None, "오디오 변환 시간 초과(FFmpeg)."
    except Exception as e:
        return None, f"오디오 변환 중 오류: {e}"
    if proc.returncode != 0:
        msg = (proc.stderr or b"").decode("utf-8", errors="ignore")
        return None, f"오디오 변환 실패: {msg[:200]}"
    return _normalize_pcm_output(proc.stdout or b""), None


def _run_ffmpeg_from_file_sync(path: str, volume: float) -> Tuple[Optional[bytes], Optional[str]]:
    af = f"volume={volume}"
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-nostdin",
        "-i", path,
        "-vn", "-sn", "-dn",
        "-af", af,
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return None, "FFmpeg를 찾을 수 없습니다."
    except subprocess.TimeoutExpired:
        return None, "오디오 변환 시간 초과(FFmpeg)."
    except Exception as e:
        return None, f"오디오 변환 중 오류: {e}"
    if proc.returncode != 0:
        msg = (proc.stderr or b"").decode("utf-8", errors="ignore")
        return None, f"오디오 변환 실패: {msg[:200]}"
    return _normalize_pcm_output(proc.stdout or b""), None


async def _ffmpeg_from_bytes(mp3_bytes: bytes, volume: float = 0.8, trim_tail: bool = False) -> Tuple[Optional[bytes], Optional[str]]:
    async with _FFMPEG_SEM:
        return await asyncio.to_thread(_run_ffmpeg_from_bytes_sync, mp3_bytes, volume, trim_tail)


async def _ffmpeg_from_file(path: str, volume: float = 0.8) -> Tuple[Optional[bytes], Optional[str]]:
    async with _FFMPEG_SEM:
        return await asyncio.to_thread(_run_ffmpeg_from_file_sync, path, volume)


def _file_cache_key(path: str, volume: float) -> str:
    try:
        st = os.stat(path)
        mt = int(st.st_mtime)
        sz = st.st_size
    except Exception:
        mt = 0
        sz = 0
    return f"FILE|{path}|{volume:.3f}|{mt}|{sz}"


def _text_cache_key(engine: str, voice: str, text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"TEXT|{engine}|{voice}|{h}"


async def _pcm_from_file_cached(path: str, volume: float) -> Tuple[Optional[bytes], Optional[str]]:
    key = _file_cache_key(path, volume)
    cached = _FILE_PCM_CACHE.get(key)
    if cached is not None:
        return cached, None
    pcm, err = await _ffmpeg_from_file(path, volume=volume)
    if pcm is not None:
        _FILE_PCM_CACHE.set(key, pcm)
    return pcm, err


async def create_custom_audio(sound_key: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        path, volume = get_audio_file_path(sound_key)
        if not path:
            return None, "지원하지 않는 오디오 타입입니다."
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None, f"오디오 파일을 찾을 수 없습니다: {os.path.basename(path)}"
        except Exception as e:
            return None, f"오디오 파일 확인 중 오류: {e}"
        if st.st_size > 10 * 1024 * 1024:
            return None, "오디오 파일이 너무 큽니다. (최대 10MB)"
        return await _pcm_from_file_cached(path, volume=volume)
    except Exception as e:
        return None, f"커스텀 오디오 생성 중 오류: {e}"


async def _naver_tts_mp3(text: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        if not text or not text.strip():
            return None, "읽을 수 있는 텍스트가 없어요."
        try:
            from navertts import NaverTTS  # type: ignore
        except Exception:
            return None, "Engine 1 이 작동하지 않아요."
        content = text.strip()
        if not content:
            return None, "텍스트가 너무 짧아요."
        cache_key = _text_cache_key("engine1mp3", "naver", content)
        cached = _TEXT_MP3_CACHE.get(cache_key)
        if cached is not None:
            return cached, None
        buf = io.BytesIO()
        tts = NaverTTS(content)
        await asyncio.to_thread(tts.write_to_fp, buf)
        mp3_bytes = buf.getvalue()
        if not mp3_bytes:
            return None, "TTS 데이터 생성에 실패했습니다."
        _TEXT_MP3_CACHE.set(cache_key, mp3_bytes)
        return mp3_bytes, None
    except Exception as e:
        return None, f"Engine 1 TTS 생성 중 오류: {e}"


async def create_naver_tts_audio(text: str) -> Tuple[Optional[bytes], Optional[str]]:
    mp3_bytes, err = await _naver_tts_mp3(text)
    if not mp3_bytes:
        return None, err
    cache_key = _text_cache_key("engine1pcm", "naver", text.strip())
    cached = _TEXT_PCM_CACHE.get(cache_key)
    if cached is not None:
        return cached, None
    pcm, err = await _ffmpeg_from_bytes(mp3_bytes, volume=0.8, trim_tail=False)
    if pcm is not None:
        _TEXT_PCM_CACHE.set(cache_key, pcm)
    return pcm, err


async def _edge_tts_mp3(text: str, voice: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        if not text or not text.strip():
            return None, "읽을 수 있는 텍스트가 없어요."
        try:
            import edge_tts  # type: ignore
        except Exception:
            return None, "Engine 2~9 가 작동하지 않아요."
        content = text.strip()
        cache_key = _text_cache_key("edgemp3", voice, content)
        cached = _TEXT_MP3_CACHE.get(cache_key)
        if cached is not None:
            return cached, None
        communicate = edge_tts.Communicate(content, voice)
        audio_buf = bytearray()
        try:
            async for message in communicate.stream():
                if isinstance(message, dict) and message.get("type") == "audio" and message.get("data"):
                    audio_buf.extend(message["data"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[TTS] edge_tts stream failed: %s", e)
            audio_buf = bytearray()
        if not audio_buf:
            tmp_fn: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmpf:
                    tmp_fn = tmpf.name
                communicate = edge_tts.Communicate(content, voice)
                await communicate.save(tmp_fn)
                if not os.path.exists(tmp_fn) or os.path.getsize(tmp_fn) == 0:
                    return None, "TTS 데이터 생성에 실패했습니다."
                with open(tmp_fn, "rb") as f:
                    audio_buf = bytearray(f.read())
            finally:
                if tmp_fn and os.path.exists(tmp_fn):
                    try:
                        os.unlink(tmp_fn)
                    except Exception as e:
                        logger.debug("[TTS] Failed to unlink temp mp3: %s", e)
        if not audio_buf:
            return None, "TTS 데이터 생성에 실패했습니다."
        out = bytes(audio_buf)
        _TEXT_MP3_CACHE.set(cache_key, out)
        return out, None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return None, f"Engine 생성 중 오류: {e}"


async def create_edge_tts_audio(text: str, voice: str) -> Tuple[Optional[bytes], Optional[str]]:
    mp3_bytes, err = await _edge_tts_mp3(text, voice)
    if not mp3_bytes:
        return None, err
    cache_key = _text_cache_key("edgepcm", voice, text.strip())
    cached = _TEXT_PCM_CACHE.get(cache_key)
    if cached is not None:
        return cached, None
    pcm, err = await _ffmpeg_from_bytes(mp3_bytes, volume=0.8, trim_tail=True)
    if pcm is not None:
        _TEXT_PCM_CACHE.set(cache_key, pcm)
    return pcm, err


async def _tiktok_tts_mp3(text: str, voice: str) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        if not text or not text.strip():
            return None, "읽을 수 있는 텍스트가 없어요."
        content = text.strip()
        cache_key = _text_cache_key("tiktokmp3", voice, content)
        cached = _TEXT_MP3_CACHE.get(cache_key)
        if cached is not None:
            return cached, None
        try:
            import aiohttp  # type: ignore
        except Exception:
            return None, "TikTok TTS 네트워크 모듈(aiohttp)을 사용할 수 없습니다."
        endpoints = (
            ("https://tiktok-tts.weilnet.workers.dev/api/generation", "data"),
            ("https://gesserit.co/api/tiktok-tts", "base64"),
        )
        separated = re.findall(r".*?[.,!?:;-]|.+", content)
        limit = 300
        for i, chunk in enumerate(list(separated)):
            if len(chunk.encode("utf-8")) > limit:
                separated[i:i + 1] = re.findall(r".*?[ ]|.+", chunk)
        merged: List[str] = []
        cur = ""
        for part in separated:
            if not part:
                continue
            if len(cur.encode("utf-8")) + len(part.encode("utf-8")) <= limit:
                cur += part
            else:
                if cur:
                    merged.append(cur)
                cur = part
        if cur:
            merged.append(cur)
        timeout = aiohttp.ClientTimeout(total=12)
        headers = {"User-Agent": "Mozilla/5.0"}
        for url, resp_key in endpoints:
            audio_chunks: List[str] = [""] * len(merged)

            async def fetch_chunk(session: Any, idx: int, chunk: str) -> None:
                async with session.post(url, json={"text": chunk, "voice": voice}, headers=headers) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        raise RuntimeError("Invalid JSON")
                    b64 = data.get(resp_key)
                    if not isinstance(b64, str) or not b64:
                        raise RuntimeError("Empty audio")
                    audio_chunks[idx] = b64

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    await asyncio.gather(*(fetch_chunk(session, i, c) for i, c in enumerate(merged)))
                b64_all = "".join(audio_chunks)
                if not b64_all:
                    continue
                mp3_bytes = base64.b64decode(b64_all)
                if not mp3_bytes:
                    continue
                _TEXT_MP3_CACHE.set(cache_key, mp3_bytes)
                return mp3_bytes, None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("[TTS] tiktok endpoint failed (%s): %s", url, e)
                continue
        return None, "TikTok TTS 데이터 생성에 실패했습니다."
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return None, f"TikTok TTS 생성 중 오류: {e}"


async def create_tiktok_tts_audio(text: str, voice: str) -> Tuple[Optional[bytes], Optional[str]]:
    mp3_bytes, err = await _tiktok_tts_mp3(text, voice)
    if not mp3_bytes:
        return None, err
    cache_key = _text_cache_key("tiktokpcm", voice, text.strip())
    cached = _TEXT_PCM_CACHE.get(cache_key)
    if cached is not None:
        return cached, None
    pcm, err = await _ffmpeg_from_bytes(mp3_bytes, volume=0.8, trim_tail=False)
    if pcm is not None:
        _TEXT_PCM_CACHE.set(cache_key, pcm)
    return pcm, err


class _ServedAudio:
    __slots__ = ("path", "expires_at")

    def __init__(self, path: str, expires_at: float) -> None:
        self.path = path
        self.expires_at = expires_at


_served_audio: Dict[str, _ServedAudio] = {}
_served_audio_lock = asyncio.Lock()
_http_runner: Any = None
_http_site: Any = None
_http_cleanup_task: Optional[asyncio.Task] = None


def _http_bind_host() -> str:
    return (os.getenv("TTS_HTTP_BIND_HOST") or "127.0.0.1").strip()


def _http_port() -> int:
    try:
        return int((os.getenv("TTS_HTTP_PORT") or "8787").strip())
    except Exception:
        return 8787


def _public_base_url() -> str:
    raw = (os.getenv("TTS_PUBLIC_BASE_URL") or f"http://127.0.0.1:{_http_port()}").strip().rstrip("/")
    return raw


def _served_tmp_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "mococo_tts_served")
    os.makedirs(path, exist_ok=True)
    return path


async def _ensure_audio_http_server() -> None:
    global _http_runner, _http_site, _http_cleanup_task
    if _http_site is not None:
        return
    try:
        from aiohttp import web  # type: ignore
    except Exception as e:
        raise RuntimeError(f"aiohttp 가 필요합니다: {e}")
    async with _served_audio_lock:
        if _http_site is not None:
            return

        async def handle_audio(request: Any) -> Any:
            token = str(request.match_info.get("token") or "")
            entry = _served_audio.get(token)
            if entry is None:
                raise web.HTTPNotFound()
            if entry.expires_at <= time.time():
                _served_audio.pop(token, None)
                raise web.HTTPGone()
            if not os.path.exists(entry.path):
                _served_audio.pop(token, None)
                raise web.HTTPNotFound()
            return web.FileResponse(entry.path)

        app = web.Application(client_max_size=32 * 1024 * 1024)
        app.router.add_get("/tts/{token}", handle_audio)
        _http_runner = web.AppRunner(app, access_log=None)
        await _http_runner.setup()
        _http_site = web.TCPSite(_http_runner, host=_http_bind_host(), port=_http_port())
        await _http_site.start()
        if _http_cleanup_task is None or _http_cleanup_task.done():
            _http_cleanup_task = asyncio.create_task(_served_audio_cleanup_worker())


async def _served_audio_cleanup_worker() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired: List[Tuple[str, str]] = []
        for token, entry in list(_served_audio.items()):
            if entry.expires_at <= now:
                expired.append((token, entry.path))
        for token, path in expired:
            _served_audio.pop(token, None)
            try:
                if path.startswith(_served_tmp_dir() + os.sep) and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass


async def _register_served_file(path: str, ttl_sec: int = 600) -> str:
    await _ensure_audio_http_server()
    token = hashlib.sha1(f"{path}|{time.time()}|{os.urandom(8).hex()}".encode("utf-8"), usedforsecurity=False).hexdigest()
    _served_audio[token] = _ServedAudio(path=path, expires_at=time.time() + ttl_sec)
    return f"{_public_base_url()}/tts/{quote(token)}"


async def _register_served_bytes(data: bytes, suffix: str = ".mp3", ttl_sec: int = 600) -> str:
    base = _served_tmp_dir()
    token = hashlib.sha1(f"{time.time()}|{os.urandom(8).hex()}".encode("utf-8"), usedforsecurity=False).hexdigest()
    path = os.path.join(base, f"{token}{suffix}")
    with open(path, "wb") as f:
        f.write(data)
    _served_audio[token] = _ServedAudio(path=path, expires_at=time.time() + ttl_sec)
    await _ensure_audio_http_server()
    return f"{_public_base_url()}/tts/{quote(token)}"


class _LavalinkNode:
    def __init__(self) -> None:
        self.base_url = (os.getenv("LAVALINK_BASE_URL") or "http://127.0.0.1:2333").strip().rstrip("/")
        self.password = (os.getenv("LAVALINK_PASSWORD") or "youshallnotpass").strip()
        self.client_name = (os.getenv("LAVALINK_CLIENT_NAME") or "mococo-tts/1.0").strip()
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), headers={"Authorization": self.password})
        self.ready = asyncio.Event()
        self.lock = asyncio.Lock()
        self.ws_task: Optional[asyncio.Task] = None
        self.aio_session: Any = None
        self.session_id: Optional[str] = None
        self.user_id: Optional[int] = None
        self.guild_events: Dict[int, asyncio.Queue] = {}
        self.guild_connected: Dict[int, asyncio.Event] = {}

    async def _ensure_http(self) -> None:
        if not self.http.is_closed:
            return
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), headers={"Authorization": self.password})

    async def ensure_started(self, user_id: int) -> None:
        _ensure_asyncio_exception_filter()
        self.user_id = user_id
        await self._ensure_http()
        if self.ws_task and not self.ws_task.done() and self.ready.is_set():
            return
        async with self.lock:
            await self._ensure_http()
            if self.ws_task and not self.ws_task.done() and self.ready.is_set():
                return
            self.ready.clear()
            self.ws_task = asyncio.create_task(self._ws_loop())
        await asyncio.wait_for(self.ready.wait(), timeout=10.0)

    async def _ws_loop(self) -> None:
        try:
            import aiohttp  # type: ignore
        except Exception as e:
            logger.error("[TTS] aiohttp unavailable for Lavalink websocket: %s", e)
            return
        session: Any = None
        ws: Any = None
        try:
            headers = {
                "Authorization": self.password,
                "User-Id": str(self.user_id or 0),
                "Client-Name": self.client_name,
            }
            session = aiohttp.ClientSession(headers=headers)
            self.aio_session = session
            ws = await session.ws_connect(f"{self.base_url.replace('http', 'ws', 1)}/v4/websocket", heartbeat=30)
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                op = data.get("op")
                if op == "ready":
                    self.session_id = str(data.get("sessionId") or "") or None
                    self.ready.set()
                    if self.session_id:
                        try:
                            await self._ensure_http()
                            await self.http.patch(f"{self.base_url}/v4/sessions/{self.session_id}", json={"resuming": True, "timeout": 60})
                        except Exception:
                            pass
                elif op == "playerUpdate":
                    guild_id = _to_int(data.get("guildId"))
                    if guild_id is not None and data.get("state", {}).get("connected"):
                        evt = self.guild_connected.get(guild_id)
                        if evt is not None and not evt.is_set():
                            evt.set()
                elif op == "event":
                    guild_id = _to_int(data.get("guildId"))
                    if guild_id is None:
                        continue
                    q = self.guild_events.get(guild_id)
                    if q is None:
                        q = asyncio.Queue()
                        self.guild_events[guild_id] = q
                    await q.put(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[TTS] Lavalink websocket loop ended: %s", e)
        finally:
            self.ready.clear()
            self.session_id = None
            self.ws_task = None
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    pass
            self.aio_session = None

    def _require_session_id(self) -> str:
        if not self.session_id:
            raise RuntimeError("Lavalink 세션이 준비되지 않았습니다.")
        return self.session_id

    async def update_player(self, guild_id: int, payload: Dict[str, Any], *, no_replace: bool = False) -> Dict[str, Any]:
        await self._ensure_http()
        session_id = self._require_session_id()
        resp = await self.http.patch(
            f"{self.base_url}/v4/sessions/{session_id}/players/{guild_id}",
            params={"noReplace": str(no_replace).lower()},
            json=payload,
        )
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return {}

    async def destroy_player(self, guild_id: int) -> None:
        await self._ensure_http()
        session_id = self.session_id
        if not session_id:
            return
        try:
            resp = await self.http.delete(f"{self.base_url}/v4/sessions/{session_id}/players/{guild_id}")
            if resp.status_code not in (204, 404):
                resp.raise_for_status()
        except Exception as e:
            logger.debug("[TTS] Lavalink destroy player failed (guild_id=%s): %s", guild_id, e)

    async def load_track(self, identifier: str) -> str:
        await self._ensure_http()
        resp = await self.http.get(f"{self.base_url}/v4/loadtracks", params={"identifier": identifier})
        resp.raise_for_status()
        data = resp.json()
        load_type = data.get("loadType")
        payload = data.get("data")
        if load_type == "track" and isinstance(payload, dict) and payload.get("encoded"):
            return str(payload["encoded"])
        if load_type == "search" and isinstance(payload, list) and payload and isinstance(payload[0], dict) and payload[0].get("encoded"):
            return str(payload[0]["encoded"])
        if load_type == "playlist" and isinstance(payload, dict):
            tracks = payload.get("tracks") or []
            if tracks and isinstance(tracks[0], dict) and tracks[0].get("encoded"):
                return str(tracks[0]["encoded"])
        raise RuntimeError(f"Lavalink 트랙 로드 실패: {load_type}")

    async def prepare_voice(self, guild_id: int, voice: Dict[str, str]) -> None:
        evt = self.guild_connected.get(guild_id)
        if evt is None:
            evt = asyncio.Event()
            self.guild_connected[guild_id] = evt
        else:
            evt.clear()
        await self.update_player(guild_id, {"voice": voice})
        try:
            await asyncio.wait_for(evt.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.debug("[TTS] Lavalink voice connect wait timeout (guild_id=%s)", guild_id)

    async def stop(self, guild_id: int) -> None:
        await self.update_player(guild_id, {"track": {"encoded": None}})

    async def play_identifier(self, guild_id: int, identifier: str) -> None:
        encoded = await self.load_track(identifier)
        await self.update_player(guild_id, {"track": {"encoded": encoded}, "volume": 100, "paused": False}, no_replace=False)

    async def wait_for_terminal_event(self, guild_id: int, timeout: float) -> None:
        q = self.guild_events.get(guild_id)
        if q is None:
            q = asyncio.Queue()
            self.guild_events[guild_id] = q
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remain = deadline - asyncio.get_running_loop().time()
            if remain <= 0:
                raise asyncio.TimeoutError
            event = await asyncio.wait_for(q.get(), timeout=remain)
            typ = str(event.get("type") or "")
            if typ == "TrackStartEvent":
                continue
            if typ == "TrackEndEvent":
                reason = str(event.get("reason") or "")
                if reason in {"finished", "loadFailed", "stopped", "cleanup", "replaced"}:
                    return
            if typ == "TrackExceptionEvent":
                ex = event.get("exception") or {}
                raise RuntimeError(str(ex.get("message") or "트랙 예외"))
            if typ == "TrackStuckEvent":
                raise RuntimeError("트랙 재생이 멈췄습니다.")
            if typ == "WebSocketClosedEvent":
                code = event.get("code")
                raise RuntimeError(f"음성 연결이 종료되었습니다. ({code})")


_LAVALINK = _LavalinkNode()


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


class LavalinkVoiceProtocol(discord.VoiceProtocol):
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable) -> None:
        super().__init__(client, channel)
        self.client = client
        self.channel = channel
        self.guild = channel.guild
        self.guild_id = self.guild.id
        self.session_id: Optional[str] = None
        self.token: Optional[str] = None
        self.endpoint: Optional[str] = None
        self._voice_ready = asyncio.Event()
        self._is_connected = False
        self._is_playing = False

    async def connect(self, *, timeout: float, reconnect: bool) -> None:
        user = getattr(self.client, "user", None)
        if user is None or not getattr(user, "id", None):
            raise RuntimeError("봇 사용자 정보를 가져올 수 없습니다.")
        await _LAVALINK.ensure_started(int(user.id))
        self._voice_ready.clear()
        await self.guild.change_voice_state(channel=self.channel, self_mute=False, self_deaf=True)
        await asyncio.wait_for(self._voice_ready.wait(), timeout=timeout)
        await _LAVALINK.prepare_voice(
            self.guild_id,
            {
                "token": str(self.token or ""),
                "endpoint": str(self.endpoint or ""),
                "sessionId": str(self.session_id or ""),
                "channelId": str(self.channel.id),
            },
        )
        self._is_connected = True

    async def disconnect(self, *, force: bool = False) -> None:
        self._is_connected = False
        self._is_playing = False
        await _LAVALINK.destroy_player(self.guild_id)
        try:
            await self.guild.change_voice_state(channel=None, self_mute=False, self_deaf=False)
        except Exception:
            pass
        self.cleanup()

    async def on_voice_server_update(self, data: dict) -> None:
        self.token = data.get("token")
        self.endpoint = data.get("endpoint")
        if self.session_id and self.token and self.endpoint:
            self._voice_ready.set()

    async def on_voice_state_update(self, data: dict) -> None:
        self.session_id = data.get("session_id")
        channel_id = _to_int(data.get("channel_id"))
        if channel_id is None:
            self._is_connected = False
            return
        if channel_id != getattr(self.channel, "id", None):
            ch = self.guild.get_channel(channel_id)
            if ch is not None:
                self.channel = ch
        if self.session_id and self.token and self.endpoint:
            self._voice_ready.set()

    def cleanup(self) -> None:
        self._is_connected = False
        try:
            super().cleanup()
        except Exception:
            pass

    def is_connected(self) -> bool:
        return self._is_connected

    def is_playing(self) -> bool:
        return self._is_playing

    def stop(self) -> None:
        self._is_playing = False
        asyncio.create_task(_LAVALINK.stop(self.guild_id))

    async def move_to(self, channel: discord.abc.Connectable) -> None:
        self.channel = channel
        self._voice_ready.clear()
        await self.guild.change_voice_state(channel=channel, self_mute=False, self_deaf=True)
        await asyncio.wait_for(self._voice_ready.wait(), timeout=VOICE_MOVE_TIMEOUT)
        await _LAVALINK.prepare_voice(
            self.guild_id,
            {
                "token": str(self.token or ""),
                "endpoint": str(self.endpoint or ""),
                "sessionId": str(self.session_id or ""),
                "channelId": str(channel.id),
            },
        )
        self._is_connected = True


async def create_guild_custom_audio(guild_id: int, key: str) -> Tuple[Optional[bytes], Optional[str]]:
    idx = _load_guild_index(guild_id)
    meta = idx.get(key)
    if not meta:
        return None, "길드 커스텀 사운드를 찾을 수 없습니다."
    filename = str(meta.get("file", ""))
    if not filename:
        return None, "길드 커스텀 사운드 파일 정보가 없습니다."
    base_dir = os.path.realpath(guild_dir_path(guild_id))
    base_prefix = base_dir + os.sep
    path = os.path.realpath(os.path.join(base_dir, filename))
    if not path.startswith(base_prefix) or not os.path.exists(path):
        return None, f"오디오 파일을 찾을 수 없습니다: {os.path.basename(filename)}"
    return await _pcm_from_file_cached(path, volume=1.0)


async def _build_play_item_for_text(message: discord.Message, clean_text: str) -> Tuple[Optional[PlayItem], Optional[str]]:
    engine_id = "engine1"
    if tts_engine_manager is not None:
        try:
            engine_id = tts_engine_manager.get_engine(message.author.id)
        except Exception as e:
            logger.debug("[TTS] tts_engine_manager.get_engine failed: %s", e)
    if _voice_backend() == "lavalink":
        tiktok_voice = TIKTOK_VOICE_MAP.get(engine_id)
        if tiktok_voice:
            mp3_bytes, err = await _tiktok_tts_mp3(clean_text or "", tiktok_voice)
        else:
            voice_name = EDGE_VOICE_MAP.get(engine_id)
            if voice_name:
                mp3_bytes, err = await _edge_tts_mp3(clean_text or "", voice_name)
            else:
                mp3_bytes, err = await _naver_tts_mp3(clean_text or "")
        if not mp3_bytes:
            return None, err
        url = await _register_served_bytes(mp3_bytes, suffix=".mp3")
        return PlayItem(mode="url", payload=url, title="tts"), None
    tiktok_voice = TIKTOK_VOICE_MAP.get(engine_id)
    if tiktok_voice:
        audio, err = await create_tiktok_tts_audio(clean_text or "", tiktok_voice)
    else:
        voice_name = EDGE_VOICE_MAP.get(engine_id)
        if voice_name:
            audio, err = await create_edge_tts_audio(clean_text or "", voice_name)
        else:
            audio, err = await create_naver_tts_audio(clean_text or "")
    if not audio:
        return None, err
    return PlayItem(mode="pcm", payload=audio, title="tts"), None


async def _build_play_item_for_global_custom(sound_key: str) -> Tuple[Optional[PlayItem], Optional[str]]:
    path, _ = get_audio_file_path(sound_key)
    if not path:
        return None, "지원하지 않는 오디오 타입입니다."
    if _voice_backend() == "lavalink":
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None, f"오디오 파일을 찾을 수 없습니다: {os.path.basename(path)}"
        except Exception as e:
            return None, f"오디오 파일 확인 중 오류: {e}"
        if st.st_size > 10 * 1024 * 1024:
            return None, "오디오 파일이 너무 큽니다. (최대 10MB)"
        url = await _register_served_file(path)
        return PlayItem(mode="url", payload=url, title=sound_key), None
    audio, err = await create_custom_audio(sound_key)
    if not audio:
        return None, err
    return PlayItem(mode="pcm", payload=audio, title=sound_key), None


async def _build_play_item_for_guild_custom(guild_id: int, key: str) -> Tuple[Optional[PlayItem], Optional[str]]:
    idx = _load_guild_index(guild_id)
    meta = idx.get(key)
    if not meta:
        return None, "길드 커스텀 사운드를 찾을 수 없습니다."
    filename = str(meta.get("file", ""))
    base_dir = os.path.realpath(guild_dir_path(guild_id))
    base_prefix = base_dir + os.sep
    path = os.path.realpath(os.path.join(base_dir, filename))
    if not path.startswith(base_prefix) or not os.path.exists(path):
        return None, f"오디오 파일을 찾을 수 없습니다: {os.path.basename(filename)}"
    if _voice_backend() == "lavalink":
        url = await _register_served_file(path)
        return PlayItem(mode="url", payload=url, title=key), None
    audio, err = await create_guild_custom_audio(guild_id, key)
    if not audio:
        return None, err
    return PlayItem(mode="pcm", payload=audio, title=key), None


def _get_voice_connect_lock(guild_id: int) -> asyncio.Lock:
    lock = voice_connect_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        voice_connect_locks[guild_id] = lock
    return lock


def get_guild_queue(guild_id: int) -> asyncio.Queue:
    q = tts_queues.get(guild_id)
    if q is None:
        q = asyncio.Queue()
        tts_queues[guild_id] = q
    return q


def _get_gen_sem(guild_id: int) -> asyncio.Semaphore:
    sem = tts_gen_sems.get(guild_id)
    if sem is None:
        sem = asyncio.Semaphore(1)
        tts_gen_sems[guild_id] = sem
    return sem


def _track_guild_task(guild_id: int, task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    s = guild_active_tasks.get(guild_id)
    if s is None:
        s = set()
        guild_active_tasks[guild_id] = s
    s.add(task)


def _untrack_guild_task(guild_id: int, task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    s = guild_active_tasks.get(guild_id)
    if not s:
        return
    s.discard(task)
    if not s:
        guild_active_tasks.pop(guild_id, None)


def _is_voice_client_alive(vc: Optional[Any]) -> bool:
    if vc is None:
        return False
    try:
        if hasattr(vc, "is_connected") and not vc.is_connected():
            return False
        if getattr(vc, "channel", None) is None:
            return False
        if getattr(vc, "guild", None) is None:
            return False
        return True
    except Exception:
        return False


def _is_expected_voice_client_backend(vc: Optional[Any]) -> bool:
    if vc is None:
        return False
    if _voice_backend() == "lavalink":
        return isinstance(vc, LavalinkVoiceProtocol)
    return isinstance(vc, discord.VoiceClient)


def _get_best_voice_client(guild: Optional[discord.Guild], guild_id: int) -> Optional[Any]:
    vc = voice_clients.get(guild_id)
    if _is_expected_voice_client_backend(vc) and _is_voice_client_alive(vc):
        return vc
    voice_clients.pop(guild_id, None)
    if guild is None:
        return None
    try:
        gvc = getattr(guild, "voice_client", None)
    except Exception:
        return None
    if not _is_expected_voice_client_backend(gvc):
        return None
    if _is_voice_client_alive(gvc):
        voice_clients[guild_id] = gvc
        return gvc
    return None


def _drain_queue(q: asyncio.Queue) -> int:
    n = 0
    while True:
        try:
            q.get_nowait()
            n += 1
        except asyncio.QueueEmpty:
            break
        except Exception:
            break
    return n


async def _safe_force_disconnect(vc: Optional[Any], guild_id: int, reason: str) -> None:
    if vc is None:
        return
    try:
        if hasattr(vc, "is_playing") and vc.is_playing():
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
    guild: Optional[discord.Guild],
    guild_id: int,
    target_channel: Optional[discord.VoiceChannel] = None,
    *,
    try_move: bool = False,
) -> Optional[Any]:
    if guild is None:
        return None
    raw_vc = getattr(guild, "voice_client", None)
    if raw_vc is None or not _is_expected_voice_client_backend(raw_vc):
        return None
    if not _is_voice_client_alive(raw_vc):
        await _safe_force_disconnect(raw_vc, guild_id, reason="recover_not_alive")
        voice_clients.pop(guild_id, None)
        return None
    if target_channel is not None:
        try:
            current_channel = getattr(raw_vc, "channel", None)
            if current_channel is not None and current_channel.id != target_channel.id and try_move:
                await asyncio.wait_for(raw_vc.move_to(target_channel), timeout=VOICE_MOVE_TIMEOUT)
        except Exception as e:
            logger.warning("[TTS] Voice recover move failed (guild_id=%s): %s", guild_id, e)
            await _safe_force_disconnect(raw_vc, guild_id, reason="recover_move_failed")
            voice_clients.pop(guild_id, None)
            return None
    voice_clients[guild_id] = raw_vc
    return raw_vc


async def join_voice_channel(channel: discord.VoiceChannel, guild_id: int) -> Tuple[Optional[Any], Optional[discord.Embed]]:
    _ensure_asyncio_exception_filter()
    lock = _get_voice_connect_lock(guild_id)
    async with lock:
        guild = channel.guild
        current_vc = _get_best_voice_client(guild, guild_id)
        if current_vc is not None:
            try:
                if getattr(current_vc, "channel", None) is not None and current_vc.channel.id == channel.id:
                    voice_clients[guild_id] = current_vc
                    return current_vc, None
            except Exception as e:
                logger.warning("[TTS] VoiceClient 상태 확인 실패 (guild_id=%s): %s", guild_id, e)
                voice_clients.pop(guild_id, None)
                current_vc = None

        if current_vc is not None:
            try:
                await asyncio.wait_for(current_vc.move_to(channel), timeout=VOICE_MOVE_TIMEOUT)
                voice_clients[guild_id] = current_vc
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
            voice_clients.pop(guild_id, None)
            await asyncio.sleep(0.5)

        me = getattr(guild, "me", None)
        if me is not None:
            perms = channel.permissions_for(me)
            if not (perms.connect and perms.speak):
                return None, None

        if _voice_backend() == "pycord":
            recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
            if recovered_vc is not None:
                return recovered_vc, None
            try:
                new_vc = await asyncio.wait_for(channel.connect(), timeout=VOICE_CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[TTS] Voice connect timed out (guild_id=%s)", guild_id)
                await asyncio.sleep(0.4)
                recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                if recovered_vc is not None:
                    return recovered_vc, None
                return None, None
            except discord.ClientException as e:
                logger.warning("[TTS] Voice connect failed (guild_id=%s): %s", guild_id, e)
                if "already connected" in str(e).lower():
                    recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                    if recovered_vc is not None:
                        return recovered_vc, None
                return None, None
            except Exception as e:
                logger.exception("[TTS] Voice connect error (guild_id=%s): %s", guild_id, e)
                recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                if recovered_vc is not None:
                    return recovered_vc, None
                return None, None
            voice_clients[guild_id] = new_vc
            embed = discord.Embed(
                title="🎤 Mococo TTS",
                description=f"{channel.mention} 음성 채널에 참가했어요!\n이 채널에서 보내는 메시지를 읽어드릴게요!",
                color=0x2ECC71,
            )
            embed.set_thumbnail(url="https://i.namu.wiki/i/u253q5zv58zJ1twYkeea-czVz8SQsvX-a1jVZ8oYsTVDH_TRC8-bpcVa4aKYQs5lI55B9srLYF9JJFUPbkI8MA.webp")
            return new_vc, embed

        recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
        if recovered_vc is not None:
            return recovered_vc, None
        try:
            new_vc = await asyncio.wait_for(channel.connect(cls=LavalinkVoiceProtocol), timeout=VOICE_CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("[TTS] Lavalink voice connect timed out (guild_id=%s)", guild_id)
            await asyncio.sleep(0.4)
            recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
            if recovered_vc is not None:
                return recovered_vc, None
            return None, None
        except discord.ClientException as e:
            logger.warning("[TTS] Lavalink voice connect failed (guild_id=%s): %s", guild_id, e)
            if "already connected" in str(e).lower():
                recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                if recovered_vc is not None:
                    return recovered_vc, None
                existing_vc = getattr(guild, "voice_client", None)
                if _is_expected_voice_client_backend(existing_vc):
                    await _safe_force_disconnect(existing_vc, guild_id, reason="lavalink_already_connected")
                    await asyncio.sleep(0.25)
                    recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
                    if recovered_vc is not None:
                        return recovered_vc, None
            return None, None
        except Exception as e:
            logger.exception("[TTS] Lavalink voice connect error (guild_id=%s): %s", guild_id, e)
            recovered_vc = await _recover_connected_voice_client(guild, guild_id, channel, try_move=True)
            if recovered_vc is not None:
                return recovered_vc, None
            return None, None
        voice_clients[guild_id] = new_vc
        embed = discord.Embed(
            title="🎤 Mococo TTS",
            description=f"{channel.mention} 음성 채널에 참가했어요!\n이 채널에서 보내는 메시지를 읽어드릴게요!",
            color=0x2ECC71,
        )
        embed.set_thumbnail(url="https://i.namu.wiki/i/u253q5zv58zJ1twYkeea-czVz8SQsvX-a1jVZ8oYsTVDH_TRC8-bpcVa4aKYQs5lI55B9srLYF9JJFUPbkI8MA.webp")
        return new_vc, embed


def start_playback_task(guild_id: int, vc: Optional[Any]) -> None:
    if not _is_voice_client_alive(vc):
        return
    task = playback_tasks.get(guild_id)
    if task is None or task.done():
        t = asyncio.create_task(playback_worker(guild_id, vc))
        playback_tasks[guild_id] = t
        _track_guild_task(guild_id, t)


async def playback_worker(guild_id: int, vc: Optional[Any]) -> None:
    task = asyncio.current_task()
    _track_guild_task(guild_id, task)
    q = get_guild_queue(guild_id)
    loop = asyncio.get_running_loop()
    try:
        while True:
            latest_vc = voice_clients.get(guild_id) or vc
            vc = latest_vc
            if not _is_voice_client_alive(vc):
                break
            item = await q.get()
            if item is None:
                break
            if _voice_backend() == "lavalink":
                try:
                    if not isinstance(item, PlayItem) or item.mode != "url":
                        continue
                    if not _is_voice_client_alive(vc):
                        break
                    if hasattr(vc, "_is_playing"):
                        vc._is_playing = True
                    await _LAVALINK.play_identifier(guild_id, str(item.payload))
                    await _LAVALINK.wait_for_terminal_event(guild_id, PLAYBACK_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning("[TTS] Lavalink playback timed out (guild_id=%s, timeout=%s)", guild_id, PLAYBACK_TIMEOUT)
                    await disconnect_from_guild(guild_id, guild=getattr(vc, "guild", None), reason="playback_timeout")
                    break
                except asyncio.CancelledError:
                    try:
                        await _LAVALINK.stop(guild_id)
                    except Exception:
                        pass
                    raise
                except Exception as e:
                    logger.warning("[TTS] Lavalink playback error (guild_id=%s): %s", guild_id, e)
                finally:
                    if hasattr(vc, "_is_playing"):
                        vc._is_playing = False
                continue
            done_evt = asyncio.Event()

            def _after(err: Optional[Exception]) -> None:
                if err:
                    logger.warning("[TTS] Playback error (guild_id=%s): %s", guild_id, err)
                loop.call_soon_threadsafe(done_evt.set)

            try:
                if not isinstance(item, PlayItem) or item.mode != "pcm":
                    continue
                if not _is_voice_client_alive(vc):
                    break
                if vc.is_playing():
                    await q.put(item)
                    await asyncio.sleep(0.2)
                    continue
                vc.play(TTSAudioSource(item.payload if isinstance(item.payload, bytes) else b""), after=_after)
            except discord.ClientException as e:
                logger.warning("[TTS] Playback client error (guild_id=%s): %s", guild_id, e)
                try:
                    await q.put(item)
                except Exception:
                    pass
                await asyncio.sleep(0.2)
                continue
            except Exception:
                logger.exception("[TTS] Unexpected playback error (guild_id=%s)", guild_id)
                continue
            try:
                await asyncio.wait_for(done_evt.wait(), timeout=PLAYBACK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[TTS] Playback timed out (guild_id=%s, timeout=%s)", guild_id, PLAYBACK_TIMEOUT)
                try:
                    if vc and vc.is_playing():
                        vc.stop()
                except Exception as e:
                    logger.warning("[TTS] vc.stop failed on timeout (guild_id=%s): %s", guild_id, e)
                await disconnect_from_guild(guild_id, guild=getattr(vc, "guild", None), reason="playback_timeout")
                break
            except asyncio.CancelledError:
                try:
                    if vc and vc.is_playing():
                        vc.stop()
                except Exception:
                    pass
                raise
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("[TTS] playback_worker error (guild_id=%s)", guild_id)
    finally:
        _untrack_guild_task(guild_id, task)
        cur = playback_tasks.get(guild_id)
        if cur is task:
            playback_tasks.pop(guild_id, None)


async def send_error_embed(channel: discord.TextChannel, title: str, description: str) -> None:
    try:
        await channel.send(embed=discord.Embed(title=f"❌ {title}", description=description, color=0xFF6B6B), delete_after=10)
    except Exception as e:
        logger.debug("[TTS] send_error_embed failed: %s", e)


async def send_warning_embed(channel: discord.TextChannel, title: str, description: str) -> None:
    try:
        await channel.send(embed=discord.Embed(title=f"⚠️ {title}", description=description, color=0xFFA726), delete_after=8)
    except Exception as e:
        logger.debug("[TTS] send_warning_embed failed: %s", e)


async def handle_tts_message(message: discord.Message) -> None:
    _ensure_asyncio_exception_filter()
    task = asyncio.current_task()
    guild = message.guild
    gid: Optional[int] = getattr(guild, "id", None)
    if gid is not None:
        _track_guild_task(gid, task)
    try:
        if guild is None:
            return
        gid = guild.id
        current_vc = _get_best_voice_client(guild, gid)
        if current_vc is not None:
            voice_clients[gid] = current_vc
        if not tts_channel_manager.is_tts_channel(gid, message.channel.id):
            return
        author_voice = message.author.voice
        if not author_voice or not author_voice.channel:
            return
        user_channel: discord.VoiceChannel = author_voice.channel
        guild_custom_key = check_guild_custom_audio_trigger(gid, message.content or "")
        global_custom_key = None if guild_custom_key else check_custom_audio_trigger(message.content or "")
        clean_text: Optional[str] = None
        if not guild_custom_key and not global_custom_key:
            clean_text = clean_message_for_tts(message.content or "")
            if not clean_text:
                return
        join_embed: Optional[discord.Embed] = None
        lock = tts_locks.get(gid)
        if lock is None:
            lock = asyncio.Lock()
            tts_locks[gid] = lock
        async with lock:
            vc: Optional[Any] = _get_best_voice_client(guild, gid)
            if vc and getattr(vc, "channel", None) is not None and vc.channel.id != user_channel.id:
                await send_warning_embed(message.channel, "TTS 사용 불가", f"현재 봇이 {vc.channel.mention}에서 사용 중이에요.")
                return
            if (not vc) or (getattr(vc, "channel", None) and vc.channel.id != user_channel.id):
                vc, join_embed = await join_voice_channel(user_channel, gid)
            if not _is_voice_client_alive(vc):
                voice_clients.pop(gid, None)
                return
            voice_clients[gid] = vc
            q = get_guild_queue(gid)
            if q.qsize() >= MAX_QUEUE_SIZE:
                await send_warning_embed(message.channel, "TTS 대기열 포화", f"현재 TTS 대기열이 가득 찼어요. (최대 {MAX_QUEUE_SIZE}개)")
                return
        item: Optional[PlayItem] = None
        err: Optional[str] = None
        sem = _get_gen_sem(gid)
        async with sem:
            if guild_custom_key:
                item, err = await _build_play_item_for_guild_custom(gid, guild_custom_key)
            elif global_custom_key:
                item, err = await _build_play_item_for_global_custom(global_custom_key)
            else:
                item, err = await _build_play_item_for_text(message, clean_text or "")
        if not item:
            if err and ("읽을 수 있는 텍스트" not in err):
                await send_error_embed(message.channel, "TTS 생성 실패", err or "알 수 없는 오류가 발생했어요. 관리자에게 문의해주세요.")
            return
        async with lock:
            q = get_guild_queue(gid)
            if q.qsize() >= MAX_QUEUE_SIZE:
                await send_warning_embed(message.channel, "TTS 대기열 포화", f"현재 TTS 대기열이 가득 찼어요. (최대 {MAX_QUEUE_SIZE}개)")
                return
            await q.put(item)
            if join_embed is not None:
                try:
                    await message.channel.send(embed=join_embed)
                except Exception as e:
                    logger.debug("[TTS] failed to send join embed: %s", e)
            if q.qsize() > 3:
                await send_warning_embed(message.channel, "TTS 대기 중", f"현재 {q.qsize()}개의 메시지가 대기 중이에요.")
            start_playback_task(gid, voice_clients.get(gid))
    except Exception:
        logger.exception("[TTS] handle_tts_message error (guild_id=%s)", getattr(getattr(message, "guild", None), "id", None))
    finally:
        if gid is not None:
            _untrack_guild_task(gid, task)


async def handle_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    _ensure_asyncio_exception_filter()
    try:
        guild = member.guild
        if guild is None:
            return
        gid = guild.id
        current_vc = _get_best_voice_client(guild, gid)
        if current_vc is not None:
            voice_clients[gid] = current_vc
        else:
            voice_clients.pop(gid, None)
        me = getattr(guild, "me", None)
        if me and member.id == me.id:
            if after.channel is None:
                await disconnect_from_guild(gid, guild=guild, reason="bot_left_voice")
                tts_channel_manager.clear_override(gid)
            else:
                vc = _get_best_voice_client(guild, gid)
                if vc is not None:
                    voice_clients[gid] = vc
            return
        vc = _get_best_voice_client(guild, gid)
        if vc is None:
            return
        bot_channel = getattr(vc, "channel", None)
        if bot_channel is None:
            return
        try:
            real_users = [m for m in bot_channel.members if not m.bot]
        except Exception:
            real_users = []
        if len(real_users) == 0:
            await disconnect_from_guild(gid, guild=guild, reason="no_real_users")
            tts_channel_manager.clear_override(gid)
    except Exception:
        logger.exception("[TTS] handle_voice_state_update error (guild_id=%s)", getattr(getattr(member, "guild", None), "id", None))


async def cleanup_guild_state(guild_id: int) -> None:
    await disconnect_from_guild(guild_id, guild=None, reason="cleanup_guild_state")


async def force_reset_guild_tts(guild: Optional[discord.Guild] = None, guild_id: Optional[int] = None, reason: str = "user_reset") -> bool:
    gid = guild_id or (guild.id if guild else None)
    if gid is None:
        return False
    await disconnect_from_guild(gid, guild=guild, reason=f"force_reset:{reason}", clear_override=True, full_reset=True)
    return True


async def disconnect_from_guild(
    guild_id: int,
    guild: Optional[discord.Guild] = None,
    reason: str = "disconnect",
    clear_override: bool = False,
    full_reset: bool = True,
) -> None:
    current_task = asyncio.current_task()
    vc = _get_best_voice_client(guild, guild_id)
    tasks_to_cancel: List[asyncio.Task] = []
    task = playback_tasks.pop(guild_id, None)
    if task is not None and task is not current_task and not task.done():
        tasks_to_cancel.append(task)
    active = guild_active_tasks.get(guild_id)
    if active:
        for t in list(active):
            if t is current_task or t.done():
                continue
            tasks_to_cancel.append(t)
    for t in tasks_to_cancel:
        try:
            t.cancel()
        except Exception:
            pass
    if tasks_to_cancel:
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks_to_cancel, return_exceptions=True), timeout=TASK_CANCEL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("[TTS] Task cancel timed out (guild_id=%s, reason=%s, tasks=%s)", guild_id, reason, len(tasks_to_cancel))
        else:
            for r in results:
                if r is None:
                    continue
                if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                    logger.warning("[TTS] Task ended with error (guild_id=%s, reason=%s): %s", guild_id, reason, r)
    if vc is not None:
        try:
            if hasattr(vc, "is_playing") and vc.is_playing():
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
    if _voice_backend() == "lavalink":
        try:
            await _LAVALINK.destroy_player(guild_id)
        except Exception:
            pass
    voice_clients.pop(guild_id, None)
    q = tts_queues.pop(guild_id, None)
    if q is not None:
        drained = _drain_queue(q)
        if drained:
            logger.debug("[TTS] queue drained (guild_id=%s, drained=%s, reason=%s)", guild_id, drained, reason)
    if full_reset:
        tts_locks.pop(guild_id, None)
        voice_connect_locks.pop(guild_id, None)
        tts_gen_sems.pop(guild_id, None)
        guild_active_tasks.pop(guild_id, None)
    if clear_override:
        try:
            tts_channel_manager.clear_override(guild_id)
        except Exception as e:
            logger.debug("[TTS] clear_override failed (guild_id=%s): %s", guild_id, e)


async def cleanup_all_connections() -> None:
    _ensure_asyncio_exception_filter()
    try:
        for gid in list(voice_clients.keys()):
            await disconnect_from_guild(gid, guild=None, reason="cleanup_all_connections", clear_override=False, full_reset=True)
        try:
            tts_channel_manager.clear_all_overrides()
        except Exception as e:
            logger.debug("[TTS] clear_all_overrides failed: %s", e)
        if _http_cleanup_task is not None and not _http_cleanup_task.done():
            _http_cleanup_task.cancel()
        if _http_runner is not None:
            try:
                await _http_runner.cleanup()
            except Exception:
                pass
        if _LAVALINK.ws_task is not None and not _LAVALINK.ws_task.done():
            _LAVALINK.ws_task.cancel()
            with contextlib.suppress(Exception):
                await _LAVALINK.ws_task
        _LAVALINK.ready.clear()
        _LAVALINK.session_id = None
        _LAVALINK.ws_task = None
        try:
            await _LAVALINK.http.aclose()
        except Exception:
            pass
        logger.info("[TTS] All voice connections cleared.")
    except Exception:
        logger.exception("[TTS] cleanup_all_connections error")
