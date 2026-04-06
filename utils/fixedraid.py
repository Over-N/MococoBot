from __future__ import annotations
from datetime import datetime, timedelta, date, time, timezone
from typing import Any, Dict, List, Optional
from database.connection import get_db
from services.party_service import party_service

KST = timezone(timedelta(hours=9))
WEEKDAYS = ("\uC6D4", "\uD654", "\uC218", "\uBAA9", "\uAE08", "\uD1A0", "\uC77C")

def _fmt_title_datetime_kst(d: date, hh: int, mm: int) -> str:
    dt = datetime.combine(d, time(hh, mm, 0, tzinfo=KST)).astimezone(KST)
    wd = WEEKDAYS[dt.weekday()]
    return f"{dt.strftime('%y.%m.%d')}({wd}) {dt.strftime('%H:%M')}"

async def list_fixed_raids_with_counts(db, guild_id: int) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT fr.id, fr.guild_id, fr.channel_id, fr.weekday, fr.hour, fr.minute,
               fr.boss, fr.difficulty, fr.message, fr.capacity, fr.is_active,
               IFNULL(m.cnt,0) AS member_count
        FROM fixed_raid fr
        LEFT JOIN (
          SELECT fixed_raid_id, COUNT(*) AS cnt
          FROM fixed_raid_member
          GROUP BY fixed_raid_id
        ) m ON m.fixed_raid_id = fr.id
        WHERE fr.guild_id = ? AND fr.is_active = 1
        ORDER BY fr.weekday, fr.hour, fr.minute, fr.id
        """,
        (guild_id,),
    )
    res: List[Dict[str, Any]] = []
    for r in rows:
        res.append({
            "id": r["id"],
            "guild_id": r["guild_id"],
            "channel_id": r["channel_id"],
            "weekday": r["weekday"],
            "weekday_label": WEEKDAYS[r["weekday"] % 7],
            "hour": r["hour"],
            "minute": r["minute"],
            "boss": r["boss"],
            "difficulty": r["difficulty"],
            "message": r["message"],
            "capacity": r["capacity"],
            "is_active": r["is_active"],
            "member_count": int(r["member_count"]),
        })
    return res

async def list_fixed_raids_for_dropdown(db, guild_id: int) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, weekday, hour, minute, boss, difficulty
        FROM fixed_raid
        WHERE guild_id = ? AND is_active = 1
        ORDER BY weekday, hour, minute, id
        """,
        (guild_id,),
    )
    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append({
            "id": r["id"],
            "label": f"[{WEEKDAYS[r['weekday'] % 7]}] {int(r['hour']):02d}:{int(r['minute']):02d} {r['boss']} {r['difficulty']}"
        })
    return items

async def create_fixed_raid(db, data: Dict[str, Any]) -> int:
    await db.execute(
        """
        INSERT INTO fixed_raid
        (guild_id, channel_id, weekday, hour, minute, boss, difficulty, message, capacity, created_by_user_id, is_active, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,NOW())
        """,
        (
            data["guild_id"], data.get("channel_id"), data["weekday"], data["hour"], data["minute"],
            data["boss"], data["difficulty"], data.get("message"), data["capacity"], data.get("created_by_user_id"),
        ),
    )
    rid_row = await db.fetch_one("SELECT LAST_INSERT_ID() AS id")
    await db.commit()
    return int((rid_row or {}).get("id") or 0)

async def delete_fixed_raid(db, fixed_raid_id: int) -> bool:
    await db.execute("UPDATE fixed_raid SET is_active = 0 WHERE id = ? AND is_active = 1", (fixed_raid_id,))
    changed = await db.fetch_one("SELECT ROW_COUNT() AS c")
    await db.commit()
    return int((changed or {}).get("c") or 0) > 0

async def join_fixed_raid_member(db, fixed_raid_id: int, user_id: int, character_id: Optional[int], role: int,
                           nickname: Optional[str]) -> None:
    """
    Add a member to a fixed raid.
    Mirrors party_service join flow: validates raid, capacity, dup; resolves/creates character by nickname.
    """
    row = await db.fetch_one(
        "SELECT capacity FROM fixed_raid WHERE id = ? AND is_active = 1 FOR UPDATE",
        (fixed_raid_id,),
    )
    if not row:
        raise ValueError("not_found")
    capacity = int(row["capacity"])

    cnt_row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM fixed_raid_member WHERE fixed_raid_id = ? FOR UPDATE",
        (fixed_raid_id,),
    )
    cnt = int((cnt_row or {}).get("c", 0))
    if cnt >= capacity:
        raise ValueError("capacity_exceeded")

    exists = await db.fetch_one(
        "SELECT 1 AS x FROM fixed_raid_member WHERE fixed_raid_id = ? AND user_id = ? LIMIT 1",
        (fixed_raid_id, user_id),
    )
    if exists:
        raise ValueError("duplicate")

    resolved_character_id: Optional[int] = character_id
    if not resolved_character_id and nickname:
        char_row = await db.fetch_one(
            "SELECT id FROM `character` WHERE char_name = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (nickname,),
        )
        if char_row:
            try:
                resolved_character_id = int(char_row["id"])
            except Exception:
                resolved_character_id = None
        else:
            await db.execute(
                "INSERT INTO `character` (char_name, class_id, item_lvl, combat_power, updated_at)"
                " VALUES (?,?,?,?,NOW())",
                (
                    nickname,
                    None,
                    0,
                    0,
                ),
            )
            rowid = await db.fetch_one("SELECT LAST_INSERT_ID() AS id")
            resolved_character_id = int((rowid or {}).get("id") or 0) or None

    await db.execute(
        "INSERT INTO fixed_raid_member (fixed_raid_id, user_id, character_id, role, nickname, created_at)"
        " VALUES (?,?,?,?,?,NOW())",
        (
            fixed_raid_id,
            user_id,
            resolved_character_id,
            int(role or 0),
            (nickname or None),
        ),
    )
    await db.commit()

