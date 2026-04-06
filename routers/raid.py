"""레이드 정보 관리 라우터"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from database.connection import get_db

router = APIRouter()
RAID_SELECT_COLUMNS = "id, name, difficulty, min_lvl, dealer, supporter, urls"

@router.get("/")
async def get_raids():
    """모든 레이드 정보 조회"""
    try:
        async with get_db() as db:
            raids = await db.execute(f"SELECT {RAID_SELECT_COLUMNS} FROM raid ORDER BY id")
            
            return JSONResponse(content={
                "message": "success" if raids else "No raids found",
                "data": raids or []
            })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

