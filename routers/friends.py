from fastapi import APIRouter, HTTPException, Query, Path, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from database.connection import get_db
import os, time, hmac, hashlib, json, asyncio
import math
from datetime import datetime, date
from decimal import Decimal
from services.discord_service import discord_service
import random
import logging
from utils.task_utils import fire_and_forget

router = APIRouter()
logger = logging.getLogger(__name__)

ADMIN_2FA_SECRET = os.getenv("ADMIN_2FA_SECRET", "")
ADMIN_WHITELIST = set([x.strip() for x in os.getenv("ADMIN_WHITELIST", "").split(",") if x.strip()])

def require_admin_2fa(request: Request):
    """
    헤더 검증:
      X-Admin-Discord-Id: 호출자 디스코드 ID (화이트리스트 검증)
      X-Admin-Timestamp : 유닉스 초 (±60초 유효)
      X-Admin-Signature : hex(hmac_sha256(secret, f"{admin_id}:{method}:{path}:{ts}"))
    """
    if not ADMIN_2FA_SECRET:
        raise HTTPException(status_code=500, detail="2FA secret not configured")

    admin_id = request.headers.get("X-Admin-Discord-Id", "")
    ts_str   = request.headers.get("X-Admin-Timestamp", "")
    sig_cli  = request.headers.get("X-Admin-Signature", "")

    if not admin_id or not ts_str or not sig_cli:
        raise HTTPException(status_code=401, detail="2FA headers required")

    if admin_id not in ADMIN_WHITELIST:
        raise HTTPException(status_code=403, detail="Not in admin whitelist")

    try:
        ts = int(ts_str)
    except:
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    now = int(time.time())
    if abs(now - ts) > 60:  # 60초 유효
        raise HTTPException(status_code=401, detail="2FA token expired")

    base = f"{admin_id}:{request.method}:{request.url.path}:{ts}"
    sig_srv = hmac.new(ADMIN_2FA_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_cli, sig_srv):
        raise HTTPException(status_code=401, detail="Invalid 2FA signature")

class ProfileUpsertRequest(BaseModel):
    user_id: int = Field(..., example=111111111111111111)
    character_id: int = Field(..., example=123)
    intro: Optional[str] = Field(None, description="자기소개(최대 300자)")

class LikeRequest(BaseModel):
    viewer_id: int
    target_id: int

class PassRequest(BaseModel):
    viewer_id: int
    target_id: int

class UnmatchRequest(BaseModel):
    user_id: int

class RelayMessageRequest(BaseModel):
    user_id: int
    content: Optional[str] = ""
    attachments: Optional[List[Dict[str, Any]]] = None  # [{filename,size,content_type}]

async def _get_active_match_id(db, user_id: int) -> Optional[int]:
    row = await db.execute("""
      SELECT mm.match_id
      FROM ff_match_members mm
      JOIN ff_matches m ON m.match_id = mm.match_id AND m.is_active = 1
      WHERE mm.user_id = ?
      LIMIT 1
    """, (user_id,))
    return row[0]["match_id"] if row else None

def _bucket_ilvl(value) -> str:
    """아이템레벨 10단위 버킷: 1749.16 -> 1740+, 1751 -> 1750+"""
    try:
        v = float(value)
        base = int(math.floor(v / 10.0) * 10)
        return f"{base}+"
    except Exception:
        return "??+"
def _jsonify_value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, Decimal):
        return float(v)
    return v

def _jsonify_row(row):
    return {k: _jsonify_value(v) for k, v in row.items()} if row else row


async def _safe_rollback(db, context: str) -> None:
    try:
        await db.rollback()
    except Exception:
        logger.exception("Rollback failed (%s)", context)

