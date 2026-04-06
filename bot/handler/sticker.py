import discord
import os
import re
import unicodedata
from typing import Optional, Dict, List, Set, Any
import asyncio
from functools import lru_cache
from datetime import datetime
from io import BytesIO
from PIL import Image

from core.config import (
    STICKER_WEBHOOK_NAME,
    STICKER_USERNAME_SUFFIX,
    NO_RESIZE_KEYS
)

NO_RESIZE_KEYS_NORM = {unicodedata.normalize("NFKC", k).lower().replace(" ", "") for k in NO_RESIZE_KEYS}

class StickerHandler:
    PREFERRED_EXT = [".webp", ".png", ".gif", ".jpg", ".jpeg"]
    SUPPORTED_EXTS: Set[str] = set(PREFERRED_EXT)
    MAX_FILE_SIZE = 25 * 1024 * 1024
    BRACKET_RX = re.compile(r"\[([^\]]{1,64})\]")

    def __init__(self):
        self.sticker_base_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "utils",
            "stickers",
        )
        self._sticker_cache: Dict[str, str] = {}
        self._key_map: Dict[str, str] = {}
        self._initialized = False
        self._loading_lock = asyncio.Lock()

    async def initialize(self):
        async with self._loading_lock:
            if self._initialized:
                return
            await self._load_stickers()
            self._initialized = True
            print(f"[Sticker] {len(self._sticker_cache)}개 스티커 로드 완료 @ {datetime.now().strftime('%H:%M:%S')}")

    async def reload_stickers(self):
        async with self._loading_lock:
            self._sticker_cache.clear()
            self._key_map.clear()
            self._find_sticker_key_cached.cache_clear()
            self._initialized = False
            await self.initialize()

    def find_sticker_key(self, text: str) -> Optional[str]:
        if not self._initialized or not text:
            return None
        return self._find_sticker_key_cached(text)

    def get_sticker_path(self, key: str) -> Optional[str]:
        return self._sticker_cache.get(key)

    def get_available_stickers(self) -> List[Dict[str, str]]:
        return [
            {"key": key, "file": os.path.basename(path), "path": path}
            for key, path in self._sticker_cache.items()
        ]

    async def _load_stickers(self):
        try:
            base = self.sticker_base_path
            if not os.path.exists(base):
                print(f"[Sticker] 스티커 폴더가 없습니다: {base}")
                return
            for entry in os.scandir(base):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in self.SUPPORTED_EXTS:
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                if size > self.MAX_FILE_SIZE:
                    print(f"[Sticker] 파일이 너무 큼(>25MB): {entry.name}")
                    continue
                key = self._extract_sticker_key(entry.name)
                if not key:
                    continue
                chosen = self._sticker_cache.get(key)
                if chosen:
                    if self._prefer(entry.path, chosen):
                        self._sticker_cache[key] = entry.path
                else:
                    self._sticker_cache[key] = entry.path
                normalized_key = self._normalize_key(key)
                self._key_map[normalized_key] = key
        except Exception as e:
            print(f"[Sticker] 스티커 로드 오류: {e}")

    @staticmethod
    def _extract_sticker_key(filename: str) -> Optional[str]:
        name, _ = os.path.splitext(filename)
        name = name.strip()
        if len(name) == 0:
            return None
        if name.startswith("[") and name.endswith("]") and len(name) >= 2:
            name = name[1:-1].strip()
        return name or None

    @staticmethod
    def _normalize_key(s: str) -> str:
        s = unicodedata.normalize("NFKC", s)
        return s.lower().replace(" ", "")

    def _prefer(self, path_new: str, path_old: str) -> bool:
        ext_new = os.path.splitext(path_new)[1].lower()
        ext_old = os.path.splitext(path_old)[1].lower()
        if ext_new != ext_old:
            rank_new = self.PREFERRED_EXT.index(ext_new) if ext_new in self.PREFERRED_EXT else 999
            rank_old = self.PREFERRED_EXT.index(ext_old) if ext_old in self.PREFERRED_EXT else 999
            if rank_new != rank_old:
                return rank_new < rank_old
        try:
            return os.path.getsize(path_new) < os.path.getsize(path_old)
        except OSError:
            return False

    @lru_cache(maxsize=1000)
    def _find_sticker_key_cached(self, text: str) -> Optional[str]:
        for match in self.BRACKET_RX.finditer(text):
            keyword = match.group(1)
            normalized_keyword = self._normalize_key(keyword)
            if normalized_keyword in self._key_map:
                return self._key_map[normalized_keyword]
        return None


