from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database.connection import get_db
from utils.value_utils import to_bool_out

router = APIRouter()

SERVER_SELECT_COLUMNS = (
    "id, guild_id, forum_channel_id, chat_channel_id, admin_roles, "
    "cancel_join_channel_id, mention_role_id, alert_timer, alert_start"
)


class ServerConfigRequest(BaseModel):
    forum_channel_id: Optional[int] = Field(None, description="포럼 채널 ID", examples=[1234567890123456789], gt=0)
    chat_channel_id: Optional[int] = Field(None, description="채팅 채널 ID", examples=[9876543210987654321], gt=0)
    admin_roles: Optional[str] = Field(None, description="관리자 역할 ID", examples=["123456789"])
    cancel_join_channel_id: Optional[int] = Field(None, description="참가 취소 채널 ID", examples=[1111111111111111111], gt=0)
    mention_role_id: Optional[int] = Field(None, description="기본 멘션 역할 ID", examples=[2222222222222222222], gt=0)
    alert_timer_mode: Optional[int] = Field(None, ge=0, le=3, description="0=OFF, 1=10m, 2=1h, 3=both")
    alert_timer: Optional[bool] = Field(None, description="(deprecated) True->3, False->0")
    alert_start: Optional[bool] = Field(None, description="시작 알림 활성화 여부")


def _dump_incoming(model: BaseModel) -> tuple[Dict[str, Any], Set[str]]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True), set(getattr(model, "model_fields_set", set()))
    return model.dict(exclude_unset=True), set(getattr(model, "__fields_set__", set()))


def _format_channel_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _prepare_update_query(incoming: Dict[str, Any], fields_set: Set[str]) -> tuple[list[str], list[Any]]:
    update_fields: list[str] = []
    update_values: list[Any] = []

    for field in ("forum_channel_id", "chat_channel_id", "admin_roles", "cancel_join_channel_id", "mention_role_id"):
        if field in fields_set:
            value = incoming.get(field)
            update_fields.append(f"{field} = ?")
            update_values.append(value if field == "admin_roles" or value is None else str(value))

    if "alert_timer_mode" in fields_set and incoming.get("alert_timer_mode") is not None:
        mode = int(incoming["alert_timer_mode"]) & 0b11
        update_fields.append("alert_timer = ?")
        update_values.append(mode)
    elif "alert_timer" in fields_set and incoming.get("alert_timer") is not None:
        mode = 3 if incoming["alert_timer"] else 0
        update_fields.append("alert_timer = ?")
        update_values.append(mode)

    if "alert_start" in fields_set and incoming.get("alert_start") is not None:
        update_fields.append("alert_start = ?")
        update_values.append(1 if incoming["alert_start"] else 0)

    return update_fields, update_values


def _format_server_response(server_data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        mode = int(server_data.get("alert_timer") or 0) & 0b11
    except (TypeError, ValueError):
        mode = 0

    return {
        "id": server_data.get("id"),
        "guild_id": server_data.get("guild_id"),
        "forum_channel_id": _format_channel_id(server_data.get("forum_channel_id")),
        "chat_channel_id": _format_channel_id(server_data.get("chat_channel_id")),
        "admin_roles": server_data.get("admin_roles"),
        "cancel_join_channel_id": _format_channel_id(server_data.get("cancel_join_channel_id")),
        "mention_role_id": _format_channel_id(server_data.get("mention_role_id")),
        "alert_timer": mode != 0,
        "alert_timer_mode": mode,
        "alert_start": to_bool_out(server_data.get("alert_start")),
    }


@router.post("/server/{guild_id}")
async def save_server_config(
    guild_id: int = Path(..., description="Discord Guild ID", examples=[123456789012345678], gt=0),
    config: ServerConfigRequest = ...,
):
    try:
        async with get_db() as db:
            existing_server = await db.fetch_one(
                "SELECT id FROM server WHERE guild_id = ?",
                (str(guild_id),),
            )

            if existing_server:
                incoming, fields_set = _dump_incoming(config)
                update_fields, update_values = _prepare_update_query(incoming, fields_set)

                if update_fields:
                    update_values.append(str(guild_id))
                    query = f"UPDATE server SET {', '.join(update_fields)} WHERE guild_id = ?"
                    await db.execute(query, tuple(update_values))

                message = "Server configuration updated successfully"
            else:
                mode = 3
                if config.alert_timer_mode is not None:
                    mode = int(config.alert_timer_mode) & 0b11
                elif config.alert_timer is not None:
                    mode = 3 if config.alert_timer else 0

                await db.execute(
                    """
                    INSERT INTO server (
                        guild_id, forum_channel_id, chat_channel_id, admin_roles,
                        cancel_join_channel_id, mention_role_id, alert_timer, alert_start
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(guild_id),
                        str(config.forum_channel_id) if config.forum_channel_id is not None else None,
                        str(config.chat_channel_id) if config.chat_channel_id is not None else None,
                        config.admin_roles,
                        str(config.cancel_join_channel_id) if config.cancel_join_channel_id is not None else None,
                        str(config.mention_role_id) if config.mention_role_id is not None else None,
                        mode,
                        1 if (config.alert_start if config.alert_start is not None else True) else 0,
                    ),
                )
                message = "Server configuration created successfully"

            await db.commit()

            server_data = await db.fetch_one(
                f"SELECT {SERVER_SELECT_COLUMNS} FROM server WHERE guild_id = ?",
                (str(guild_id),),
            )
            if not server_data:
                raise HTTPException(status_code=500, detail="Database error: saved server config could not be reloaded")

            response = _format_server_response(server_data)
            response["message"] = message
            return JSONResponse(content=response)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/server/{guild_id}")
async def get_server_config(
    guild_id: int = Path(..., description="Discord Guild ID", examples=[123456789012345678], gt=0),
):
    try:
        async with get_db() as db:
            server_data = await db.fetch_one(
                f"SELECT {SERVER_SELECT_COLUMNS} FROM server WHERE guild_id = ?",
                (str(guild_id),),
            )

            if not server_data:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Server not found",
                        "guild_id": str(guild_id),
                        "detail": f"Guild ID {guild_id}에 대한 서버 설정이 존재하지 않습니다.",
                    },
                )

            return JSONResponse(content=_format_server_response(server_data))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.delete("/server/{guild_id}")
async def delete_server_config(
    guild_id: int = Path(..., description="Discord Guild ID", examples=[123456789012345678], gt=0),
):
    try:
        async with get_db() as db:
            await db.execute("DELETE FROM server WHERE guild_id = ?", (str(guild_id),))
            affected_rows = db.rowcount

            if affected_rows == 0:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Server not found",
                        "guild_id": str(guild_id),
                        "detail": f"Guild ID {guild_id}에 대한 서버 설정이 존재하지 않습니다.",
                    },
                )

            await db.commit()

            return JSONResponse(
                content={
                    "message": "Server configuration deleted successfully",
                    "guild_id": str(guild_id),
                    "deleted": True,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")