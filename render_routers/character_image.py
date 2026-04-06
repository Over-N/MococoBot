from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from io import BytesIO
from typing import Optional
from PIL import Image
import asyncio
import anyio
import os
import time

from services.discord_service import discord_service
from render.character_renderer.rendering import render_character_card as _render_character_card
from render.mini_card_renderer import render_new_card as _render_new_card
from render.render_exec import run_render as _run_render
from render_routers.config import LOSTARK_API_PROFILE_KEY, LOSTARK_API_SIBLINGS_KEY, SPECIAL_NICKNAME, MOKOKO_NICKNAME
from utils.http_client import get_http_client
from render_routers.image_utils import decode_image

render_character_card = _render_character_card
render_new_card = _render_new_card
run_render = _run_render

router = APIRouter()

NO_CACHE_REQ_HEADERS = {
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}

_FETCH_LIMIT = max(1, int(os.getenv("RENDER_FETCH_CONCURRENCY", "32")))
_fetch_sem = asyncio.Semaphore(_FETCH_LIMIT)


def _add_cb(url: str, cb: Optional[str]) -> str:
    if not cb:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}cb={cb}"

def _cache_buster(enabled: bool, bucket_seconds: int = 300) -> Optional[str]:
    if not enabled:
        return None
    return str(int(time.time() // bucket_seconds))

@router.get("/profile", summary="LostArk API → PNG")
async def render_profile(
    nickname: str = Query(..., description="캐릭터 닉네임"),
    fresh: int = Query(0, description="캐시 무력화(1=강제)"),
    user_id: Optional[str] = Query(None, description="디스코드 유저 ID"),
):
    try:
        headers_profile = {
            "accept": "application/json",
            "authorization": f"bearer {LOSTARK_API_PROFILE_KEY}",
            **(NO_CACHE_REQ_HEADERS if fresh else {}),
        }
        client = await get_http_client()
        url = (
            "https://developer-lostark.game.onstove.com/armories/characters/"
            f"{nickname}?filters=profiles+equipment+engravings+gems+collectibles+arkpassive+arkgrid"
        )
        async with _fetch_sem:
            res = await client.get(url, headers=headers_profile, timeout=30.0)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=f"LostArk API error: {res.text}")
        payload = res.json()

        # ㅋㅋ 데이터 주는거 미친거 아니냐 얘네?
        ap = payload.get("ArmoryProfile", {}) or {}
        server = ap.get("ServerName") or ""
        nick = ap.get("CharacterName") or ""
        class_name = ap.get("CharacterClassName") or ""
        equipments = payload.get("ArmoryEquipment", []) or []

        uid = (user_id or "").strip()
        try:
            emap = await discord_service._get_user_emojis([uid]) or {}
        except Exception:
            emap = {}

        token = emap.get(uid)
        payload["user_id"] = uid
        if token:
            payload.setdefault("emojis", {})[uid] = token

        def _render() -> BytesIO:
            return render_character_card(
                server,
                nick,
                class_name,
                equipments,
                payload,
                nickname_emoji=token,
            )

        try:
            buf: BytesIO = await run_render(_render, timeout=90.0)
        except TimeoutError:
            raise HTTPException(status_code=504, detail="렌더링 타임아웃")

        return StreamingResponse(buf, media_type="image/png")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"render error: {e}")

def _clean_num(s: Optional[str]) -> str:
    if s is None:
        return ""
    return str(s)

async def _fetch_json_and_images(nickname: str, *, fresh: bool = False):
    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_API_SIBLINGS_KEY}",
        **(NO_CACHE_REQ_HEADERS if fresh else {}),
    }
    url = (
        f"https://developer-lostark.game.onstove.com/armories/characters/"
        f"{nickname}?filters=profiles+arkpassive"
    )

    client = await get_http_client()
    async with _fetch_sem:
        res = await client.get(url, headers=headers, timeout=30.0)
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=f"LostArk API error: {res.text}")

    payload = res.json() or {}
    ap = payload.get("ArmoryProfile", {}) or {}
    character_image_url = ap.get("CharacterImage") or None

    char_img: Optional[Image.Image] = None

    async def _download_image(url: str, *, fresh: bool) -> Optional[Image.Image]:
        try:
            cb = _cache_buster(fresh)
            final_url = _add_cb(url, cb)
            async with _fetch_sem:
                r = await client.get(final_url, headers=NO_CACHE_REQ_HEADERS if fresh else {}, timeout=30.0)
            if r.status_code == 200:
                return await anyio.to_thread.run_sync(decode_image, r.content)
            if fresh and r.status_code in (403, 404):
                async with _fetch_sem:
                    r2 = await client.get(url, headers=NO_CACHE_REQ_HEADERS, timeout=30.0)
                if r2.status_code == 200:
                    return await anyio.to_thread.run_sync(decode_image, r2.content)
        except Exception:
            return None
        return None

    if character_image_url:
        char_img = await _download_image(character_image_url, fresh=fresh)

    return payload, char_img

