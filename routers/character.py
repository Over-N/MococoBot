"""캐릭터 관리 API 라우터"""
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Tuple
from database.connection import get_db
from utils.datetime_utils import format_datetime_fields
from services.character_sync import (
    batch_update_discord_posts,
    parse_float,
    save_character_to_db,
    search_lostark_character,
)
import urllib.parse
import logging

router = APIRouter()
logger = logging.getLogger(__name__)
CHARACTER_SELECT_COLUMNS = (
    "c.id, NULL AS user_id, c.class_id, c.char_name, c.item_lvl, c.combat_power, "
    "NULL AS created_at, c.updated_at"
)

class CharacterCreateRequest(BaseModel):
    """캐릭터 생성 요청 모델"""
    user_id: str = Field(..., example="987654321098765432")
    class_name: str = Field(..., example="창술사")
    char_name: str = Field(..., example="조교병")
    item_lvl: float = Field(..., example=1740.0)
    combat_power: float = Field(..., example=3000.0)


class CharacterUpdateRequest(BaseModel):
    """캐릭터 업데이트 요청 모델"""
    char_name: str = Field(..., example="조교병", description="업데이트할 캐릭터 닉네임")
    update_discord: bool = Field(False, example=True, description="Discord 포럼 포스트 업데이트 여부")


class UserEmojiRequest(BaseModel):
    """사용자 이모지 설정 요청 모델"""
    emoji: str = Field(..., example="🔥", description="사용자 커스텀 이모지")


async def get_party_ids_for_character_ids(character_ids: List[int]) -> List[int]:
    if not character_ids:
        return []

    try:
        async with get_db() as db:
            placeholders = ",".join("?" for _ in character_ids)
            rows = await db.execute(f"""
                SELECT DISTINCT p.id
                FROM party p
                JOIN participants pt ON p.id = pt.party_id
                WHERE pt.character_id IN ({placeholders})
                  AND p.thread_manage_id IS NOT NULL
            """, character_ids) or []
            return [r["id"] for r in rows]
    except Exception:
        return []


@router.get("/class")
async def get_all_classes():
    """모든 직업 목록 조회"""
    try:
        async with get_db() as db:
            classes = await db.execute("SELECT name, emoji FROM class ORDER BY name")
            return JSONResponse(content={"data": classes or []})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/user/{user_id}/characters")
async def get_user_characters(user_id: int = Path(...)):
    """사용자 캐릭터 목록 조회"""
    try:
        async with get_db() as db:
            rows = await db.execute("""
                SELECT """ + CHARACTER_SELECT_COLUMNS + """, cl.name AS class_name, cl.emoji AS class_emoji
                FROM siblings s
                JOIN `character` c ON c.id = s.character_id
                LEFT JOIN class cl ON cl.id = c.class_id
                WHERE s.user_id = ?
                ORDER BY c.item_lvl DESC
            """, (int(user_id),))
        return JSONResponse(content={"data": rows or []})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/search/{nickname}")
