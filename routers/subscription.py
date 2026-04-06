import os
from fastapi import APIRouter, HTTPException, Query, Request
from database.connection import get_db
from datetime import datetime, date, timedelta
from services.calendar_service import (
    run_daily_fetch_and_dispatch,
    fetch_calendar_json,
    parse_calendar_data,
    CalendarDrawer,
    _get_game_date,
    get_today_rewards_values_from_cache,
    refresh_calendar_cache,
    get_calendar_payload_cached,
    get_gold_islands_schedule_from_cache
)
from services.notice_service import run_notice_fetch_and_dispatch, fetch_notice_json, save_notice_cache
from services.youtube_service import run_youtube_fetch_and_dispatch, fetch_feed_entries, save_youtube_cache_if_changed
from services.discord_service import discord_service
import io
router = APIRouter()

CAL_TEST_GUILD_ID = 1227993158596694139
CAL_TEST_CHANNEL_ID = 1227993159041028109

@router.post("/dispatch/calendar/test")
async def dispatch_calendar_test(
    request: Request,
    game_date_str: str | None = Query(None, description="YYYY-MM-DD. 미지정 시 로아 기준 당일(오전 6시 기준)"),
):
    target_guild_id = int(CAL_TEST_GUILD_ID)
    target_channel_id = int(CAL_TEST_CHANNEL_ID)
    if target_guild_id <= 0 or target_channel_id <= 0:
        raise HTTPException(status_code=500, detail="CAL_TEST_GUILD_ID / CAL_TEST_CHANNEL_ID not set")

    try:
        game_date: date = date.fromisoformat(game_date_str) if game_date_str else _get_game_date()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid game_date_str (expected YYYY-MM-DD)")

    ok = await refresh_calendar_cache()
    if not ok:
        raise HTTPException(status_code=502, detail="calendar fetch failed (refresh_calendar_cache)")

    payload = await get_calendar_payload_cached(target_date=game_date, refresh_policy="never")
    if not payload:
        raise HTTPException(status_code=500, detail="calendar cache missing after refresh")

    parsed = parse_calendar_data(payload, game_date)

    drawer = CalendarDrawer()
    image_bytes = await drawer.draw(game_date, parsed)

    filename = f"calendar_{game_date.strftime('%Y%m%d')}.png"
    file = discord_service.File(image_bytes, filename=filename)

    sent = await discord_service.send_to_channel(
        str(target_channel_id),
        content=f"**[TEST] {game_date} 로스트아크 일정 (cache refreshed)**",
        file=file,
    )
    if not sent:
        raise HTTPException(status_code=502, detail="discord send failed")

    return {
        "ok": True,
        "cache_refreshed": True,
        "game_date": str(game_date),
        "guild_id": target_guild_id,
        "channel_id": target_channel_id,
    }

@router.get("/calendar/rewards")
async def internal_calendar_rewards(
    request: Request,
    day: int = Query(0, description="-1=어제, 0=오늘(기본), 1=내일"),
    date_str: str | None = Query(None, alias="date", description="YYYY-MM-DD. 지정 시 day 무시"),
):
    if date_str:
        try:
            game_date: date = date.fromisoformat(date_str)
        except Exception:
            gd = _get_game_date()
            return {
                "ok": False,
                "status": "bad_request",
                "game_date": gd.isoformat(),
                "message": "날짜 형식이 올바르지 않아요. (YYYY-MM-DD)",
                "rewards": {},
                "adventure_islands": [],
            }
        return await get_today_rewards_values_from_cache(game_date=game_date)

    d = int(day)
    if d < -1:
        d = -1
    elif d > 1:
        d = 1
    game_date = _get_game_date() + timedelta(days=d)
    return await get_today_rewards_values_from_cache(game_date=game_date)


@router.get("/calendar/gold")
async def internal_calendar_gold(request: Request):
    return await get_gold_islands_schedule_from_cache()