@router.get("/mini-profile", summary="LostArk API → Mini PNG (156x241)")
async def render_mini_profile(
    nickname: str = Query(..., description="캐릭터 닉네임"),
    fresh: int = Query(0, description="캐시 무력화(1=강제)")
):
    try:
        payload, char_img = await _fetch_json_and_images(nickname, fresh=bool(fresh))
        class_deco_img: Optional[Image.Image] = None

        try:
            deco_url = ((((payload or {}).get("ArmoryProfile") or {}).get("Decorations") or {}).get("Symbol") or None)
            if deco_url:
                client = await get_http_client()
                cb = _cache_buster(bool(fresh))
                final_deco = _add_cb(deco_url, cb)
                async with _fetch_sem:
                    r = await client.get(final_deco, headers=NO_CACHE_REQ_HEADERS if fresh else {}, timeout=30.0)
                if r.status_code == 200:
                    class_deco_img = await anyio.to_thread.run_sync(decode_image, r.content)
                elif fresh:
                    async with _fetch_sem:
                        r2 = await client.get(deco_url, headers=NO_CACHE_REQ_HEADERS, timeout=30.0)
                    if r2.status_code == 200:
                        class_deco_img = await anyio.to_thread.run_sync(decode_image, r2.content)
        except Exception:
            class_deco_img = None

        ap = payload.get("ArmoryProfile", {}) or {}
        ark_passive = (payload.get("ArkPassive") or {})

        server      = ap.get("ServerName") or ""
        nick        = ap.get("CharacterName") or nickname
        class_name  = ap.get("CharacterClassName") or ""
        title = ap.get("Title") or ""
        title_ark = (ark_passive or {}).get("Title") or ""

        item_level_value        = _clean_num(ap.get("ItemAvgLevel"))
        expedition_level_value  = _clean_num(ap.get("ExpeditionLevel"))
        honor_point_value       = _clean_num(ap.get("HonorPoint"))
        combat_power_value      = _clean_num(ap.get("CombatPower"))
        pvp_value               = _clean_num(ap.get("PvpGradeName"))
        guild_name              = _clean_num(ap.get("GuildName"))

        client = await get_http_client()
        sib_headers = {
            "accept": "application/json",
            "authorization": f"bearer {LOSTARK_API_SIBLINGS_KEY}",
            **(NO_CACHE_REQ_HEADERS if fresh else {}),
        }
        sib_url = f"https://developer-lostark.game.onstove.com/characters/{nickname}/siblings"
        async with _fetch_sem:
            sib_res = await client.get(sib_url, headers=sib_headers, timeout=30.0)
        if sib_res.status_code != 200:
            raise HTTPException(status_code=sib_res.status_code, detail=f"LostArk siblings API error: {sib_res.text}")
        siblings = sib_res.json() or []

        special = any((row.get("CharacterName") or "") in SPECIAL_NICKNAME for row in siblings)
        is_mokoko = any((row.get("CharacterName") or "") in MOKOKO_NICKNAME for row in siblings)

        def _render() -> BytesIO:
            return render_new_card(
                server_name=server,
                nickname=nick,
                class_name=class_name,
                title=title,
                class_job=title_ark,
                item_level_value=item_level_value,
                expedition_level_value=int(expedition_level_value or 0),
                honor_point_value=str(honor_point_value),
                combat_power_value=str(combat_power_value),
                pvp_value=str(pvp_value),
                guild_name=str(guild_name),
                character_image=char_img,
                special=special,
                class_deco_bg=class_deco_img,
                is_mokoko=is_mokoko
            )

        buf: BytesIO = await run_render(_render, timeout=90.0)
        return StreamingResponse(buf, media_type="image/png")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"render error: {e}")
