from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
import random
import logging

from database.connection import get_db

KST = timezone(timedelta(hours=9))
router = APIRouter()
logger = logging.getLogger(__name__)


class ScheduleAdd(BaseModel):
    hh: str = Field(..., pattern=r"^\d{2}$")
    mm: str = Field(..., pattern=r"^\d{2}$")

class QuizConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    channel_id: Optional[int] = None
    schedule_hhmm: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")

class AttemptPayload(BaseModel):
    guild_id: int
    message_id: int
    user_id: int
    username: str
    answer: str

def _normalize_answer(s: str) -> str:
    return (s or "").strip().lower()

@router.get("/config/{guild_id}")
async def get_config(guild_id: int):
    """없으면 기본 레코드 즉시 만들고 반환(비활성 상태)."""
    async with get_db() as db:
        rows = await db.execute(
            "SELECT guild_id, enabled, channel_id, schedule_hhmm FROM quiz_config WHERE guild_id = ?",
            (guild_id,),
        )
        if rows:
            return rows[0]

        await db.execute("INSERT IGNORE INTO quiz_config (guild_id) VALUES (?)", (guild_id,))
        await db.commit()

        return {
            "guild_id": guild_id,
            "enabled": 0,
            "channel_id": None,
            "schedule_hhmm": None,
        }

@router.patch("/config/{guild_id}")
async def patch_config(guild_id: int, payload: QuizConfigPatch):
    """부분 업데이트: enabled / channel_id / schedule_hhmm"""
    sets, args = [], []
    if payload.enabled is not None:
        sets.append("enabled = ?")
        args.append(1 if payload.enabled else 0)
    if payload.channel_id is not None:
        sets.append("channel_id = ?")
        args.append(payload.channel_id)
    if payload.schedule_hhmm is not None:
        sets.append("schedule_hhmm = ?")
        args.append(payload.schedule_hhmm)

    if not sets:
        return {"updated": False}

    args.append(guild_id)
    async with get_db() as db:
        await db.execute(f"UPDATE quiz_config SET {', '.join(sets)} WHERE guild_id = ?", tuple(args))
        await db.commit()
    return {"updated": True}


@router.get("/schedules/{guild_id}")
async def get_schedules(guild_id: int):
    async with get_db() as db:
        rows = await db.execute(
            "SELECT id, hhmm, enabled, last_sent_date FROM quiz_schedules WHERE guild_id = ? ORDER BY hhmm",
            (guild_id,)
        )
        return rows or []

@router.post("/schedules/{guild_id}/add")
async def add_schedule(guild_id: int, payload: ScheduleAdd):
    hhmm = f"{payload.hh}:{payload.mm}"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO quiz_schedules (guild_id, hhmm) VALUES (?, ?) ON DUPLICATE KEY UPDATE enabled = 1",
            (guild_id, hhmm)
        )
        await db.commit()
    return {"ok": True, "hhmm": hhmm}

@router.delete("/schedules/{guild_id}/remove")
async def remove_schedule(guild_id: int, hhmm: str = Query(..., pattern=r"^\d{2}:\d{2}$")):
    async with get_db() as db:
        await db.execute("DELETE FROM quiz_schedules WHERE guild_id = ? AND hhmm = ?", (guild_id, hhmm))
        await db.commit()
    return {"ok": True}

@router.post("/send_now")
async def send_now(guild_id: int = Query(...), channel_id: Optional[int] = Query(None)):
    from services.discord_service import discord_service

    async with get_db() as db:
        if channel_id is None:
            cfg = await db.execute("SELECT channel_id FROM quiz_config WHERE guild_id = ?", (guild_id,))
            if not cfg or not cfg[0]["channel_id"]:
                raise HTTPException(400, "채널이 설정되어 있지 않습니다.")
            channel_id = int(cfg[0]["channel_id"])

        quiz_pool = await db.execute(
            "SELECT id, question, explanation, image_url FROM quiz_bank WHERE enabled = 1"
        ) or []
        if not quiz_pool:
            raise HTTPException(404, "No enabled quiz questions available.")
        picked = random.choice(quiz_pool)
        quiz_id = int(picked["id"])
        question = picked["question"]
        explanation = picked.get("explanation")
        image_url = picked.get("image_url")

    msg_id = await discord_service.send_quiz_message(
        str(channel_id), question,
        guild_id=guild_id, quiz_id=quiz_id,
    )
    if not msg_id:
        raise HTTPException(500, "메시지 전송에 실패했습니다.")

    async with get_db() as db:
        await db.execute(
            "INSERT INTO quiz_active (guild_id, message_id, channel_id, quiz_id) VALUES (?, ?, ?, ?)",
            (guild_id, int(msg_id), channel_id, quiz_id)
        )
        await db.commit()

    return {
        "ok": True,
        "quiz_id": quiz_id,
        "channel_id": channel_id,
        "message_id": int(msg_id),
        "question": question,
        "explanation": explanation,
        "image_url": image_url,
    }

