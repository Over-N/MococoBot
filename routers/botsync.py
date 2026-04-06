from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from database.connection import get_db

router = APIRouter()

class BotGuild(BaseModel):
    id: int
    name: Optional[str] = None
    icon: Optional[str] = None
    owner_id: Optional[int] = None

@router.post("/botsync/guilds/bulk_upsert")
async def bulk_upsert(items: List[BotGuild]):
    """봇이 가입한 길드 전체/증분 upsert (최초 구동/주기 동기화)"""
    try:
        async with get_db() as db:
            for g in items or []:
                await db.execute("""
                    INSERT INTO bot_guilds (guild_id, name, owner_id, icon)
                    VALUES (?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE name=VALUES(name), owner_id=VALUES(owner_id), icon=VALUES(icon)
                """, (g.id, g.name, g.owner_id, g.icon))
            await db.commit()
        return JSONResponse({"ok": True, "upserted": len(items or [])})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"bulk_upsert error: {e}")

@router.delete("/botsync/guilds/{guild_id}")
async def remove_guild(guild_id: int):
    """봇이 길드에서 나갔을 때 삭제"""
    try:
        async with get_db() as db:
            await db.execute("DELETE FROM bot_guilds WHERE guild_id = ?", (guild_id,))
            await db.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"remove_guild error: {e}")