sticker_handler = StickerHandler()

_WEBHOOK_NAME = STICKER_WEBHOOK_NAME
_webhook_cache: Dict[int, discord.Webhook] = {}
_webhook_locks: Dict[int, asyncio.Lock] = {}

def _get_lock(cid: int) -> asyncio.Lock:
    lock = _webhook_locks.get(cid)
    if not lock:
        lock = asyncio.Lock()
        _webhook_locks[cid] = lock
    return lock

async def _get_or_create_webhook(message: discord.Message) -> Optional[discord.Webhook]:
    if not message.guild:
        return None
    parent_channel = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
    if not hasattr(parent_channel, "create_webhook"):
        return None
    cid = parent_channel.id
    if cid in _webhook_cache:
        return _webhook_cache[cid]
    lock = _get_lock(cid)
    async with lock:
        if cid in _webhook_cache:
            return _webhook_cache[cid]
        try:
            hooks = await parent_channel.webhooks()
            for h in hooks:
                if h.name == _WEBHOOK_NAME:
                    _webhook_cache[cid] = h
                    return h
            new_hook = await parent_channel.create_webhook(name=_WEBHOOK_NAME)
            _webhook_cache[cid] = new_hook
            return new_hook
        except Exception as e:
            print(f"[Sticker] 웹훅 준비 실패: {e}")
            return None

def _make_file_and_embed(author: discord.Member, sticker_key: str, sticker_path: str, avatar_url: Optional[str]):
    if unicodedata.normalize("NFKC", sticker_key).lower().replace(" ", "") in NO_RESIZE_KEYS_NORM:
        with open(sticker_path, "rb") as f:
            buf = BytesIO(f.read())
        buf.seek(0)
        return discord.File(buf, filename=os.path.basename(sticker_path))

    with Image.open(sticker_path) as im:
        im = im.convert("RGBA")
        im = im.resize((150, 150), Image.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)

    return discord.File(buf, filename="sticker.png")

async def _send_sticker_via_webhook(message: discord.Message, sticker_path: str, sticker_key: str) -> bool:
    hook = await _get_or_create_webhook(message)
    if not hook:
        return False
    username = f"{message.author.display_name}{STICKER_USERNAME_SUFFIX}" if STICKER_USERNAME_SUFFIX else message.author.display_name
    try:
        avatar_url = str(message.author.display_avatar.url)
    except Exception:
        avatar_url = None
    kwargs = {}
    if isinstance(message.channel, discord.Thread):
        kwargs["thread"] = message.channel
    try:
        file_obj = await asyncio.to_thread(
            _make_file_and_embed,
            message.author,
            sticker_key,
            sticker_path,
            avatar_url,
        )
        await hook.send(
            username=username,
            avatar_url=avatar_url,
            files=[file_obj],
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
            **kwargs,
        )
        return True
    except discord.HTTPException as e:
        print(f"[Sticker] 웹훅 전송 실패: {e}")
        return False
    except Exception as e:
        print(f"[Sticker] 웹훅 전송 예외: {e}")
        return False


async def handle_sticker_message(message: discord.Message) -> bool:
    try:
        if not sticker_handler._initialized:
            await sticker_handler.initialize()
        if isinstance(message.channel, discord.DMChannel):
            return False
        sticker_key = sticker_handler.find_sticker_key(message.content or "")
        if not sticker_key:
            return False
        sticker_path = sticker_handler.get_sticker_path(sticker_key)
        if not sticker_path or not os.path.exists(sticker_path):
            return False
        try:
            if os.path.getsize(sticker_path) > StickerHandler.MAX_FILE_SIZE:
                print(f"[Sticker] 파일이 너무 큼(송신 취소): {os.path.basename(sticker_path)}")
                return False
        except OSError:
            return False
        ok = await _send_sticker_via_webhook(message, sticker_path, sticker_key)
        if ok:
            return True
        return False
    except Exception as e:
        print(f"[Sticker] 스티커 처리 오류: {e}")
        return False

async def initialize_sticker():
    await sticker_handler.initialize()

def get_sticker_stats() -> Dict[str, Any]:
    info = sticker_handler._find_sticker_key_cached.cache_info()
    info_dict = {"hits": info.hits, "misses": info.misses, "maxsize": info.maxsize, "currsize": info.currsize}
    return {
        "total_stickers": len(sticker_handler._sticker_cache),
        "total_keys": len(sticker_handler._key_map),
        "cache_info": info_dict,
        "initialized": sticker_handler._initialized,
        "base_path": sticker_handler.sticker_base_path,
    }