@router.post("/attach_message")
async def attach_message(guild_id: int = Query(...), message_id: int = Query(...)):
    async with get_db() as db:
        await db.execute("UPDATE quiz_active SET message_id = ? WHERE guild_id = ?", (message_id, guild_id))
        await db.commit()
    return {"ok": True}

@router.get("/dispatch/due")
async def dispatch_due():
    def _chunked(items: List[Any], size: int) -> List[List[Any]]:
        if size <= 0:
            return [items]
        return [items[i:i + size] for i in range(0, len(items), size)]

    now_kst = datetime.now(KST)
    hhmm = now_kst.strftime("%H:%M")
    today = now_kst.date()
    out: List[Dict[str, Any]] = []
    async with get_db() as db:
        rows = await db.execute(
            """
            SELECT s.guild_id, s.hhmm, c.channel_id
            FROM quiz_schedules s
            JOIN quiz_config c ON c.guild_id = s.guild_id
            WHERE c.enabled = 1 AND s.enabled = 1 AND s.hhmm = ? AND (s.last_sent_date IS NULL OR s.last_sent_date < ?)
            """,
            (hhmm, today)
        ) or []
        quiz_pool = await db.execute(
            "SELECT id, question, image_url FROM quiz_bank WHERE enabled = 1"
        ) or []
        if not quiz_pool:
            return out

        for r in rows:
            q = random.choice(quiz_pool)
            out.append({"guild_id": r["guild_id"], "channel_id": r["channel_id"], "quiz": q})
        if not out:
            return out

        guild_ids = [x["guild_id"] for x in out]
        for gid_chunk in _chunked(guild_ids, 200):
            marks = ",".join("?" for _ in gid_chunk)
            await db.execute(
                f"DELETE FROM quiz_active WHERE guild_id IN ({marks})",
                tuple(gid_chunk),
            )

        for out_chunk in _chunked(out, 200):
            vals = ",".join(["(?, 0, ?, ?)"] * len(out_chunk))
            flat: List[Any] = []
            for x in out_chunk:
                flat.extend([x["guild_id"], x["channel_id"], x["quiz"]["id"]])
            await db.execute(
                f"INSERT INTO quiz_active (guild_id, message_id, channel_id, quiz_id) VALUES {vals}",
                tuple(flat),
            )

        for gid_chunk in _chunked(guild_ids, 200):
            marks = ",".join("?" for _ in gid_chunk)
            await db.execute(
                f"UPDATE quiz_schedules SET last_sent_date = ? WHERE hhmm = ? AND guild_id IN ({marks})",
                (today, hhmm, *gid_chunk),
            )
        await db.commit()
    return out

