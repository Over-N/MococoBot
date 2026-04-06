from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageOps
import anyio
import asyncio
from pathlib import Path
import time
import logging

from database.connection import get_db, DatabaseManager
from render_routers.party_core import get_party_core
from render_routers.image_utils import decode_image
from services.party_service import party_service
from render_routers.config import (
    LOSTARK_API_PROFILE_KEY,
    LOSTARK_API_SIBLINGS_KEY,
    SPECIAL_NICKNAME,
    MOKOKO_NICKNAME,
)

from render.mini_card_renderer import (
    render_new_card as _render_new_card,
    CARD_W, CARD_H,
)
_render_card = _render_new_card

from render.mini_card_renderer import _clean_title as clean_html
from utils.http_client import get_http_client

router = APIRouter()
logger = logging.getLogger(__name__)

NO_CACHE_REQ_HEADERS = {
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}

def _cache_buster(enabled: bool, bucket_seconds: int = 300) -> Optional[str]:
    return str(int(time.time() // bucket_seconds)) if enabled else None

def _add_cb(url: str, cb: Optional[str]) -> str:
    if not cb:
        return url
    return f"{url}{'&' if '?' in url else '?'}cb={cb}"

# ===== 칭호별 배경 매핑 & 우선순위 =====
TITLE_BG_MAP = {
    "심연의 군주":   "kazeroth.png",
    "이클립스":     "kamen.png",
    "몽환의 지배자": "abrelshud.png",
    "광기의 그림자": "kouku.png",
    "쾌락의 탐닉자": "biackiss.png",
    "마수의 포효":   "valtan.png",
}
TITLE_PRIORITY = [
    "심연의 군주",
    "이클립스",
    "몽환의 지배자",
    "광기의 그림자",
    "쾌락의 탐닉자",
    "마수의 포효",
]

def _arrange_slots(participants: Dict[str, Any]) -> List[Optional[Dict[str, Any]]]:
    dealers = list((participants.get("dealers") or [])[:6])        # 최대 6
    supporters = list((participants.get("supporters") or [])[:2])  # 최대 2

    grid: List[Optional[Dict[str, Any]]] = [None]*8
    if supporters:
        grid[3] = supporters[0]
    if len(supporters) >= 2:
        grid[7] = supporters[1]

    # 딜러 채우기(좌->우)
    di = 0
    for idx in [0,1,2,4,5,6]:
        if di < len(dealers):
            grid[idx] = dealers[di]
            di += 1
    return grid

# ==== 닉 추출 ====
def _pick_nickname(p: Optional[Dict[str, Any]]) -> Optional[str]:
    if not p:
        return None
    for k in ("nickname","name","character_name","characterName","CharacterName"):
        v = p.get(k)
        if v:
            return str(v)
    return None

# ==== 캐릭 JSON + 이미지/심볼 패치 ====
async def _fetch_character_assets(nickname: str, *, fresh: bool = False):
    ap_headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_API_PROFILE_KEY}",
        **(NO_CACHE_REQ_HEADERS if fresh else {}),
    }
    sib_headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_API_SIBLINGS_KEY}",
        **(NO_CACHE_REQ_HEADERS if fresh else {}),
    }

    client = await get_http_client()
    url = (
        "https://developer-lostark.game.onstove.com/armories/characters/"
        f"{nickname}?filters=profiles+arkpassive"
    )
    r = await client.get(url, headers=ap_headers, timeout=30.0)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"LostArk API error: {r.text}")
    payload = r.json() or {}
    ap = payload.get("ArmoryProfile") or {}
    ark = payload.get("ArkPassive") or {}

    char_img = None
    deco_img = None
    cb = _cache_buster(fresh)

    async def _download(url: str) -> Optional[Image.Image]:
        try:
            final = _add_cb(url, cb)
            rr = await client.get(final, headers=NO_CACHE_REQ_HEADERS if fresh else {}, timeout=30.0)
            if rr.status_code == 200:
                return await anyio.to_thread.run_sync(decode_image, rr.content)
            if fresh and rr.status_code in (403, 404):
                rr2 = await client.get(url, headers=NO_CACHE_REQ_HEADERS, timeout=30.0)
                if rr2.status_code == 200:
                    return await anyio.to_thread.run_sync(decode_image, rr2.content)
        except Exception:
            return None
        return None

    if ap.get("CharacterImage"):
        char_img = await _download(ap["CharacterImage"])
    deco_url = ((ap.get("Decorations") or {}).get("Symbol")) or None
    if deco_url:
        deco_img = await _download(deco_url)

    sib_url = f"https://developer-lostark.game.onstove.com/characters/{nickname}/siblings"
    sib_res = await client.get(sib_url, headers=sib_headers, timeout=30.0)
    siblings = sib_res.json() if sib_res.status_code == 200 else []

    def _clean(x):
        return "" if x is None else str(x)

    data = {
        "server": ap.get("ServerName") or "",
        "nick": ap.get("CharacterName") or nickname,
        "class_name": ap.get("CharacterClassName") or "",
        "title": ap.get("Title") or "",
        "class_job": (ark or {}).get("Title") or "",
        "item_level": _clean(ap.get("ItemAvgLevel")),
        "expedition": int(_clean(ap.get("ExpeditionLevel") or "0") or 0),
        "honor": _clean(ap.get("HonorPoint")),
        "combat": _clean(ap.get("CombatPower")),
        "pvp": _clean(ap.get("PvpGradeName")),
        "guild": _clean(ap.get("GuildName")),
        "siblings": siblings or [],
        "char_img": char_img,
        "deco_img": deco_img,
        "special": False,
        "is_mokoko": False,
    }
    try:
        sib_names = {str((s or {}).get("CharacterName") or "") for s in (data["siblings"] or [])}
        data["special"] = bool(sib_names & SPECIAL_NICKNAME)
        data["is_mokoko"] = bool(sib_names & MOKOKO_NICKNAME)
    except Exception:
        logger.debug("Failed to derive special/mokoko flags for nickname=%s", nickname, exc_info=True)
    return data

