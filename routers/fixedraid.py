from __future__ import annotations
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, field_validator
from database.connection import get_db
from utils.fixedraid import (
    list_fixed_raids_with_counts,
    list_fixed_raids_for_dropdown,
    create_fixed_raid,
    delete_fixed_raid,
    join_fixed_raid_member,
    leave_fixed_raid_member,
    weekly_generate_for_guild,
)

router = APIRouter()

class CreatePayload(BaseModel):
    guild_id: int
    channel_id: Optional[int] = None
    weekday: int
    hour: int
    minute: int
    boss: str
    difficulty: str
    message: Optional[str] = None
    capacity: int = 8
    created_by_user_id: Optional[int] = None
    @field_validator("weekday")
    @classmethod
    def _v_wd(cls, v: int) -> int:
        if v < 0 or v > 6: raise ValueError("weekday 0..6")
        return v
    @field_validator("hour")
    @classmethod
    def _v_h(cls, v: int) -> int:
        if v < 0 or v > 23: raise ValueError("hour 0..23")
        return v
    @field_validator("minute")
    @classmethod
    def _v_m(cls, v: int) -> int:
        if v < 0 or v > 59: raise ValueError("minute 0..59")
        return v
    @field_validator("capacity")
    @classmethod
    def _v_c(cls, v: int) -> int:
        if v < 1 or v > 30: raise ValueError("capacity 1..30")
        return v

class DeletePayload(BaseModel):
    fixed_raid_id: int

class JoinPayload(BaseModel):
    fixed_raid_id: int
    user_id: int
    character_id: Optional[int] = None
    role: int = 0
    nickname: Optional[str] = None

class LeavePayload(BaseModel):
    fixed_raid_id: int
    user_id: int

class WeeklyGeneratePayload(BaseModel):
    guild_id: int

# New response model for retrieving the list of members in a fixed raid.
class MembersResponse(BaseModel):
    user_id: int
    character_id: Optional[int]
    role: int
    nickname: Optional[str]
    name: Optional[str] = None  # character name if known
    class_id: Optional[int] = None
    class_name: Optional[str] = None
    class_emoji: Optional[str] = None

@router.get("/state", response_class=ORJSONResponse)
async def state(guild_id: int = Query(...)) -> Any:
    async with get_db() as db:
        return {"ok": True, "data": await list_fixed_raids_with_counts(db, guild_id)}

@router.get("/dropdown", response_class=ORJSONResponse)
async def dropdown(guild_id: int = Query(...)) -> Any:
    async with get_db() as db:
        return {"ok": True, "items": await list_fixed_raids_for_dropdown(db, guild_id)}

@router.post("/create", response_class=ORJSONResponse)
async def create(payload: CreatePayload) -> Any:
    async with get_db() as db:
        rid = await create_fixed_raid(db, payload.model_dump())
        return {"ok": True, "fixed_raid_id": rid}

@router.delete("/delete", response_class=ORJSONResponse)
async def delete(payload: DeletePayload) -> Any:
    async with get_db() as db:
        if not await delete_fixed_raid(db, payload.fixed_raid_id):
            raise HTTPException(status_code=404, detail="not_found")
        return {"ok": True}

@router.post("/join", response_class=ORJSONResponse)
async def join(payload: JoinPayload) -> Any:
    async with get_db() as db:
        try:
            await join_fixed_raid_member(
                db,
                fixed_raid_id=payload.fixed_raid_id,
                user_id=payload.user_id,
                character_id=payload.character_id,
                role=payload.role,
                nickname=(payload.nickname or "").strip() or None,
            )
        except ValueError as e:
            msg = str(e)
            if msg == "capacity_exceeded":
                raise HTTPException(status_code=409, detail=msg)
            if msg in ("not_found", "duplicate"):
                raise HTTPException(status_code=404 if msg == "not_found" else 409, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True}

@router.post("/leave", response_class=ORJSONResponse)
async def leave(payload: LeavePayload) -> Any:
    async with get_db() as db:
        if not await leave_fixed_raid_member(db, payload.fixed_raid_id, payload.user_id):
            raise HTTPException(status_code=404, detail="not_found")
        return {"ok": True}

@router.post("/weekly_generate", response_class=ORJSONResponse)
async def weekly_generate(payload: WeeklyGeneratePayload) -> Any:
    created = await weekly_generate_for_guild(payload.guild_id)
    return {"ok": True, "created": created}

# ---------------------------------------------------------------------------
# Additional fixed-raid member management endpoints
# ---------------------------------------------------------------------------

@router.get("/members", response_class=ORJSONResponse)
async def members(fixed_raid_id: int = Query(...)) -> Any:
    """
    Retrieve the current members of a given fixed raid.  This includes their character details
    (if known) so that the UI can display roles, class names, and other metadata.  Returns a list
    of dictionaries keyed similarly to `MembersResponse`.
    """
    async with get_db() as db:
        rows = await db.fetch_all(
            """
            SELECT frm.user_id, frm.character_id, frm.role, frm.nickname,
                   c.char_name AS name, c.class_id,
                   cl.name AS class_name, cl.emoji AS class_emoji
              FROM fixed_raid_member frm
         LEFT JOIN `character` c ON frm.character_id = c.id
         LEFT JOIN class cl ON c.class_id = cl.id
             WHERE frm.fixed_raid_id = ?
          ORDER BY frm.created_at ASC
            """,
            (fixed_raid_id,),
        )
        items = []
        for r in rows or []:
            items.append(
                {
                    "user_id": int(r["user_id"]),
                    "character_id": int(r["character_id"]) if r.get("character_id") is not None else None,
                    "role": int(r.get("role") or 0),
                    "nickname": r.get("nickname"),
                    "name": r.get("name"),
                    "class_id": r.get("class_id"),
                    "class_name": r.get("class_name"),
                    "class_emoji": r.get("class_emoji"),
                }
            )
        return {"ok": True, "items": items}