@router.post("/user/toggle")
async def toggle_user_subscription(payload: dict):
    try:
        user_id = int(payload["user_id"])
        typ = str(payload["type"])
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")

    async with get_db() as db:
        row = await db.execute(
            "SELECT enabled FROM user_subscriptions WHERE user_id=%s AND type=%s",
            (user_id, typ),
        ) or []
        if row and int(row[0].get("enabled", 0)) == 1:
            res = await db.execute(
                "UPDATE user_subscriptions SET enabled=0 WHERE user_id=%s AND type=%s",
                (user_id, typ),
            )
            if res is None:
                raise HTTPException(status_code=500, detail="db execute failed")
            await db.commit()
            return {"ok": True, "enabled": 0, "action": "disabled"}
        else:
            res = await db.execute(
                """
                INSERT INTO user_subscriptions(user_id, type, enabled)
                VALUES (%s, %s, 1)
                ON DUPLICATE KEY UPDATE enabled=1
                """,
                (user_id, typ),
            )
            if res is None:
                raise HTTPException(status_code=500, detail="db execute failed")
            await db.commit()
            return {"ok": True, "enabled": 1, "action": "enabled"}

@router.post("/channel/toggle")
async def toggle_channel_subscription(payload: dict):
    try:
        guild_id = int(payload["guild_id"])
        channel_id = int(payload["channel_id"])
        typ = str(payload["type"])
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")

    async with get_db() as db:
        row = await db.execute(
            "SELECT enabled FROM guild_channel_subscriptions WHERE guild_id=%s AND type=%s",
            (guild_id, typ),
        ) or []
        if row and int(row[0].get("enabled", 0)) == 1:
            res = await db.execute(
                "UPDATE guild_channel_subscriptions SET enabled=0 WHERE guild_id=%s AND type=%s",
                (guild_id, typ),
            )
            if res is None:
                raise HTTPException(status_code=500, detail="db execute failed")
            await db.commit()
            return {
                "ok": True,
                "enabled": 0,
                "action": "disabled",
                "guild_id": guild_id,
                "channel_id": None,
            }
        else:
            res = await db.execute(
                """
                INSERT INTO guild_channel_subscriptions(guild_id, channel_id, type, enabled)
                VALUES (%s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE enabled=1, channel_id=VALUES(channel_id)
                """,
                (guild_id, channel_id, typ),
            )
            if res is None:
                raise HTTPException(status_code=500, detail="db execute failed")
            await db.commit()
            return {
                "ok": True,
                "enabled": 1,
                "action": "enabled",
                "guild_id": guild_id,
                "channel_id": channel_id,
            }

@router.get("/preview/daily")
async def preview_daily():
    try:
        game_date: date = _get_game_date()
        payload = await fetch_calendar_json()
        if not payload:
            raise HTTPException(status_code=502, detail="calendar fetch failed")
        parsed = parse_calendar_data(payload, game_date)
        return {"ok": True, "game_date": str(game_date), "parsed": parsed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/dispatch/notice")
async def manual_dispatch_notice(initial: bool = Query(False, description="초기 기준점만 설정하고 발송하지 않음")):
    try:
        await run_notice_fetch_and_dispatch(initial=initial)
        return {"ok": True, "message": "notice dispatch executed", "initial": bool(initial)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/preview/notice")
async def preview_notice(fetch: bool = Query(False, description="True면 공지 API 호출→캐시만 저장(전송 없음)")):
    try:
        payload = None
        if fetch:
            payload = await fetch_notice_json()
            await save_notice_cache(payload)
        return {"ok": True, "fetched": bool(fetch), "count": len(payload) if payload else None, "sample": (payload or [])[:5]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/dispatch/youtube")
async def manual_dispatch_youtube(initial: bool = Query(False, description="초기 기준점만 설정하고 발송하지 않음")):
    try:
        await run_youtube_fetch_and_dispatch(initial=initial)
        return {"ok": True, "message": "youtube dispatch executed", "initial": bool(initial)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/preview/youtube")
async def preview_youtube(fetch: bool = Query(False, description="True면 피드 호출→캐시만 저장(전송 없음)")):
    try:
        entries = await fetch_feed_entries() if fetch else None
        if entries is not None:
            await save_youtube_cache_if_changed(entries)
        sample = []
        if entries:
            for e in entries[:5]:
                sample.append({
                    "video_id": e["video_id"],
                    "title": e["title"],
                    "published": e["published"].strftime("%Y-%m-%d %H:%M"),
                    "link": e["link"],
                })
        return {"ok": True, "fetched": bool(fetch), "count": len(entries) if entries else None, "sample": sample}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