async def _fetch_next_candidate(db, user_id: int, exclude_user_id: Optional[int] = None):
    """
    우선순위:
      1) 내가 아직 like/pass 안 한 사람
      2) 내가 pass 했던 사람
      3) 내가 view만 했던 사람
    공통 필터: 본인 제외, 비활성 제외, 매칭 중 제외, 차단 제외
    보너스: 직전 노출(exclude_user_id) 1회 제외 → 없으면 제외 해제 재검색
    """

    async def _try_find(exclude: Optional[int]):
        extra_ex = "AND p.user_id <> ?" if exclude is not None else ""
        params: List[Any] = [user_id, user_id, user_id, user_id]
        if exclude is not None:
            params.append(exclude)
        params.append(300)
        rows = await db.execute(
            f"""
            SELECT
                p.user_id,
                p.character_id,
                c.char_name,
                c.item_lvl,
                cl.name AS class_name,
                v.action AS seen_action
            FROM ff_profile p
            JOIN `character` c ON c.id = p.character_id
            LEFT JOIN `class` cl ON cl.id = c.class_id
            LEFT JOIN ff_blocks b1 ON b1.blocker_id = ? AND b1.blocked_id = p.user_id
            LEFT JOIN ff_blocks b2 ON b2.blocker_id = p.user_id AND b2.blocked_id = ?
            LEFT JOIN ff_views v ON v.viewer_id = ? AND v.seen_user_id = p.user_id
            WHERE p.is_active = 1
              AND p.user_id <> ?
              AND NOT EXISTS (
                    SELECT 1
                    FROM ff_match_members mm
                    JOIN ff_matches m ON m.match_id = mm.match_id
                    WHERE mm.user_id = p.user_id
                      AND m.is_active = 1
               )
              AND b1.blocker_id IS NULL
              AND b2.blocker_id IS NULL
              {extra_ex}
              AND (v.action IS NULL OR v.action IN ('pass', 'view'))
            LIMIT ?
            """,
            tuple(params),
        ) or []
        if not rows:
            return None

        random.shuffle(rows)
        new_rows = [r for r in rows if not r.get("seen_action")]
        pass_rows = [r for r in rows if r.get("seen_action") == "pass"]
        view_rows = [r for r in rows if r.get("seen_action") == "view"]
        picked = (new_rows or pass_rows or view_rows)
        if not picked:
            return None
        candidate = dict(picked[0])
        candidate.pop("seen_action", None)
        return candidate

    cand = await _try_find(exclude_user_id)
    if not cand and exclude_user_id is not None:
        cand = await _try_find(None)

    if not cand:
        return None

    await db.execute("""
        INSERT INTO ff_views(viewer_id, seen_user_id, action)
        VALUES(?,?,'view')
        ON DUPLICATE KEY UPDATE action='view', created_at=NOW()
    """, (user_id, cand["user_id"]))

    return _jsonify_row(cand)

@router.post("/profile")
async def upsert_profile(req: ProfileUpsertRequest):
    try:
        async with get_db() as db:
            chk = await db.execute("SELECT id FROM `character` WHERE id = ?", (req.character_id,))
            if not chk:
                raise HTTPException(status_code=404, detail="캐릭터를 찾을 수 없습니다.")

            intro = (req.intro or "").strip()
            if len(intro) > 300:
                intro = intro[:300]

            await db.execute("""
              INSERT INTO ff_profile(user_id, character_id, intro, is_active)
              VALUES(?, ?, ?, 1)
              ON DUPLICATE KEY UPDATE
                character_id = VALUES(character_id),
                intro        = VALUES(intro),
                is_active    = 1
            """, (req.user_id, req.character_id, intro))

            await db.commit()
            return JSONResponse(content={"message": "프로필이 저장되었습니다."})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/profile/{user_id}")