async def leave_fixed_raid_member(db, fixed_raid_id: int, user_id: int) -> bool:
    await db.execute("DELETE FROM fixed_raid_member WHERE fixed_raid_id = ? AND user_id = ?", (fixed_raid_id, user_id))
    changed = await db.fetch_one("SELECT ROW_COUNT() AS c")
    await db.commit()
    return int((changed or {}).get("c") or 0) > 0

def _next_date_from_weekday(base: date, weekday: int) -> date:
    off = (weekday - base.weekday()) % 7
    return base + timedelta(days=off)

def _fmt_start_date_kst(d: date, hh: int, mm: int) -> str:
    dt = datetime.combine(d, time(hh, mm, 0, tzinfo=KST)).astimezone(KST)
    return dt.strftime("%Y-%m-%d %H:%M")

async def weekly_generate_for_guild(guild_id: int, base_date: Optional[date] = None) -> int:
    async with get_db() as db:
        rows = await db.fetch_all(
            """
            SELECT id, channel_id, weekday, hour, minute, boss, difficulty, message, capacity, created_by_user_id
            FROM fixed_raid
            WHERE guild_id = ? AND is_active = 1
            """,
            (guild_id,),
        )

    today = base_date or datetime.now(KST).date()
    created = 0
    for r in rows:
        fixed_raid_id = int(r["id"])
        wd, hh, mm = int(r["weekday"]), int(r["hour"]), int(r["minute"])
        boss, diff, msg, creator = r["boss"], r["difficulty"], r["message"], r["created_by_user_id"]
        event_date = _next_date_from_weekday(today, wd)

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO party_source_fixedraid (fixed_raid_id, event_date)
                VALUES (?,?)
                ON DUPLICATE KEY UPDATE fixed_raid_id = VALUES(fixed_raid_id)
                """,
                (fixed_raid_id, event_date),
            )
            link = await db.fetch_one(
                "SELECT party_id FROM party_source_fixedraid WHERE fixed_raid_id = ? AND event_date = ?",
                (fixed_raid_id, event_date),
            )
            party_id = int(link["party_id"]) if (link and link["party_id"] is not None) else None
            await db.commit()

        if party_id is not None:
            continue

        start_date = _fmt_start_date_kst(event_date, hh, mm)
        title_core = f"{boss} : {diff}"
        title_time = _fmt_title_datetime_kst(event_date, hh, mm)
        title_str = f"[🖥] [{title_core}] {title_time}" + (f" : {msg.strip()}" if msg and str(msg).strip() else "")
        payload = {
            "title": title_str,
            "raid_name": boss,
            "difficulty": diff,
            "start_date": start_date,
            "owner_id": creator,
            "message": msg,
        }
        result = await party_service.create_party(guild_id, payload)
        party_id = int(result.get("party_id") or result.get("id") or result.get("data", {}).get("party_id") or 0)
        if not party_id:
            continue

        resolved_members: List[Dict[str, Any]] = []
        async with get_db() as db:
            await db.execute(
                "UPDATE party_source_fixedraid SET party_id = ? WHERE fixed_raid_id = ? AND event_date = ?",
                (party_id, fixed_raid_id, event_date),
            )
            members = await db.fetch_all(
                "SELECT user_id, character_id, role, nickname FROM fixed_raid_member WHERE fixed_raid_id = ?",
                (fixed_raid_id,),
            )
            for m in members:
                uid = m["user_id"]
                cid = m.get("character_id")
                role = int(m.get("role") or 0)
                nick = m.get("nickname")
                resolved_cid: Optional[int] = None
                if cid:
                    resolved_cid = int(cid)
                elif nick:
                    c_row = await db.fetch_one(
                        "SELECT id FROM `character` WHERE char_name = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
                        (nick,),
                    )
                    if c_row:
                        resolved_cid = int(c_row["id"])
                    else:
                        await db.execute(
                            "INSERT INTO `character` (char_name, class_id, item_lvl, combat_power, updated_at)"
                            " VALUES (?,?,?,?,NOW())",
                            (
                                nick,
                                None,
                                0,
                                0,
                            ),
                        )
                        rowid = await db.fetch_one("SELECT LAST_INSERT_ID() AS id")
                        resolved_cid = int((rowid or {}).get("id") or 0) or None
                resolved_members.append(
                    {
                        "user_id": uid,
                        "role": role,
                        "character_id": resolved_cid,
                    }
                )
            await db.commit()

        for m in resolved_members:
            try:
                resolved_cid = int(m.get("character_id") or 0)
                await party_service.join_party(party_id, resolved_cid, str(m["user_id"]), int(m["role"]))
            except Exception:
                pass
        created += 1
    return created