def _render_mini_card(d: Dict[str, Any]) -> Image.Image:
    buf: BytesIO = _render_card(
        server_name=d["server"],
        nickname=d["nick"],
        class_name=d["class_name"],
        title=d["title"],
        class_job=d["class_job"],
        item_level_value=d["item_level"],
        expedition_level_value=d["expedition"],
        honor_point_value=d["honor"],
        combat_power_value=d["combat"],
        pvp_value=d["pvp"],
        guild_name=d["guild"],
        character_image=d["char_img"],
        special=d["special"],
        class_deco_bg=d["deco_img"],
        is_mokoko=d["is_mokoko"],
    )
    return Image.open(buf).convert("RGBA")

# ==== 배경 불러오기 ====
def _load_background(filename: Optional[str] = None) -> Image.Image:
    base_dir = Path(__file__).resolve().parent.parent / "render" / "background"
    name = filename or "back.png"
    candidates = [
        Path(__file__).resolve().parent / "background" / name,
        base_dir / name,
        Path("background") / name,
    ]
    for p in candidates:
        if p.exists():
            return Image.open(p).convert("RGBA")
    if filename:
        return _load_background(None)
    return Image.new("RGBA", (CARD_W*4 + 160, CARD_H*2 + 160), (0,0,0,255))

PAD_X, PAD_Y = 30, 30
MARGIN = 40

def _compute_min_canvas() -> Tuple[int, int]:
    cols, rows = 4, 2
    min_w = cols*CARD_W + (cols-1)*PAD_X + 2*MARGIN
    min_h = rows*CARD_H + (rows-1)*PAD_Y + 2*MARGIN
    return min_w, min_h

def _ensure_canvas(width: int, height: int) -> Tuple[int, int]:
    mw, mh = _compute_min_canvas()
    return max(width, mw), max(height, mh)