async def get_profile(user_id: int = Path(..., description="Discord 유저 ID")):
    """사용자의 프로필 + 현재 매칭 상태 + 간단 통계"""
    try:
        async with get_db() as db:
            profile_result = await db.execute("""
                SELECT 
                    p.user_id,
                    p.character_id,
                    p.is_active,
                    p.updated_at,
                    p.intro,
                    c.char_name,
                    c.item_lvl,
                    c.combat_power,
                    cl.name  AS class_name,
                    cl.emoji AS class_emoji
                FROM ff_profile p
                LEFT JOIN `character` c ON c.id = p.character_id
                LEFT JOIN `class`     cl ON cl.id = c.class_id
                WHERE p.user_id = ?
            """, (user_id,))

            if not profile_result:
                return JSONResponse(status_code=404, content={"message": "프로필을 찾을 수 없습니다.", "has_profile": False})

            profile = profile_result[0]

            current_match = await db.execute("""
                SELECT mm.match_id, m.created_at AS match_started_at
                FROM ff_match_members mm
                JOIN ff_matches m ON m.match_id = mm.match_id AND m.is_active = 1
                WHERE mm.user_id = ?
                LIMIT 1
            """, (user_id,))

            stats_result = await db.execute("""
                SELECT 
                  (SELECT COUNT(DISTINCT seen_user_id) FROM ff_views WHERE viewer_id = ?) AS viewed_count,
                  (SELECT COUNT(*) FROM ff_views WHERE seen_user_id = ? AND action = 'like') AS received_likes,
                  (SELECT COUNT(*) FROM ff_views WHERE viewer_id   = ? AND action = 'like') AS sent_likes,
                  (SELECT COUNT(DISTINCT mm.match_id) FROM ff_match_members mm WHERE mm.user_id = ?) AS total_matches
            """, (user_id, user_id, user_id, user_id))
            stats = stats_result[0] if stats_result else {"viewed_count": 0, "received_likes": 0, "sent_likes": 0, "total_matches": 0}

            return JSONResponse(content={
                "has_profile": True,
                "profile": {
                    "user_id": profile["user_id"],
                    "character_id": profile["character_id"],
                    "is_active": bool(profile["is_active"]),
                    "updated_at": str(profile.get("updated_at") or ""),
                    "intro": profile.get("intro") or "",
                    "character": {
                        "name": profile["char_name"],
                        "item_level": profile["item_lvl"],
                        "combat_power": profile["combat_power"],
                        "class_name": profile["class_name"],
                        "class_emoji": profile["class_emoji"],
                    }
                },
                "current_match": {
                    "is_matched": bool(current_match),
                    "match_id": current_match[0]["match_id"] if current_match else None,
                    "match_started_at": str(current_match[0]["match_started_at"]) if current_match else None
                },
                "statistics": {
                    "viewed_profiles": stats["viewed_count"],
                    "received_likes":  stats["received_likes"],
                    "sent_likes":      stats["sent_likes"],
                    "total_matches":   stats["total_matches"]
                }
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"프로필 조회 중 오류: {str(e)}")
    
@router.delete("/profile/{user_id}")
async def delete_profile(
    user_id: int = Path(..., description="Discord 유저 ID")
):
    try:
        async with get_db() as db:
            await db.execute("DELETE FROM ff_profile WHERE user_id = ?", (user_id,))
            await db.commit()
            return JSONResponse(content={"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"프로필 삭제 중 오류: {str(e)}")

@router.get("/candidate")
async def next_candidate(user_id: int = Query(...)):
    """
    후보 우선순위 + 직전 노출 1회 제외
    """
    try:
        async with get_db() as db:
            last = await db.execute("""
                SELECT seen_user_id
                FROM ff_views
                WHERE viewer_id=?
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id,))
            exclude_id = last[0]["seen_user_id"] if last else None

            cand = await _fetch_next_candidate(db, user_id, exclude_user_id=exclude_id)
            await db.commit()
            return JSONResponse(content=cand)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/like")
async def like(req: LikeRequest):
    """
    상호 좋아요 시 원자적으로 매치 생성
    + 같은 사람에게는 30분 쿨타임: 쿨 중에는 재전송 불가(스팸 방지), 쿨 지나면 다시 가능
    응답 예)
      {"matched": False, "reason": "cooldown", "retry_after": 1234}  # 초 단위 남은 시간
      {"matched": False, "reason": "awaiting_other_like"}
      {"matched": True,  "match_id": 7}
    """
    COOLDOWN_MINUTES = 30
    db = None
    try:
        async with get_db() as db:
            try:
                await db.execute("START TRANSACTION")
            except Exception:
                logger.exception("Failed to start transaction (like)")

            # 0) 자기 자신 방지
            if req.viewer_id == req.target_id:
                await _safe_rollback(db, "like:self_like_forbidden")
                return JSONResponse(content={"matched": False, "reason": "self_like_forbidden"})

            # 1) 프로필 잠금 & 활성 확인
            me = await db.execute(
                "SELECT user_id, is_active, character_id FROM ff_profile WHERE user_id = ? FOR UPDATE",
                (req.viewer_id,)
            )
            tg = await db.execute(
                "SELECT user_id, is_active, character_id FROM ff_profile WHERE user_id = ? FOR UPDATE",
                (req.target_id,)
            )
            if not me or not tg:
                await _safe_rollback(db, "like:profile_missing")
                return JSONResponse(content={"matched": False, "reason": "profile_missing"})
            if not me[0]["is_active"] or not tg[0]["is_active"]:
                await _safe_rollback(db, "like:inactive_profile")
                return JSONResponse(content={"matched": False, "reason": "inactive_profile"})

            # 2) 현재 "활성 매칭" 여부 (양쪽 모두 검사)
            if await _get_active_match_id(db, req.viewer_id):
                await _safe_rollback(db, "like:viewer_already_matched")
                return JSONResponse(content={"matched": False, "reason": "viewer_already_matched"})
            if await _get_active_match_id(db, req.target_id):
                await _safe_rollback(db, "like:target_already_matched")
                return JSONResponse(content={"matched": False, "reason": "target_already_matched"})

            # 2.5) 같은 대상에게 보낸 '좋아요' 쿨타임 (30분)
            last_like = await db.execute("""
                SELECT created_at
                FROM ff_views
                WHERE viewer_id=? AND seen_user_id=? AND action='like'
                LIMIT 1
            """, (req.viewer_id, req.target_id))
            if last_like:
                # 남은 초 계산 (NOW() 기준 last_like + 30분 까지)
                remain = await db.execute("""
                    SELECT GREATEST(0, TIMESTAMPDIFF(SECOND, NOW(), DATE_ADD(?, INTERVAL ? MINUTE))) AS s
                """, (last_like[0]["created_at"], COOLDOWN_MINUTES))
                remain_s = int(remain[0]["s"]) if remain and remain[0]["s"] is not None else 0
                if remain_s > 0:
                    await _safe_rollback(db, "like:cooldown")
                    return JSONResponse(content={
                        "matched": False,
                        "reason": "cooldown",
                        "retry_after": remain_s
                    })

            # 3) 내 좋아요 기록 (타임스탬프 갱신)
            await db.execute("""
              INSERT INTO ff_views(viewer_id, seen_user_id, action)
              VALUES(?,?,'like')
              ON DUPLICATE KEY UPDATE action='like', created_at=NOW()
            """, (req.viewer_id, req.target_id))

            # 4) 상대가 과거에 이미 나를 좋아요 했는지
            mutual = await db.execute("""
              SELECT 1 FROM ff_views
              WHERE viewer_id=? AND seen_user_id=? AND action='like' LIMIT 1
            """, (req.target_id, req.viewer_id))

            if not mutual:
                # ↳ 아직 상호가 아님 → (지금 타겟은 매칭 상태가 아니므로) DM 알림
                liker = await db.execute("""
                    SELECT c.item_lvl, cl.name AS class_name, c.char_name
                    FROM `character` c
                    LEFT JOIN `class` cl ON cl.id = c.class_id
                    WHERE c.id = ?
                    LIMIT 1
                """, (me[0]["character_id"],))
                def _bucket_ilvl(v):
                    import math
                    try:
                        vv = float(v)
                        base = int(math.floor(vv / 10.0) * 10)
                        return f"{base}+"
                    except Exception:
                        return "??+"

                item_lvl = _bucket_ilvl(liker[0]["item_lvl"]) if liker else "??+"
                class_name = liker[0]["class_name"] if liker else "?"

                embed = {
                    "title": "누군가가 당신에게 **좋아요**를 보냈어요! 💌",
                    "description": (
                        "익명의 누군가가 대화를 원해요!\n"
                        f"**직업**: {class_name}\n"
                        f"**아이템 레벨**: {item_lvl}\n\n"
                        "아래 **[좋아요]**를 눌러 서로 매칭을 맺어보세요."
                    ),
                    "color": 0x5A73FF
                }
                components = [{
                    "type": 1,
                    "components": [{
                        "type": 2,
                        "style": 3,
                        "label": "좋아요",
                        "custom_id": f"ff_like:{req.viewer_id}"
                    }]
                }]

                await db.commit()

                async def _send_like_dm() -> None:
                    try:
                        await discord_service.send_dm(str(req.target_id), embed=embed, components=components)
                    except Exception:
                        logger.exception(
                            "Failed to send like DM (viewer_id=%s, target_id=%s)",
                            req.viewer_id,
                            req.target_id,
                        )

                fire_and_forget(
                    _send_like_dm(),
                    name="friends:like_dm",
                    timeout_sec=10,
                )
                return JSONResponse(content={"matched": False, "reason": "awaiting_other_like"})

            # 5) 상호 좋아요 → 다시 현재 매칭 상태 재확인(경쟁 상태 대비)
            if await _get_active_match_id(db, req.viewer_id):
                await _safe_rollback(db, "like:viewer_already_matched_recheck")
                return JSONResponse(content={"matched": False, "reason": "viewer_already_matched"})
            if await _get_active_match_id(db, req.target_id):
                await _safe_rollback(db, "like:target_already_matched_recheck")
                return JSONResponse(content={"matched": False, "reason": "target_already_matched"})

            # 6) 매치 생성
            await db.execute("INSERT INTO ff_matches(is_active) VALUES(1)")
            mid_row = await db.execute("SELECT LAST_INSERT_ID() AS id")
            mid = int((mid_row or [{}])[0].get("id") or 0)
            await db.execute(
                "INSERT INTO ff_match_members(match_id, user_id) VALUES(?,?), (?,?)",
                (mid, req.viewer_id, mid, req.target_id)
            )

            # 사용된 상호 like 흔적 정리(다른 사용자들의 과거 좋아요는 유지)
            await db.execute("""
                DELETE FROM ff_views 
                WHERE (viewer_id = ? AND seen_user_id = ?)
                   OR (viewer_id = ? AND seen_user_id = ?)
            """, (req.viewer_id, req.target_id, req.target_id, req.viewer_id))

            await db.commit()
            return JSONResponse(content={"matched": True, "match_id": mid})
    except Exception as e:
        try:
            if db:
                await _safe_rollback(db, "like:outer_exception")
        except Exception:
            logger.exception("Unexpected rollback wrapper failure (like)")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pass")
async def pass_(req: PassRequest):
    """
    넘기기 기록 후 바로 다음 후보를 반환
    - 다음 후보는 방금 넘긴 대상(exclude_user_id=target_id)을 1회 제외
    - 후보가 없으면 제외 해제하여 다시 시도
    """
    try:
        async with get_db() as db:
            # pass 기록
            await db.execute("""
              INSERT INTO ff_views(viewer_id, seen_user_id, action)
              VALUES(?,?,'pass') ON DUPLICATE KEY UPDATE action='pass', created_at=NOW()
            """, (req.viewer_id, req.target_id))

            next_cand = await _fetch_next_candidate(db, req.viewer_id, exclude_user_id=req.target_id)
            await db.commit()
            return JSONResponse(content={"message": "ok", "next": next_cand})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/unmatch")
async def unmatch(req: UnmatchRequest):
    """
    현재 매칭 해제 + partner_id 반환
    """
    try:
        async with get_db() as db:
            try:
                await db.execute("START TRANSACTION")
            except Exception:
                logger.exception("Failed to start transaction (unmatch)")

            row = await db.execute("""
              SELECT me.match_id, other.user_id AS partner_id
              FROM ff_match_members me
              JOIN ff_match_members other
                   ON other.match_id = me.match_id AND other.user_id <> me.user_id
              JOIN ff_matches m ON m.match_id = me.match_id AND m.is_active=1
              WHERE me.user_id = ?
              LIMIT 1
            """, (req.user_id,))

            if not row:
                await _safe_rollback(db, "unmatch:no_active_match")
                return JSONResponse(content={"ok": False, "message": "no_active_match"})

            mid = row[0]["match_id"]
            partner_id = row[0]["partner_id"]

            await db.execute(
                "UPDATE ff_matches SET is_active=0, closed_at=NOW(), closed_reason='unmatched_by_user' WHERE match_id=?",
                (mid,)
            )
            await db.execute("DELETE FROM ff_match_members WHERE match_id=?", (mid,))

            await db.commit()
            return JSONResponse(content={"ok": True, "match_id": mid, "partner_id": partner_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/partner")
async def get_partner(user_id: int = Query(...)):
    """
    현재 활성 매칭 파트너 조회
    항상 {"partner_id": int|None, "match_id": int|None} 형태로 응답
    """
    try:
        async with get_db() as db:
            row = await db.execute("""
                SELECT m.match_id, other.user_id AS partner_id
                FROM ff_match_members me
                JOIN ff_match_members other
                     ON other.match_id = me.match_id AND other.user_id <> me.user_id
                JOIN ff_matches m ON m.match_id = me.match_id AND m.is_active = 1
                WHERE me.user_id = ?
                LIMIT 1
            """, (user_id,))
            if not row:
                return JSONResponse(content={"partner_id": None, "match_id": None})
            return JSONResponse(content={
                "partner_id": row[0]["partner_id"],
                "match_id": row[0]["match_id"],
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/relay")
async def relay_message(req: RelayMessageRequest):
    """
    DM 릴레이: 로그 저장 후 partner_id 반환 (실제 전송은 봇이 수행)
    """
    try:
        async with get_db() as db:
            link = await db.execute("""
              SELECT other.user_id AS partner_id, me.match_id
              FROM ff_match_members me
              JOIN ff_match_members other ON other.match_id = me.match_id AND other.user_id <> me.user_id
              JOIN ff_matches m ON m.match_id = me.match_id AND m.is_active = 1
              WHERE me.user_id = ? LIMIT 1
            """, (req.user_id,))
            if not link:
                return JSONResponse(content={"ok": False, "reason": "no_active_match"})
            partner_id = link[0]["partner_id"]; match_id = link[0]["match_id"]

            attachments_json = json.dumps(req.attachments) if req.attachments else "null"

            await db.execute("""
              INSERT INTO ff_messages(match_id, sender_id, content, attachments)
              VALUES(?,?,?,?)
            """, (match_id, req.user_id, req.content or "", attachments_json))
            await db.commit()
            return JSONResponse(content={"ok": True, "partner_id": partner_id, "match_id": match_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/logs/{match_id}")
async def get_logs(request: Request, match_id: int = Path(...), _=Depends(require_admin_2fa)):
    """관리자 로그 열람(2FA 필요)"""
    try:
        async with get_db() as db:
            rows = await db.execute("""
            SELECT created_at, sender_id, content
            FROM ff_messages
            WHERE match_id=?
            ORDER BY created_at ASC
            """, (match_id,)) or []
            rows = [_jsonify_row(r) for r in rows]
            return JSONResponse(content={"data": rows})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
