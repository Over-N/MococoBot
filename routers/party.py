from fastapi import APIRouter, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from services.party_service import party_service
from utils.datetime_utils import format_datetime_fields
from database.connection import get_db, DatabaseManager
import asyncio
import logging
from utils.task_utils import fire_and_forget

router = APIRouter()


class PartyCreateRequest(BaseModel):
    title: str = Field(..., example="발탄 노말 모집")
    guild_id: str = Field(..., example="123456789012345678")
    raid_name: str = Field(..., example="발탄")
    difficulty: str = Field(..., example="노말")
    start_date: Optional[str] = Field(None, example="25.01.15 20:00")
    owner_id: Optional[int] = Field(None, example=987654321098765432)
    message: Optional[str] = Field(None, example="발탄 노말 레이드 파티 모집합니다!")


class PartyEditRequest(BaseModel):
    title: Optional[str] = Field(None, example="발탄 노말 모집")
    guild_id: Optional[str] = Field(None, example="123456789012345678")
    raid_name: Optional[str] = Field(None, example="발탄")
    difficulty: Optional[str] = Field(None, example="노말")
    start_date: Optional[str] = Field(None, example="25.01.15 20:00")
    owner_id: Optional[int] = Field(None, example=987654321098765432)
    message: Optional[str] = Field(None, example="발탄 노말 레이드 파티 모집합니다!")


class PartyJoinRequest(BaseModel):
    character_id: int = Field(..., example=1)
    user_id: str = Field(..., example="987654321098765432")
    role: Optional[int] = Field(0, example=0)


class PartyStatusUpdateRequest(BaseModel):
    is_dealer_closed: bool = Field(..., example=False)
    is_supporter_closed: bool = Field(..., example=True)


class WaitlistCancelRequest(BaseModel):
    user_id: str = Field(..., example="987654321098765432")
    role: Optional[int] = Field(0, example=0)


async def get_party_with_raid_info(db: DatabaseManager, party_id: int) -> Dict[str, Any]:
    party = await db.execute("""
        SELECT
            p.id, p.title, p.guild_id, p.raid_id, p.start_date, p.owner, p.message,
            p.thread_manage_id, p.is_dealer_closed, p.is_supporter_closed, NULL AS created_at, NULL AS updated_at,
            r.name AS raid_name, r.difficulty, r.min_lvl, r.dealer, r.supporter
        FROM party p
        LEFT JOIN raid r ON p.raid_id = r.id
        WHERE p.id = ?
        LIMIT 1
    """, (party_id,))
    if not party:
        raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")
    return party[0]