def _compute_slots(canvas_w: int, canvas_h: int) -> List[Tuple[int,int]]:
    cols, rows = 4, 2
    total_w = cols*CARD_W + (cols-1)*PAD_X
    total_h = rows*CARD_H + (rows-1)*PAD_Y
    start_x = (canvas_w - total_w)//2
    start_y = (canvas_h - total_h)//2
    pts: List[Tuple[int,int]] = []
    for r in range(rows):
        y = start_y + r*(CARD_H + PAD_Y)
        for c in range(cols):
            x = start_x + c*(CARD_W + PAD_X)
            pts.append((x,y))
    return pts  # 0..7 (상4, 하4)


def _compose_lounge_png_sync(
    *,
    raw_bg: Image.Image,
    cards: List[Optional[Image.Image]],
    canvas_w: int,
    canvas_h: int,
) -> bytes:
    bg = ImageOps.fit(
        raw_bg,
        (canvas_w, canvas_h),
        method=Image.LANCZOS,
        centering=(0.5, 0.5),
    )
    pts = _compute_slots(canvas_w, canvas_h)
    for img, (x, y) in zip(cards, pts):
        if img is None:
            continue
        bg.alpha_composite(img, (x, y))

    out = BytesIO()
    bg.save(out, "PNG")
    return out.getvalue()

def _strip_branding(img: Image.Image, bottom_ratio: float = 0.08) -> Image.Image:
    if bottom_ratio <= 0:
        return img
    h_crop = int(img.height * bottom_ratio)
    if h_crop <= 0 or h_crop >= img.height:
        return img
    return img.crop((0, 0, img.width, img.height - h_crop))

def _pick_bg_by_titles(clean_titles: List[str]) -> Optional[str]:
    s = set(t.strip() for t in clean_titles if t)
    for title in TITLE_PRIORITY:
        if title in s:
            return TITLE_BG_MAP.get(title)
    return None

# ==== 메인 엔드포인트 ====
@router.get("/lounge/{party_id}", tags=["라운지 이미지"])
async def get_lounge_image(
    party_id: int,
    fresh: int = 0,
    width: int = 1920,
    height: int = 1080,
):
    try:
        async with get_db() as db:
            _ = await get_party_core(db, party_id)
            participants = await party_service.get_participants_data(party_id)

            participants = participants or {"dealers": [], "supporters": []}

        grid = _arrange_slots(participants)

        # 닉네임 목록
        nicks: List[Optional[str]] = [_pick_nickname(p) for p in grid]
        async def _build_one(nick: Optional[str]) -> Tuple[Optional[Image.Image], Optional[str]]:
            if not nick:
                return None, None
            try:
                d = await _fetch_character_assets(nick, fresh=bool(fresh))
                img = await anyio.to_thread.run_sync(_render_mini_card, d)
                title_clean = clean_html(d.get("title"))
                return img, (title_clean or None)
            except Exception:
                return None, None

        results = await asyncio.gather(*(_build_one(n) for n in nicks))
        cards: List[Optional[Image.Image]] = [r[0] for r in results]
        clean_titles: List[str] = [r[1] or "" for r in results]

        # 배경 선택 (칭호 우선순위 적용)
        chosen_bg_name = _pick_bg_by_titles(clean_titles)

        CANVAS_W, CANVAS_H = _ensure_canvas(width, height)
        raw_bg = await anyio.to_thread.run_sync(_load_background, chosen_bg_name)
        raw_bg = raw_bg.copy()
        if raw_bg.mode != "RGBA":
            raw_bg = raw_bg.convert("RGBA")
        png_bytes = await anyio.to_thread.run_sync(
            lambda: _compose_lounge_png_sync(
                raw_bg=raw_bg,
                cards=cards,
                canvas_w=CANVAS_W,
                canvas_h=CANVAS_H,
            )
        )
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=60",
                "Content-Disposition": f'inline; filename="lounge_{party_id}.png"',
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"라운지 이미지 생성 실패: {e}")
