from fastapi import APIRouter, Response, HTTPException
import io
from typing import Dict, Any, List

from render.raid_card_renderer import render_mococo_card
from database.connection import get_db, DatabaseManager
from render_routers.party_core import get_party_core
from services.party_service import party_service
from services.discord_service import discord_service
from render.render_exec import run_render

router = APIRouter()

def _shape_payload(party_row: Dict[str, Any], participants: Dict[str, Any]) -> Dict[str, Any]:
    data = {
        "id": party_row["id"],
        "title": party_row["title"],
        "message": party_row.get("message") or "",
        "raid_name": party_row.get("raid_name") or "",
        "difficulty": party_row.get("difficulty") or "",
        "min_lvl": party_row.get("min_lvl"),
        "start_date": party_row.get("start_date"),
        "dealer": party_row.get("dealer") or 0,
        "supporter": party_row.get("supporter") or 0,
        "participants": participants or {"dealers": [], "supporters": []},
    }
    return {"data": data}

def _collect_user_ids(participants: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for key in ("dealers", "supporters"):
        for p in participants.get(key) or []:
            uid = p.get("user_id")
            if uid is not None:
                ids.append(str(uid))
    return list(dict.fromkeys(ids))

@router.get("/party/{party_id}", tags=["레이드 파티 이미지"])
async def get_party_image(party_id: int):
    try:
        async with get_db() as db:
            party_row = await get_party_core(db, party_id)
            participants = await party_service.get_participants_data(party_id)
            payload = _shape_payload(party_row, participants)

        # 유저 이모지 맵 주입
        user_ids = _collect_user_ids(payload["data"]["participants"])
        if user_ids:
            emojis_map = await discord_service._get_user_emojis(user_ids)
            if emojis_map:
                payload["data"]["emojis"] = {str(k): v for k, v in emojis_map.items()}

        # CPU 렌더는 스레드풀에서, 동시성 제한 + 타임아웃
        def _render() -> bytes:
            img = render_mococo_card(payload, width=680)  # 높이는 자동
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()

        png: bytes = await run_render(_render, timeout=30.0)

        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=60",
                "Content-Disposition": f'inline; filename="party_{party_id}.png"',
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이미지 생성 실패: {e}")