@router.post("/{guild_id}/create")
async def create_party(guild_id: int = Path(...), request: PartyCreateRequest = ...):
    try:
        party_data = {
            "title": request.title,
            "raid_name": request.raid_name,
            "difficulty": request.difficulty,
            "start_date": request.start_date,
            "owner_id": request.owner_id,
            "message": request.message,
        }
        result = await party_service.create_party(guild_id, party_data)
        return JSONResponse(status_code=201, content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{party_id}/edit")
async def edit_party(party_id: int = Path(...), request: PartyEditRequest = ...):
    try:
        async with get_db() as db:
            existing_party = await db.execute("""
                SELECT p.title, p.guild_id, r.name AS raid_name, r.difficulty,
                       p.start_date, p.owner AS owner_id, p.message
                FROM party p
                JOIN raid r ON p.raid_id = r.id
                WHERE p.id = ?
                LIMIT 1
            """, (party_id,))
            if not existing_party:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")
            existing = existing_party[0]

        party_data = {
            "title": request.title if request.title is not None else existing["title"],
            "guild_id": request.guild_id if request.guild_id is not None else existing["guild_id"],
            "raid_name": request.raid_name if request.raid_name is not None else existing["raid_name"],
            "difficulty": request.difficulty if request.difficulty is not None else existing["difficulty"],
            "start_date": request.start_date if request.start_date is not None else existing["start_date"],
            "owner_id": request.owner_id if request.owner_id is not None else existing["owner_id"],
            "message": request.message if request.message is not None else existing["message"],
        }

        result = await party_service.update_party(party_id, party_data)
        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{party_id}/status")
async def update_party_status(party_id: int = Path(...), request: PartyStatusUpdateRequest = ...):
    try:
        async with get_db() as db:
            await db.execute("""
                UPDATE party
                SET is_dealer_closed = ?, is_supporter_closed = ?
                WHERE id = ?
            """, (int(request.is_dealer_closed), int(request.is_supporter_closed), party_id))

            affected = await db.execute("SELECT ROW_COUNT() AS affected")
            if not affected or int(affected[0].get("affected") or 0) == 0:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")

            await db.commit()

        fire_and_forget(
            party_service.update_discord_after_change(party_id),
            name="party:update_discord_after_status",
            timeout_sec=20,
            coalesce_key=f"party:discord_update:{party_id}",
        )

        return JSONResponse(content={
            "party_id": party_id,
            "is_dealer_closed": request.is_dealer_closed,
            "is_supporter_closed": request.is_supporter_closed,
            "updated": True
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"상태 업데이트 오류: {str(e)}")


@router.get("/list")
async def get_party_list(guild_id: Optional[int] = Query(None)):
    try:
        async with get_db() as db:
            query = """
                SELECT p.id, p.title, p.start_date, p.guild_id, p.owner, p.message,
                       p.thread_manage_id, p.is_dealer_closed, p.is_supporter_closed,
                       r.name AS raid_name, r.difficulty, r.min_lvl, r.dealer, r.supporter
                FROM party p
                LEFT JOIN raid r ON p.raid_id = r.id
            """
            params = []

            if guild_id:
                query += " WHERE p.guild_id = ? "
                params.append(str(guild_id))

            query += " ORDER BY p.start_date ASC"
            parties = await db.execute(query, tuple(params) if params else None)

            result_data = []
            for p in (parties or []):
                pid = p["id"]
                item = dict(p)
                item["participants"] = await party_service.get_participants_data(pid)
                result_data.append(format_datetime_fields(item))

            return JSONResponse(content={"data": result_data})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{party_id}/join")
async def join_party(party_id: int = Path(...), request: PartyJoinRequest = ...):
    try:
        result = await party_service.join_party(
            party_id, request.character_id, request.user_id, request.role
        )
        if "message" in result:
            sc = int(result.get("status_code") or 200)
            return JSONResponse(status_code=sc, content=result)
        return JSONResponse(status_code=int(result.get("status_code") or 201), content=result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{party_id}/participants")
async def get_party_participants(party_id: int = Path(...)):
    try:
        async with get_db() as db:
            exists = await db.execute("SELECT 1 FROM party WHERE id = ? LIMIT 1", (party_id,))
            if not exists:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")
        participants_data = await party_service.get_participants_data(party_id)
        return JSONResponse(content={"data": participants_data})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{party_id}/delete")
async def delete_party(party_id: int = Path(...)):
    try:
        result = await party_service.delete_party(party_id)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{party_id}/participants/{participant_id}/kick")
async def kick_participant(party_id: int = Path(...), participant_id: int = Path(...)):
    try:
        result = await party_service.leave_party(party_id, participant_id=participant_id)
        return JSONResponse(status_code=int(result.get("status_code") or 200), content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{party_id}/participants/{user_id}")
async def leave_party(party_id: int = Path(...), user_id: str = Path(...)):
    try:
        result = await party_service.leave_party(party_id, user_id=user_id)
        return JSONResponse(status_code=int(result.get("status_code") or 200), content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/guilds/{guild_id}/participants/{user_id}")
async def purge_user_participations_in_guild(guild_id: str = Path(...), user_id: str = Path(...)):
    try:
        result = await party_service.purge_user_participations_in_guild(guild_id=guild_id, user_id=user_id)
        if result is None:
            return Response(status_code=204)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{thread_id}/thread")
async def get_party_by_thread_id(thread_id: int = Path(...)):
    try:
        async with get_db() as db:
            party_result = await db.execute("""
                SELECT id, title, thread_manage_id, guild_id, owner
                FROM party
                WHERE thread_manage_id = ?
                LIMIT 1
            """, (thread_id,))
            if not party_result:
                raise HTTPException(status_code=404, detail="해당 스레드 ID로 파티를 찾을 수 없습니다.")
            return JSONResponse(content=party_result[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{party_id}")
async def get_party_detail(party_id: int = Path(...)):
    try:
        async with get_db() as db:
            party = await get_party_with_raid_info(db, party_id)
        party["participants"] = await party_service.get_participants_data(party_id)
        return JSONResponse(content={"data": format_datetime_fields(party)})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파티 조회 오류: {str(e)}")


@router.get("/byraid/{raid_id}")
async def get_parties_by_raid(raid_id: int = Path(...)):
    try:
        async with get_db() as db:
            parties = await db.execute("""
                SELECT
                    p.id, p.title, p.start_date, p.guild_id, p.owner, p.thread_manage_id,
                    r.name AS raid_name, r.difficulty, r.dealer, r.supporter,
                    SUM(CASE WHEN pt.role = 0 THEN 1 ELSE 0 END) AS dealer_count,
                    SUM(CASE WHEN pt.role = 1 THEN 1 ELSE 0 END) AS supporter_count
                FROM party p
                LEFT JOIN raid r ON p.raid_id = r.id
                LEFT JOIN participants pt ON pt.party_id = p.id
                WHERE p.raid_id = ?
                GROUP BY p.id
                ORDER BY p.start_date ASC
            """, (raid_id,))

            result = []
            for party in parties or []:
                formatted = format_datetime_fields(party)
                formatted["participant_counts"] = {
                    "dealer_count": int(party.get("dealer_count") or 0),
                    "supporter_count": int(party.get("supporter_count") or 0),
                }
                result.append(formatted)

            return JSONResponse(content={"data": result})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"레이드 파티 조회 오류: {str(e)}")


@router.get("/user/{user_id}")
async def get_user_parties(user_id: int = Path(...)):
    try:
        async with get_db() as db:
            parties = await db.execute("""
                SELECT p.id, p.title, p.start_date, p.guild_id, p.owner, p.thread_manage_id,
                       r.name AS raid_name, r.difficulty, r.dealer, r.supporter,
                       pt.role AS user_role
                FROM party p
                LEFT JOIN raid r ON p.raid_id = r.id
                JOIN participants pt ON p.id = pt.party_id AND pt.user_id = ?
                ORDER BY p.start_date ASC
            """, (str(user_id),))

            result = [format_datetime_fields(party) for party in parties or []]
            return JSONResponse(content={"data": result})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"사용자 파티 조회 오류: {str(e)}")


@router.get("/count")
async def get_party_counts(guild_id: Optional[int] = Query(None)):
    try:
        async with get_db() as db:
            where_main = "WHERE p.guild_id = ?" if guild_id else ""
            where_p2 = "WHERE p2.guild_id = ?" if guild_id else ""
            where_p3 = "WHERE p3.guild_id = ?" if guild_id else ""
            params = [str(guild_id)] if guild_id else []

            stats = await db.execute(f"""
                SELECT
                    COUNT(*) AS total_parties,
                    SUM(CASE WHEN p.start_date >= NOW() THEN 1 ELSE 0 END) AS upcoming_parties,
                    SUM(CASE WHEN p.start_date < NOW() THEN 1 ELSE 0 END) AS past_parties,
                    (
                        SELECT COUNT(DISTINCT r.id)
                        FROM raid r
                        JOIN party p2 ON r.id = p2.raid_id
                        {where_p2}
                    ) AS unique_raids,
                    (
                        SELECT COUNT(DISTINCT pt.user_id)
                        FROM participants pt
                        JOIN party p3 ON pt.party_id = p3.id
                        {where_p3}
                    ) AS total_participants
                FROM party p
                {where_main}
            """, params * 3 if guild_id else [])

            return JSONResponse(content=stats[0] if stats else {"total_parties": 0})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파티 통계 조회 오류: {str(e)}")


@router.post("/public/{party_id}")
async def toggle_party_public(party_id: int = Path(...)):
    try:
        async with get_db() as db:
            await db.execute("""
                UPDATE party
                SET is_active = IF(IFNULL(is_active, 0) = 1, 0, 1)
                WHERE id = ?
            """, (party_id,))

            affected = await db.execute("SELECT ROW_COUNT() AS affected")
            if not affected or int(affected[0].get("affected") or 0) == 0:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")

            row = await db.execute("SELECT is_active FROM party WHERE id = ? LIMIT 1", (party_id,))
            new_state = int(row[0].get("is_active") or 0) if row else 0

            await db.commit()

        fire_and_forget(
            party_service.update_discord_after_change(party_id),
            name="party:update_discord_after_public_toggle",
            timeout_sec=20,
            coalesce_key=f"party:discord_update:{party_id}",
        )

        return JSONResponse(content={"party_id": party_id, "is_active": new_state})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"공개 상태 변경 오류: {str(e)}")


@router.post("/{party_id}/toggle/{role}")
async def toggle_party_recruit_status(
    party_id: int = Path(...),
    role: int = Path(..., description="0: 딜러 모집 토글, 1: 서포터 모집 토글"),
):
    try:
        if role not in (0, 1):
            raise HTTPException(status_code=400, detail="role 값은 0(딜러) 또는 1(서포터)만 허용됩니다.")

        async with get_db() as db:
            if role == 0:
                await db.execute("""
                    UPDATE party
                    SET is_dealer_closed = IF(IFNULL(is_dealer_closed, 0) = 1, 0, 1)
                    WHERE id = ?
                """, (party_id,))
            else:
                await db.execute("""
                    UPDATE party
                    SET is_supporter_closed = IF(IFNULL(is_supporter_closed, 0) = 1, 0, 1)
                    WHERE id = ?
                """, (party_id,))

            affected = await db.execute("SELECT ROW_COUNT() AS affected")
            if not affected or int(affected[0].get("affected") or 0) == 0:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")

            row = await db.execute("""
                SELECT is_dealer_closed, is_supporter_closed
                FROM party
                WHERE id = ?
                LIMIT 1
            """, (party_id,))
            if not row:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다.")

            dealer_closed = int(row[0].get("is_dealer_closed") or 0)
            supporter_closed = int(row[0].get("is_supporter_closed") or 0)

            if role == 0:
                message = "딜러 모집을 재개합니다." if dealer_closed == 0 else "딜러 모집을 종료합니다."
            else:
                message = "서포터 모집을 재개합니다." if supporter_closed == 0 else "서포터 모집을 종료합니다."

            await db.commit()

        fire_and_forget(
            party_service.update_discord_after_change(party_id),
            name="party:update_discord_after_role_toggle",
            timeout_sec=20,
            coalesce_key=f"party:discord_update:{party_id}",
        )

        return JSONResponse(content={
            "party_id": party_id,
            "role": "dealer" if role == 0 else "supporter",
            "is_dealer_closed": dealer_closed,
            "is_supporter_closed": supporter_closed,
            "message": message,
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"모집 상태 토글 오류: {str(e)}")


@router.get("/{party_id}/waitlist")
async def get_party_waitlist(party_id: int = Path(...), role: Optional[int] = Query(None)):
    try:
        data = await party_service.get_waitlist(party_id, role=role)
        return JSONResponse(content={"data": data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{party_id}/waitlist/me")
async def get_my_waitlist_position(
    party_id: int = Path(...),
    user_id: str = Query(...),
    role: int = Query(0),
):
    try:
        data = await party_service.get_waitlist_my_position(party_id, user_id=user_id, role=role)
        return JSONResponse(content={"data": data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{party_id}/waitlist")
async def cancel_waitlist(party_id: int = Path(...), request: WaitlistCancelRequest = ...):
    try:
        result = await party_service.cancel_waitlist(party_id, request.user_id, request.role)
        return JSONResponse(status_code=int(result.get("status_code") or 200), content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
