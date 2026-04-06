"""캐릭터 원정대 관리 API 라우터"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from database.connection import get_db
from typing import Dict, Optional
import urllib.parse
try:
    from services.character_sync import search_lostark_character, save_character_to_db
except Exception:
    from routers.character import search_lostark_character, save_character_to_db

router = APIRouter()

class RegisterBody(BaseModel):
    """캐릭터 등록 요청 모델"""
    char_name: str = Field(..., example="조교병", description="등록할 캐릭터 이름")

async def find_character_by_name(db, char_name: str) -> Optional[Dict]:
    """캐릭터 이름으로 DB 조회"""
    return await db.execute(
        "SELECT id FROM `character` WHERE char_name = ?", 
        (char_name,)
    )

async def is_character_registered(db, user_id: int, char_id: int) -> bool:
    """사용자 원정대에 캐릭터가 등록되어 있는지 확인"""
    result = await db.execute(
        "SELECT 1 FROM `siblings` WHERE user_id = ? AND character_id = ?",
        (user_id, char_id)
    )
    return bool(result)

def create_response(data: Dict, message: str = None, status_code: int = 200) -> JSONResponse:
    """표준 응답 생성"""
    if message:
        data["message"] = message
    return JSONResponse(content=data, status_code=status_code)

@router.get("/{user_id}")
async def list_user_characters(user_id: int):
    """사용자에게 등록된 모든 캐릭터 조회"""
    try:
        async with get_db() as db:
            rows = await db.execute("""
                SELECT
                  c.id AS char_id,
                  c.char_name,
                  c.item_lvl,
                  c.combat_power,
                  cl.name AS class_name,
                  cl.emoji AS class_emoji,
                  c.updated_at
                FROM `siblings` s
                JOIN `character` c ON c.id = s.character_id
                LEFT JOIN class cl ON cl.id = c.class_id
                WHERE s.user_id = ?
                ORDER BY c.updated_at DESC
            """, (user_id,)) or []
            
            return {
                "user_id": str(user_id),
                "characters": rows,
                "count": len(rows)
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/{user_id}/register")
async def register_character(user_id: int, body: RegisterBody):
    """캐릭터 이름으로 사용자에게 캐릭터 등록"""
    try:
        async with get_db() as db:
            # 캐릭터 이름으로 존재 여부 확인
            char_result = await find_character_by_name(db, body.char_name)
            
            char_id = None
            action_details = {}
            
            if char_result:
                # DB에 캐릭터가 존재하는 경우
                char_id = char_result[0]['id']
                action_details = {
                    "character_source": "database",
                    "api_called": False
                }
            else:
                # DB에 캐릭터가 없는 경우 - 로스트아크 API 조회
                decoded_char_name = urllib.parse.unquote(body.char_name)
                
                # 로스트아크 API에서 캐릭터 검색
                lostark_data = await search_lostark_character(decoded_char_name)
                if not lostark_data:
                    raise HTTPException(
                        status_code=404, 
                        detail=f"캐릭터 '{body.char_name}'을 로스트아크에서 찾을 수 없습니다."
                    )
                
                # 캐릭터를 DB에 저장
                saved_character = await save_character_to_db(lostark_data)
                if not saved_character:
                    raise HTTPException(
                        status_code=500,
                        detail="캐릭터 정보를 DB에 저장하는데 실패했습니다."
                    )
                
                char_id = saved_character["id"]
                action_details = {
                    "character_source": "lostark_api", 
                    "api_called": True,
                    "character_data": {
                        "char_name": lostark_data["char_name"],
                        "class_name": lostark_data["class_name"],
                        "item_lvl": lostark_data["item_lvl"],
                        "combat_power": lostark_data["combat_power"]
                    }
                }
            
            # 이미 원정대에 등록된 캐릭터인지 확인
            if await is_character_registered(db, user_id, char_id):
                return {
                    "ok": True,
                    "message": f"Character '{body.char_name}' is already registered to siblings",
                    "char_id": char_id,
                    "action": "already_exists",
                    **action_details
                }
            
            # 원정대에 캐릭터 등록
            await db.execute(
                "INSERT INTO `siblings` (user_id, character_id) VALUES (?, ?)",
                (user_id, char_id)
            )
            await db.commit()

            return {
                "ok": True,
                "message": f"Character '{body.char_name}' successfully registered to siblings",
                "char_id": char_id,
                "action": "registered",
                **action_details
            }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@router.delete("/reset/{user_id}")
async def reset_user_characters(user_id: int):
    try:
        async with get_db() as db:
            existing = await db.execute(
                "SELECT COUNT(1) AS cnt FROM `siblings` WHERE user_id = ?",
                (user_id,)
            )
            cnt = existing[0]["cnt"] if existing else 0
            if cnt == 0:
                return {
                    "ok": True,
                    "message": f"No records for user {user_id}",
                    "user_id": str(user_id),
                    "deleted_count": 0,
                    "action": "reset"
                }
            await db.execute(
                "DELETE FROM `siblings` WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
            return {
                "ok": True,
                "message": f"Deleted {cnt} records for user {user_id}",
                "user_id": str(user_id),
                "deleted_count": cnt,
                "action": "reset"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reset failed: {str(e)}")

@router.delete("/{user_id}/{char_id}")
async def unregister_character(user_id: int, char_id: int):
    """사용자에게 등록된 캐릭터 삭제"""
    try:
        async with get_db() as db:
            # 해당 사용자에게 등록된 캐릭터인지 확인
            if not await is_character_registered(db, user_id, char_id):
                raise HTTPException(
                    status_code=404,
                    detail=f"Character ID {char_id} is not registered to user {user_id}"
                )
            
            # 캐릭터 등록 해제
            await db.execute(
                "DELETE FROM `siblings` WHERE user_id = ? AND character_id = ?",
                (user_id, char_id)
            )
            await db.commit()
        
            return {
                "ok": True,
                "message": f"Character ID {char_id} successfully removed from user {user_id}",
                "char_id": char_id,
                "action": "deleted"
            }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deletion failed: {str(e)}")

@router.get("/{user_id}/count")
async def get_character_count(user_id: int):
    """사용자에게 등록된 캐릭터 수 조회"""
    try:
        async with get_db() as db:
            result = await db.execute(
                "SELECT COUNT(1) as count FROM `siblings` WHERE user_id = ?",
                (user_id,)
            )
            
            count = result[0]['count'] if result else 0
        
            return {
                "user_id": str(user_id),
                "character_count": count
            }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Count query failed: {str(e)}")

@router.get("/{user_id}/stats")
async def get_character_stats(user_id: int):
    """사용자 캐릭터 통계 정보"""
    try:
        async with get_db() as db:
            # 단일 쿼리로 모든 통계 데이터 조회
            stats = await db.execute("""
                SELECT 
                    COUNT(1) as total_characters,
                    AVG(c.item_lvl) as avg_item_level,
                    MAX(c.item_lvl) as max_item_level,
                    MIN(c.item_lvl) as min_item_level,
                    SUM(CASE WHEN c.item_lvl >= 1600 THEN 1 ELSE 0 END) as characters_1600_plus,
                    SUM(CASE WHEN c.item_lvl >= 1500 THEN 1 ELSE 0 END) as characters_1500_plus,
                    COUNT(DISTINCT c.class_id) as unique_classes
                FROM `siblings` s
                JOIN `character` c ON c.id = s.character_id
                WHERE s.user_id = ?
            """, (user_id,))
            
            # 클래스별 캐릭터 수
            class_counts = await db.execute("""
                SELECT 
                    cl.name as class_name, 
                    COUNT(1) as count
                FROM `siblings` s
                JOIN `character` c ON c.id = s.character_id
                JOIN class cl ON cl.id = c.class_id
                WHERE s.user_id = ?
                GROUP BY cl.id
                ORDER BY count DESC
            """, (user_id,))
            
            return {
                "user_id": str(user_id),
                "stats": stats[0] if stats else {},
                "class_distribution": class_counts or []
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stats query failed: {str(e)}")

@router.get("/{user_id}/characters/{char_id}")
async def get_character_detail(user_id: int, char_id: int):
    """특정 캐릭터 상세 정보 조회"""
    try:
        async with get_db() as db:
            # 해당 사용자의 특정 캐릭터 조회
            char = await db.execute("""
                SELECT 
                    c.id AS char_id,
                    c.char_name,
                    NULL AS server_name,
                    c.item_lvl,
                    c.combat_power,
                    cl.id AS class_id,
                    cl.name AS class_name,
                    cl.emoji AS class_emoji,
                    NULL AS created_at,
                    c.updated_at
                FROM `siblings` s
                JOIN `character` c ON c.id = s.character_id
                LEFT JOIN class cl ON cl.id = c.class_id
                WHERE s.user_id = ? AND c.id = ?
            """, (user_id, char_id))
            
            if not char:
                raise HTTPException(
                    status_code=404,
                    detail=f"Character ID {char_id} not found for user {user_id}"
                )
            
            return {
                "user_id": str(user_id),
                "character": char[0]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")