@router.post("/attempt")
async def attempt(payload: AttemptPayload):
    guild_id = payload.guild_id
    message_id = payload.message_id
    user_id = payload.user_id
    username = payload.username
    raw_answer = payload.answer
    answer = _normalize_answer(raw_answer)

    async with get_db() as db:
        rows = await db.execute(
            """
            SELECT a.quiz_id, a.solved_at, a.solved_by, a.solved_name,
                   b.answer_raw, b.explanation
              FROM quiz_active a
              JOIN quiz_bank   b ON a.quiz_id = b.id
             WHERE a.guild_id = ? AND a.message_id = ?
             LIMIT 1
            """,
            (guild_id, message_id)
        )
        if not rows:
            return {"ok": False, "is_correct": False, "first_solver": False}

        rec = rows[0]
        solved_at   = rec["solved_at"]
        solved_by   = rec.get("solved_by")
        solved_name = rec.get("solved_name")
        explanation = rec.get("explanation")
        correct_now = (_normalize_answer(rec["answer_raw"]) == answer)
        correct_answer_raw  = rec.get("answer_raw") or ""
        correct_answer_norm = _normalize_answer(correct_answer_raw)

        if solved_at:
            try:
                await db.execute(
                    "INSERT INTO quiz_attempt_log (guild_id, message_id, user_id, username, attempt, is_correct) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (guild_id, message_id, user_id, username, raw_answer[:255])
                )
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to write quiz attempt log (already_solved, guild_id=%s, message_id=%s, user_id=%s)",
                    guild_id,
                    message_id,
                    user_id,
                )

            return {
                "ok": True,
                "is_correct": False,
                "first_solver": False,
                "already_solved": True,
                "solved_by": {"user_id": solved_by, "username": solved_name},
                "explanation": explanation,
            }

        if not correct_now:
            try:
                await db.execute(
                    "INSERT INTO quiz_attempt_log (guild_id, message_id, user_id, username, attempt, is_correct) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (guild_id, message_id, user_id, username, raw_answer[:255])
                )
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to write quiz attempt log (wrong_answer, guild_id=%s, message_id=%s, user_id=%s)",
                    guild_id,
                    message_id,
                    user_id,
                )

            return {"ok": True, "is_correct": False, "first_solver": False}

        now_utc = datetime.utcnow()
        await db.execute(
            """
            UPDATE quiz_active
               SET solved_by = ?, solved_name = ?, solved_at = ?
             WHERE guild_id = ? AND message_id = ? AND solved_at IS NULL
            """,
            (user_id, username[:100], now_utc, guild_id, message_id)
        )
        await db.commit()

        solved = await db.execute(
            "SELECT solved_by, solved_name, solved_at FROM quiz_active WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id)
        )
        first_solver = bool(solved and solved[0]["solved_at"] and int(solved[0]["solved_by"]) == int(user_id))

        try:
            await db.execute(
                "INSERT INTO quiz_attempt_log (guild_id, message_id, user_id, username, attempt, is_correct) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, message_id, user_id, username, raw_answer[:255], 1 if first_solver else 0)
            )
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to write quiz attempt log (correct_answer, guild_id=%s, message_id=%s, user_id=%s)",
                guild_id,
                message_id,
                user_id,
            )

        if not first_solver:
            return {
                "ok": True,
                "is_correct": False,
                "first_solver": False,
                "already_solved": True,
                "solved_by": {
                    "user_id": solved[0]["solved_by"] if solved else None,
                    "username": solved[0]["solved_name"] if solved else None,
                },
                "explanation": explanation,
            }

        await db.execute(
            """
            INSERT INTO quiz_scores (guild_id, user_id, username, score, last_hit)
            VALUES (?, ?, ?, 1, ?)
            ON DUPLICATE KEY UPDATE score = score + 1, username = VALUES(username), last_hit = VALUES(last_hit)
            """,
            (guild_id, user_id, username[:100], now_utc)
        )
        await db.commit()

        return {
            "ok": True,
            "is_correct": True,
            "first_solver": True,
            "explanation": explanation,
            "solved_by": {"user_id": user_id, "username": username},
            "answer_raw": correct_answer_raw,
            "answer_norm": correct_answer_norm,
        }
        
@router.get("/scoreboard")
async def scoreboard(guild_id: int, period: str = Query("week", pattern="^(week|month)$")):
    now_kst = datetime.now(KST)
    async with get_db() as db:
        if period == "week":
            start = (now_kst - timedelta(days=now_kst.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now_kst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_utc = start.astimezone(timezone.utc)
        rows = await db.execute(
            """
            SELECT user_id, MAX(username) AS username, COUNT(*) AS score
            FROM quiz_attempt_log l
            JOIN quiz_active a ON a.guild_id = l.guild_id AND a.message_id = l.message_id
            WHERE l.guild_id = ? AND l.is_correct = 1 AND a.solved_at >= ?
            GROUP BY user_id
            ORDER BY score DESC, user_id ASC
            LIMIT 20
            """,
            (guild_id, start_utc)
        )
        return rows or []

@router.get("/ranking")
async def ranking(
    guild_id: int,
    period: str = Query("week", pattern="^(week|month|all)$")
):
    """
    주/월: quiz_attempt_log.created_at 기준 (정답 기록 유지)
    전체: quiz_scores 누적 사용
    반환: [{user_id, username, score}]
    """
    async with get_db() as db:
        if period == "all":
            rows = await db.execute(
                """
                SELECT user_id, username, score
                FROM quiz_scores
                WHERE guild_id = ?
                ORDER BY score DESC, user_id ASC
                LIMIT 20
                """,
                (guild_id,)
            )
            return rows or []

        now_kst = datetime.now(KST)
        if period == "week":
            start_kst = (now_kst - timedelta(days=now_kst.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_kst = now_kst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        start_ts = start_kst.strftime("%Y-%m-%d %H:%M:%S")

        rows = await db.execute(
            """
            SELECT user_id,
                   MAX(username) AS username,
                   COUNT(*)      AS score
            FROM quiz_attempt_log
            WHERE guild_id = ?
              AND is_correct = 1
              AND created_at >= ?
            GROUP BY user_id
            ORDER BY score DESC, user_id ASC
            LIMIT 20
            """,
            (guild_id, start_ts)
        )
        return rows or []