async def search_character(nickname: str = Path(...)):
    """닉네임으로 캐릭터 검색 (DB + 로스트아크 API) — DB가 24시간 이상이면 API로 갱신 시도"""
    try:
        decoded_nickname = urllib.parse.unquote(nickname)

        async with get_db() as db:
            rows = await db.execute("""
                SELECT 
                    """ + CHARACTER_SELECT_COLUMNS + """, 
                    cl.name  AS class_name, 
                    cl.emoji AS class_emoji,
                    (c.updated_at IS NULL OR c.updated_at < (NOW() - INTERVAL 1 DAY)) AS is_stale
                FROM `character` c
                LEFT JOIN class cl ON c.class_id = cl.id
                WHERE c.char_name = ?
                LIMIT 1
            """, (decoded_nickname,))
            db_character = rows[0] if rows else None

            if db_character:
                if db_character.get("is_stale"):
                    lostark_data = await search_lostark_character(decoded_nickname)
                    if lostark_data:
                        old_item_lvl = parse_float(db_character.get("item_lvl"))
                        old_combat = parse_float(db_character.get("combat_power"))
                        new_item_lvl = parse_float(lostark_data.get("item_lvl"))
                        new_combat = parse_float(lostark_data.get("combat_power"))

                        update_fields = []
                        params = []

                        if new_item_lvl != old_item_lvl:
                            update_fields.append("item_lvl = ?")
                            params.append(new_item_lvl)

                        if new_combat > old_combat:
                            update_fields.append("combat_power = ?")
                            params.append(new_combat)

                        if update_fields:
                            update_fields.append("updated_at = NOW()")
                            params.append(db_character["id"])
                            await db.execute(
                                f"UPDATE `character` SET {', '.join(update_fields)} WHERE id = ?",
                                tuple(params),
                            )
                            await db.commit()

                            rows2 = await db.execute("""
                                SELECT """ + CHARACTER_SELECT_COLUMNS + """, cl.name AS class_name, cl.emoji AS class_emoji
                                FROM `character` c
                                LEFT JOIN class cl ON c.class_id = cl.id
                                WHERE c.id = ?
                                LIMIT 1
                            """, (db_character["id"],))
                            db_character = rows2[0] if rows2 else db_character

                return JSONResponse(content={
                    "data": format_datetime_fields(db_character),
                    "source": "database",
                })

        lostark_data = await search_lostark_character(decoded_nickname)
        if not lostark_data:
            raise HTTPException(status_code=404, detail="캐릭터를 찾을 수 없습니다.")

        saved_character = await save_character_to_db(lostark_data)
        return JSONResponse(content={
            "data": saved_character or {
                "id": 0, "user_id": "", "class_id": 0,
                "char_name": lostark_data["char_name"],
                "item_lvl": lostark_data["item_lvl"],
                "combat_power": lostark_data["combat_power"],
                "class_name": lostark_data["class_name"],
                "class_emoji": "",
            },
            "source": "lostark_api",
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Character search error: {str(e)}")


@router.post("/create")
async def create_character(request: CharacterCreateRequest):
    """캐릭터 수동 생성"""
    try:
        char_data = {
            "char_name": request.char_name,
            "class_name": request.class_name,
            "item_lvl": request.item_lvl,
            "combat_power": request.combat_power,
        }

        saved_character = await save_character_to_db(char_data, request.user_id)
        if not saved_character:
            raise HTTPException(status_code=500, detail="캐릭터 생성에 실패했습니다.")

        return JSONResponse(status_code=201, content={
            "data": saved_character,
            "message": "캐릭터가 성공적으로 생성되었습니다.",
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Character creation error: {str(e)}")


@router.patch("/update")
async def update_character(request: CharacterUpdateRequest = ...):
    """캐릭터 정보 업데이트"""
    try:
        decoded_char_name = urllib.parse.unquote(request.char_name)

        lostark_data = await search_lostark_character(decoded_char_name)
        if not lostark_data:
            raise HTTPException(
                status_code=404,
                detail=f"로스트아크 API에서 '{decoded_char_name}' 캐릭터를 찾을 수 없습니다."
            )

        updated_characters = []
        character_ids: List[int] = []

        async with get_db() as db:
            characters_data = await db.execute("""
                SELECT 
                    c.id, c.class_id, c.char_name, c.item_lvl, c.combat_power, 
                    cl.name as class_name,
                    pu.user_id,
                    new_cl.id as new_class_id
                FROM `character` c
                LEFT JOIN class cl ON c.class_id = cl.id
                LEFT JOIN (
                    SELECT character_id, MIN(user_id) AS user_id
                    FROM participants
                    GROUP BY character_id
                ) pu ON pu.character_id = c.id
                LEFT JOIN class new_cl ON new_cl.name = ?
                WHERE c.char_name = ?
            """, (lostark_data["class_name"], decoded_char_name))

            if not characters_data:
                raise HTTPException(
                    status_code=404,
                    detail=f"'{decoded_char_name}' 캐릭터를 DB에서 찾을 수 없습니다."
                )

            new_class_id = characters_data[0].get("new_class_id")
            if not new_class_id:
                raise HTTPException(
                    status_code=500,
                    detail=f"직업 '{lostark_data['class_name']}'을 찾을 수 없습니다."
                )

            new_item_lvl = parse_float(lostark_data.get("item_lvl"))
            new_combat_power = parse_float(lostark_data.get("combat_power"))

            for old_char in characters_data:
                char_id = old_char["id"]
                character_ids.append(char_id)

                old_item_lvl = parse_float(old_char.get("item_lvl"))
                old_combat = parse_float(old_char.get("combat_power"))
                old_class_name = old_char.get("class_name") or ""

                update_fields = []
                params = []

                if int(old_char.get("class_id") or 0) != int(new_class_id):
                    update_fields.append("class_id = ?")
                    params.append(new_class_id)

                if new_item_lvl != old_item_lvl:
                    update_fields.append("item_lvl = ?")
                    params.append(new_item_lvl)

                if new_combat_power != old_combat:
                    update_fields.append("combat_power = ?")
                    params.append(new_combat_power)

                if update_fields:
                    update_fields.append("updated_at = NOW()")
                    params.append(char_id)
                    await db.execute(
                        f"UPDATE `character` SET {', '.join(update_fields)} WHERE id = ?",
                        tuple(params),
                    )

                updated_characters.append({
                    "id": char_id,
                    "user_id": old_char.get("user_id"),
                    "class_id": new_class_id,
                    "char_name": lostark_data["char_name"],
                    "item_lvl": new_item_lvl,
                    "combat_power": new_combat_power,
                    "class_name": lostark_data["class_name"],
                    "changes": {
                        "item_lvl_changed": new_item_lvl != old_item_lvl,
                        "combat_power_changed": new_combat_power != old_combat,
                        "class_changed": lostark_data["class_name"] != old_class_name,
                    },
                    "old_data": {
                        "item_lvl": old_item_lvl,
                        "combat_power": old_combat,
                        "class_name": old_class_name,
                    },
                })

            await db.commit()

        discord_update_result = None
        if request.update_discord and character_ids:
            party_ids = await get_party_ids_for_character_ids(list(set(character_ids)))
            if party_ids:
                discord_update_result = await batch_update_discord_posts(list(set(party_ids)))

        response_data: Dict[str, Any] = {
            "characters": updated_characters,
            "total_updated": len(updated_characters),
            "char_name": decoded_char_name,
            "source": "lostark_api",
            "message": f"'{decoded_char_name}' 이름의 {len(updated_characters)}개 캐릭터가 성공적으로 업데이트되었습니다.",
        }

        if discord_update_result:
            response_data["discord_update"] = {
                "total_parties": discord_update_result["total"],
                "success_count": len(discord_update_result["success"]),
                "failed_count": len(discord_update_result["failed"]),
                "details": discord_update_result,
            }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Character update error: {str(e)}")

@router.delete("/{character_id}")
async def delete_character(character_id: int = Path(...)):
    """캐릭터 삭제"""
    try:
        async with get_db() as db:
            if not await db.execute("SELECT id FROM `character` WHERE id = ?", (character_id,)):
                raise HTTPException(status_code=404, detail="캐릭터를 찾을 수 없습니다.")

            await db.execute("DELETE FROM `character` WHERE id = ?", (character_id,))
            await db.commit()

            return JSONResponse(content={
                "character_id": character_id,
                "deleted": True,
                "message": "캐릭터가 성공적으로 삭제되었습니다.",
            })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Character deletion error: {str(e)}")
