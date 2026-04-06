from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from database.connection import get_db

router = APIRouter()

# ===============================
# Pydantic Models
# ===============================
class TTSChannelRequest(BaseModel):
    """TTS 채널 설정 요청 모델"""
    tts_channel_id: int = Field(..., description="TTS 채널 ID", example=1234567890123456789, gt=0)

# ===============================
# TTS 엔진 기본 설정
# ===============================
# 사용할 수 있는 TTS 엔진 식별자 목록
# engine1: NaverTTS (기본)
# engine2: Microsoft Edge TTS (ko-KR-SunHiNeural)
# engine3: Microsoft Edge TTS (ko-KR-InJoonNeural)
VALID_ENGINE_IDS = {"engine1", "engine2", "engine3", "engine4", "engine5", "engine7", "engine9"}

# ===============================
# API Endpoints
# ===============================
@router.post("/{guild_id}")
async def save_tts_channel(guild_id: int = Path(...), request: TTSChannelRequest = ...):
    """TTS 채널 설정 저장"""
    try:
        async with get_db() as db:
            # 기존 서버 설정 확인
            existing_server = await db.execute("SELECT id FROM server WHERE guild_id = ?", (guild_id,))
            
            if existing_server:
                # 기존 설정 업데이트
                await db.execute("""
                    UPDATE server SET tts_channel_id = ?
                    WHERE guild_id = ?
                """, (request.tts_channel_id, guild_id))
                message = "TTS channel updated successfully"
            else:
                # 새 설정 생성
                await db.execute("""
                    INSERT INTO server (guild_id, tts_channel_id)
                    VALUES (?, ?)
                """, (guild_id, request.tts_channel_id))
                message = "TTS channel created successfully"
            
            await db.commit()
            
            return JSONResponse(content={
                "message": message,
                "guild_id": guild_id,
                "tts_channel_id": request.tts_channel_id
            })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.patch("/{guild_id}")
async def update_tts_channel(guild_id: int = Path(...), request: TTSChannelRequest = ...):
    """TTS 채널 설정 수정"""
    try:
        async with get_db() as db:
            # 서버 설정 존재 확인
            existing_server = await db.execute("SELECT id FROM server WHERE guild_id = ?", (guild_id,))
            
            if not existing_server:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Server not found",
                        "guild_id": guild_id,
                        "detail": f"Guild ID {guild_id}에 대한 서버 설정이 존재하지 않습니다."
                    }
                )
            
            # TTS 채널 설정 업데이트
            await db.execute("""
                UPDATE server SET tts_channel_id = ?
                WHERE guild_id = ?
            """, (request.tts_channel_id, guild_id))
            await db.commit()
            
            return JSONResponse(content={
                "message": "TTS channel updated successfully",
                "guild_id": guild_id,
                "tts_channel_id": request.tts_channel_id
            })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/list")
async def get_all_tts_channels():
    """모든 서버의 TTS 채널 설정 조회"""
    try:
        async with get_db() as db:
            servers = await db.execute("""
                SELECT guild_id, tts_channel_id
                FROM server 
                WHERE tts_channel_id IS NOT NULL
                ORDER BY guild_id
            """)
            
            return JSONResponse(content={
                "message": "success" if servers else "No TTS channels found",
                "data": servers or []
            })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/{guild_id}")
async def get_tts_channel(guild_id: int = Path(...)):
    """특정 서버의 TTS 채널 설정 조회"""
    try:
        async with get_db() as db:
            server_result = await db.execute("""
                SELECT guild_id, tts_channel_id
                FROM server 
                WHERE guild_id = ?
            """, (guild_id,))
            
            if not server_result:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Server not found",
                        "guild_id": guild_id,
                        "detail": f"Guild ID {guild_id}에 대한 서버 설정이 존재하지 않습니다."
                    }
                )
            
            return JSONResponse(content={
                "message": "success",
                "data": server_result[0]
            })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.delete("/{guild_id}")
async def delete_tts_channel(guild_id: int = Path(...)):
    """TTS 채널 설정 삭제"""
    try:
        async with get_db() as db:
            # 서버 설정 존재 확인
            existing_server = await db.execute("SELECT id, tts_channel_id FROM server WHERE guild_id = ?", (guild_id,))
            
            if not existing_server or not existing_server[0]['tts_channel_id']:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "TTS channel not found",
                        "guild_id": guild_id,
                        "detail": f"Guild ID {guild_id}에 대한 TTS 채널 설정이 존재하지 않습니다."
                    }
                )
            
            # TTS 채널 설정만 삭제 (NULL로 설정)
            await db.execute("""
                UPDATE server SET tts_channel_id = NULL
                WHERE guild_id = ?
            """, (guild_id,))
            await db.commit()
            
            return JSONResponse(content={
                "message": "TTS channel deleted successfully",
                "guild_id": guild_id,
                "deleted": True
            })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/engine/all")
async def list_tts_engines():
    try:
        async with get_db() as db:
            rows = await db.execute("SELECT user_id, engine_id FROM user_tts_engine") or []
            data: dict[str, str] = {}
            for row in rows:
                try:
                    uid = row.get("user_id")
                    eid = row.get("engine_id")
                    if uid is not None and eid:
                        data[str(uid)] = str(eid)
                except Exception:
                    continue
            message = "success" if data else "No engine settings found"
            return JSONResponse(content={"message": message, "data": data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/engine/{user_id}")
async def get_tts_engine(user_id: int = Path(...)):
    """
    특정 사용자의 TTS 엔진 설정을 조회
    """
    try:
        async with get_db() as db:
            result = await db.execute(
                "SELECT engine_id FROM user_tts_engine WHERE user_id = ?",
                (user_id,)
            )
            if not result:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Engine not found",
                        "user_id": user_id,
                        "detail": f"사용자 {user_id}에 대한 엔진 설정이 존재하지 않습니다."
                    }
                )
            engine_id = result[0].get("engine_id") if result else None
            return JSONResponse(content={
                "message": "success",
                "data": {
                    "user_id": user_id,
                    "engine_id": engine_id
                }
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/engine/{user_id}/{engine_id}")
async def set_tts_engine(user_id: int = Path(...), engine_id: str = Path(...)):
    """
    특정 사용자의 TTS 엔진을 설정합니다.

    `engine_id`는 engine1, engine2, engine3 중 하나여야 합니다. 다른 값이 제공되면 400 오류를 반환합니다.
    값이 존재하면 업데이트하고, 존재하지 않으면 새로 삽입합니다.
    """
    if engine_id not in VALID_ENGINE_IDS:
        return JSONResponse(
            status_code=400,
            content={
                "message": "Invalid engine_id",
                "engine_id": engine_id,
                "detail": f"유효한 엔진은 {sorted(VALID_ENGINE_IDS)} 입니다."
            }
        )
    try:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO user_tts_engine (user_id, engine_id)
                VALUES (?, ?)
                ON DUPLICATE KEY UPDATE engine_id = VALUES(engine_id)
                """,
                (user_id, engine_id)
            )
            await db.commit()
            return JSONResponse(content={
                "message": "Engine set successfully",
                "user_id": user_id,
                "engine_id": engine_id
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")