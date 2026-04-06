from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database.connection import get_db
from utils.value_utils import to_bool_out
from services.stove_profile_link import (
    StoveProfileError,
    LOSTARK_UNAVAILABLE_DETAIL,
    build_top_characters,
    fetch_lostark_siblings_by_character_name,
    fetch_stove_status_message,
    normalize_stove_profile_id_digits_only,
    resolve_stove_profile,
)

try:
    from services.character_sync import search_lostark_character
except Exception:
    from routers.character import search_lostark_character


router = APIRouter()

NONCE_TTL_SEC = max(60, int(os.getenv("VERIFY_STOVE_NONCE_TTL_SEC", "300")))
NONCE_PEPPER = os.getenv("VERIFY_STOVE_NONCE_PEPPER", "")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _is_lostark_unavailable_message(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    keywords = (
        LOSTARK_UNAVAILABLE_DETAIL,
        "lost ark open api request failed",
        "lostark open api request failed",
        "developer-lostark.game.onstove.com",
        "service unavailable",
        "temporarily unavailable",
        "bad gateway",
        "gateway timeout",
        "maintenance",
        "점검",
        "로스트아크 서버",
        "로스트아크 정기점검",
    )
    return any(keyword in text for keyword in keywords)


def _is_lostark_unavailable_exception(exc: Exception) -> bool:
    if isinstance(exc, StoveProfileError):
        if exc.status_code == 503:
            return True
        return _is_lostark_unavailable_message(exc.message)
    if isinstance(exc, HTTPException):
        if exc.status_code in {502, 503, 504}:
            return True
        return _is_lostark_unavailable_message(exc.detail)
    return _is_lostark_unavailable_message(exc)


def _raise_lostark_unavailable_http() -> None:
    raise HTTPException(status_code=503, detail=LOSTARK_UNAVAILABLE_DETAIL)


def _dump_incoming(model: BaseModel) -> Tuple[dict, Set[str]]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True), getattr(model, "model_fields_set", set())
    return model.dict(exclude_unset=True), getattr(model, "__fields_set__", set())


def _format_config_response(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": config.get("id"),
        "guild_id": config.get("guild_id"),
        "basic_role_id": config.get("basic_role_id"),
        "auto_nickname": to_bool_out(config.get("auto_nickname")),
        "search_nickname": to_bool_out(config.get("search_nickname")),
        "nickname_mode": config.get("nickname_mode"),
        "guild_name": config.get("guild_name"),
        "guild_role_id": config.get("guild_role_id"),
        "guest_role_id": config.get("guest_role_id"),
        "log_channel_id": config.get("log_channel_id"),
        "complete_message": config.get("complete_message"),
        "verification_channel_id": config.get("verification_channel_id"),
        "embed_title": config.get("embed_title"),
        "embed_description": config.get("embed_description"),
        "detailed_verify": to_bool_out(config.get("detailed_verify")),
        "created_at": str(config.get("created_at") or ""),
        "updated_at": str(config.get("updated_at") or ""),
    }


def _format_log_entry(log: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": log.get("id"),
        "guild_id": log.get("guild_id"),
        "user_id": log.get("user_id"),
        "character_name": log.get("character_name"),
        "character_class": log.get("character_class"),
        "character_server": log.get("character_server"),
        "character_guild": log.get("character_guild"),
        "item_level": _to_float_or_none(log.get("item_level")),
        "verified_at": str(log.get("verified_at") or ""),
    }


def _hash_nonce(
    *,
    user_id: str,
    nonce: str,
    stove_profile_id: str = "",
) -> str:
    payload = f"{user_id}:{stove_profile_id}:{nonce}:{NONCE_PEPPER}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _linked_character_names(siblings: Any, representative_character_name: Any = None) -> set[str]:
    names: set[str] = set()
    rows = siblings if isinstance(siblings, list) else []
    for row in rows:
        name = (row or {}).get("CharacterName")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    rep = str(representative_character_name or "").strip()
    if rep:
        names.add(rep)
    return names


def _build_fallback_characters(representative_character_name: Any) -> list[dict[str, Any]]:
    rep = str(representative_character_name or "").strip()
    if not rep:
        return []
    return [
        {
            "character_name": rep,
            "character_class": "Unknown",
            "server_name": None,
            "item_level": None,
        }
    ]


class VerifyConfigRequest(BaseModel):
    basic_role_id: Optional[str] = Field(None)
    auto_nickname: Optional[bool] = Field(True)
    nickname_mode: Optional[str] = Field(None)
    search_nickname: Optional[bool] = Field(True)
    guild_name: Optional[str] = Field(None)
    guild_role_id: Optional[str] = Field(None)
    guest_role_id: Optional[str] = Field(None)
    log_channel_id: Optional[str] = Field(None)
    complete_message: Optional[str] = Field(None)
    verification_channel_id: Optional[str] = Field(None)
    embed_title: Optional[str] = Field(None)
    embed_description: Optional[str] = Field(None)
    detailed_verify: Optional[bool] = Field(False)


class VerifyUserRequest(BaseModel):
    user_id: str = Field(...)
    character_name: str = Field(...)


class StoveChallengeIssueRequest(BaseModel):
    user_id: str = Field(...)
    stove_value: str = Field(..., description="stove profile id or profile.onstove.com url")


class StoveChallengeConfirmRequest(BaseModel):
    user_id: str = Field(...)


@router.post("/{guild_id}/config")
async def save_verify_config(
    guild_id: int = Path(..., description="Discord Guild ID", gt=0),
    config: VerifyConfigRequest = ...,
):
    try:
        async with get_db() as db:
            exists = await db.execute("SELECT 1 FROM verify WHERE guild_id = ? LIMIT 1", (str(guild_id),))
            incoming, fields_set = _dump_incoming(config)

            if exists:
                update_fields = []
                update_values = []
                cols = (
                    "basic_role_id",
                    "guild_name",
                    "guild_role_id",
                    "log_channel_id",
                    "complete_message",
                    "verification_channel_id",
                    "embed_title",
                    "embed_description",
                    "guest_role_id",
                    "nickname_mode",
                )
                for col in cols:
                    if col in fields_set:
                        update_fields.append(f"{col} = ?")
                        update_values.append(incoming.get(col))

                if ("auto_nickname" in fields_set) and (incoming.get("auto_nickname") is not None):
                    update_fields.append("auto_nickname = ?")
                    update_values.append(1 if incoming["auto_nickname"] else 0)
                if ("search_nickname" in fields_set) and (incoming.get("search_nickname") is not None):
                    update_fields.append("search_nickname = ?")
                    update_values.append(1 if incoming["search_nickname"] else 0)
                if ("detailed_verify" in fields_set) and (incoming.get("detailed_verify") is not None):
                    update_fields.append("detailed_verify = ?")
                    update_values.append(1 if incoming["detailed_verify"] else 0)

                if update_fields:
                    update_values.append(str(guild_id))
                    await db.execute(
                        f"UPDATE verify SET {', '.join(update_fields)} WHERE guild_id = ?",
                        tuple(update_values),
                    )
                message = "Verify configuration updated successfully"
            else:
                await db.execute(
                    """
                    INSERT INTO verify (
                        guild_id, basic_role_id, auto_nickname, search_nickname, guild_name, guild_role_id,
                        log_channel_id, complete_message, verification_channel_id,
                        nickname_mode, embed_title, embed_description, guest_role_id, detailed_verify
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(guild_id),
                        incoming.get("basic_role_id"),
                        1 if incoming.get("auto_nickname", True) else 0,
                        1 if incoming.get("search_nickname", True) else 0,
                        incoming.get("guild_name"),
                        incoming.get("guild_role_id"),
                        incoming.get("log_channel_id"),
                        incoming.get("complete_message"),
                        incoming.get("verification_channel_id"),
                        incoming.get("nickname_mode"),
                        incoming.get("embed_title"),
                        incoming.get("embed_description"),
                        incoming.get("guest_role_id"),
                        1 if incoming.get("detailed_verify", False) else 0,
                    ),
                )
                message = "Verify configuration created successfully"

            await db.commit()
            config_data = await db.execute(
                """
                SELECT id, guild_id, basic_role_id, auto_nickname, search_nickname, nickname_mode,
                       guild_name, guild_role_id, guest_role_id, log_channel_id, complete_message,
                       verification_channel_id, embed_title, embed_description, detailed_verify, created_at, updated_at
                FROM verify
                WHERE guild_id = ? LIMIT 1
                """,
                (str(guild_id),),
            )
            response = _format_config_response(config_data[0])
            response["message"] = message
            return JSONResponse(content=response)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/{guild_id}/config")
async def get_verify_config(
    guild_id: int = Path(..., description="Discord Guild ID", gt=0),
):
    try:
        async with get_db() as db:
            config = await db.execute(
                """
                SELECT id, guild_id, basic_role_id, auto_nickname, search_nickname, nickname_mode,
                       guild_name, guild_role_id, guest_role_id, log_channel_id, complete_message,
                       verification_channel_id, embed_title, embed_description, detailed_verify, created_at, updated_at
                FROM verify
                WHERE guild_id = ? LIMIT 1
                """,
                (str(guild_id),),
            )
            if not config:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Verify configuration not found",
                        "guild_id": str(guild_id),
                        "detail": f"Guild ID {guild_id} verify config not found.",
                    },
                )
            return JSONResponse(content=_format_config_response(config[0]))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/{guild_id}/verify")
async def verify_user(
    guild_id: int = Path(..., description="Discord Guild ID", gt=0),
    request: VerifyUserRequest = ...,
):
    try:
        decoded_char_name = urllib.parse.unquote(request.character_name or "").strip()
        if not decoded_char_name:
            raise HTTPException(status_code=400, detail="character_name is required")

        async with get_db() as db:
            cfg = await db.execute(
                """
                SELECT basic_role_id, auto_nickname, guild_name, guild_role_id,
                       log_channel_id, complete_message, guest_role_id, nickname_mode, detailed_verify
                FROM verify WHERE guild_id = ? LIMIT 1
                """,
                (str(guild_id),),
            )
            if not cfg:
                raise HTTPException(status_code=404, detail="Verify configuration not found.")
            config = cfg[0]

            if to_bool_out(config.get("detailed_verify")):
                link = await db.execute(
                    """
                    SELECT stove_profile_id
                    FROM verify_stove_links
                    WHERE discord_user_id = ?
                    LIMIT 1
                    """,
                    (str(request.user_id),),
                )
                if not link:
                    raise HTTPException(status_code=409, detail="STOVE link is required")
                stove_profile_id = str(link[0].get("stove_profile_id") or "").strip()
                if not stove_profile_id:
                    raise HTTPException(status_code=409, detail="Linked STOVE profile is invalid")

        try:
            lostark_data = await search_lostark_character(decoded_char_name)
        except HTTPException as e:
            if _is_lostark_unavailable_exception(e):
                _raise_lostark_unavailable_http()
            raise
        except StoveProfileError as e:
            if _is_lostark_unavailable_exception(e):
                _raise_lostark_unavailable_http()
            raise
        except Exception as e:
            if _is_lostark_unavailable_exception(e):
                _raise_lostark_unavailable_http()
            raise
        if not lostark_data:
            raise HTTPException(status_code=404, detail=f"Character not found: {decoded_char_name}")

        async with get_db() as db:
            config_rows = await db.execute(
                """
                SELECT basic_role_id, auto_nickname, guild_name, guild_role_id,
                       log_channel_id, complete_message, guest_role_id, nickname_mode
                FROM verify WHERE guild_id = ? LIMIT 1
                """,
                (str(guild_id),),
            )
            if not config_rows:
                raise HTTPException(status_code=404, detail="Verify configuration not found.")
            config = config_rows[0]

            item_level = _to_float_or_none(lostark_data.get("item_lvl"))
            server_name = lostark_data.get("server_name") or "unknown"
            guild_name = lostark_data.get("guild_name")

            await db.execute(
                """
                INSERT INTO verify_logs (
                    guild_id, user_id, character_name, character_class,
                    character_server, character_guild, item_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(guild_id),
                    request.user_id,
                    lostark_data.get("char_name"),
                    lostark_data.get("class_name"),
                    server_name,
                    guild_name,
                    item_level,
                ),
            )
            await db.commit()

            is_guild_member = bool(config.get("guild_name")) and guild_name and config["guild_name"] == guild_name

            roles_to_assign = []
            if config.get("basic_role_id"):
                roles_to_assign.append(config["basic_role_id"])
            if is_guild_member and config.get("guild_role_id"):
                roles_to_assign.append(config["guild_role_id"])
            elif config.get("guild_name") and not is_guild_member and config.get("guest_role_id"):
                roles_to_assign.append(config["guest_role_id"])

            return JSONResponse(
                content={
                    "message": "Verification completed",
                    "verification_result": {
                        "user_id": request.user_id,
                        "character_name": lostark_data.get("char_name"),
                        "character_class": lostark_data.get("class_name"),
                        "item_level": item_level,
                        "server_name": server_name,
                        "guild_name": guild_name,
                        "is_guild_member": is_guild_member,
                        "roles_to_assign": roles_to_assign,
                        "should_change_nickname": to_bool_out(config.get("auto_nickname")),
                        "new_nickname": lostark_data.get("char_name")
                        if to_bool_out(config.get("auto_nickname"))
                        else None,
                        "log_channel_id": config.get("log_channel_id"),
                        "complete_message": config.get("complete_message"),
                        "char_image": lostark_data.get("char_image"),
                        "nickname_mode": config.get("nickname_mode"),
                    },
                }
            )
    except HTTPException:
        raise
    except StoveProfileError as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=500, detail=f"Verification error: {str(e)}")


@router.post("/stove/challenge")
async def issue_stove_challenge(
    request: StoveChallengeIssueRequest = ...,
):
    try:
        stove_profile_id = normalize_stove_profile_id_digits_only(request.stove_value)
        challenge_key = secrets.token_hex(16).upper()
        nonce_hash = _hash_nonce(
            user_id=str(request.user_id),
            stove_profile_id=stove_profile_id,
            nonce=challenge_key,
        )
        now = _utcnow()
        expires_at = now + timedelta(seconds=NONCE_TTL_SEC)

        async with get_db() as db:
            owner_rows = await db.execute(
                """
                SELECT discord_user_id
                FROM verify_stove_links
                WHERE stove_profile_id = ?
                LIMIT 1
                """,
                (stove_profile_id,),
            )
            if owner_rows:
                owner_id = str(owner_rows[0].get("discord_user_id") or "")
                if owner_id and owner_id != str(request.user_id):
                    raise HTTPException(
                        status_code=409,
                        detail="This STOVE profile is already linked to another Discord user.",
                    )

            await db.execute(
                """
                DELETE FROM verify_stove_challenges
                WHERE discord_user_id = ?
                  AND status IN ('PENDING', 'CANCELED', 'EXPIRED')
                """,
                (str(request.user_id),),
            )
            await db.execute(
                """
                DELETE FROM verify_stove_challenges
                WHERE status IN ('CANCELED', 'EXPIRED')
                """,
                tuple(),
            )
            await db.execute(
                """
                INSERT INTO verify_stove_challenges (
                    discord_user_id, stove_profile_id, nonce_hash, expires_at, status
                ) VALUES (?, ?, ?, ?, 'PENDING')
                """,
                (
                    str(request.user_id),
                    stove_profile_id,
                    nonce_hash,
                    expires_at.replace(tzinfo=None),
                ),
            )
            await db.commit()

        return JSONResponse(
            content={
                "message": "challenge_issued",
                "challenge_key": challenge_key,
                "stove_profile_id": stove_profile_id,
                "expires_at": expires_at.isoformat(),
                "ttl_seconds": NONCE_TTL_SEC,
                "instruction": "Set this key as your STOVE profile introduction and then press complete.",
            }
        )
    except HTTPException:
        raise
    except StoveProfileError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to issue challenge: {e}")


@router.post("/stove/confirm")
async def confirm_stove_challenge(
    request: StoveChallengeConfirmRequest = ...,
):
    try:
        async with get_db() as db:
            challenge_rows = await db.execute(
                """
                SELECT id, stove_profile_id, nonce_hash, expires_at
                FROM verify_stove_challenges
                WHERE discord_user_id = ? AND status = 'PENDING'
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(request.user_id),),
            )
            if not challenge_rows:
                raise HTTPException(status_code=404, detail="No pending STOVE challenge found.")

            challenge = challenge_rows[0]
            expires_at = challenge.get("expires_at")
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(expires_at)
                except Exception:
                    expires_at = None
            if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if (expires_at is not None) and (expires_at < _utcnow()):
                await db.execute(
                    "DELETE FROM verify_stove_challenges WHERE id = ?",
                    (int(challenge["id"]),),
                )
                await db.commit()
                raise HTTPException(status_code=410, detail="Challenge expired. Request a new challenge.")

        challenge_stove_profile_id = str(challenge.get("stove_profile_id") or "").strip()
        if not challenge_stove_profile_id:
            raise HTTPException(status_code=500, detail="Challenge data is invalid. Re-issue a challenge.")

        resolution = await resolve_stove_profile(challenge_stove_profile_id)
        stove_bio = (resolution.stove_profile_bio or "").strip()
        if not stove_bio:
            stove_bio = (
                await fetch_stove_status_message(
                    challenge_stove_profile_id,
                    retries=2,
                    retry_delay_sec=1.2,
                )
                or ""
            ).strip()
        if not stove_bio:
            await asyncio.sleep(1.0)
            stove_bio = (
                await fetch_stove_status_message(
                    challenge_stove_profile_id,
                    retries=1,
                    retry_delay_sec=1.0,
                )
                or ""
            ).strip()
        if not stove_bio:
            raise HTTPException(
                status_code=409,
                detail="Could not read STOVE profile introduction. Check profile visibility/save state and retry.",
            )

        computed_hash = _hash_nonce(
            user_id=str(request.user_id),
            stove_profile_id=challenge_stove_profile_id,
            nonce=stove_bio,
        )
        if computed_hash != challenge.get("nonce_hash"):
            async with get_db() as db:
                await db.execute(
                    """
                    UPDATE verify_stove_challenges
                    SET attempt_count = attempt_count + 1, last_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    ("challenge_mismatch", int(challenge["id"])),
                )
                await db.commit()
            raise HTTPException(status_code=409, detail="Challenge key mismatch in STOVE profile introduction.")

        top_characters = build_top_characters(resolution.siblings, limit=0)

        async with get_db() as db:
            owner_rows = await db.execute(
                """
                SELECT discord_user_id
                FROM verify_stove_links
                WHERE stove_profile_id = ?
                LIMIT 1
                """,
                (resolution.stove_profile_id,),
            )
            if owner_rows:
                owner_id = str(owner_rows[0].get("discord_user_id") or "")
                if owner_id and owner_id != str(request.user_id):
                    raise HTTPException(
                        status_code=409,
                        detail="This STOVE profile is already linked to another Discord user.",
                    )

            existing_link = await db.execute(
                """
                SELECT id
                FROM verify_stove_links
                WHERE discord_user_id = ?
                LIMIT 1
                """,
                (str(request.user_id),),
            )
            if existing_link:
                await db.execute(
                    """
                    UPDATE verify_stove_links
                    SET stove_profile_id = ?, representative_character_name = ?,
                        verified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        resolution.stove_profile_id,
                        resolution.representative_character_name,
                        int(existing_link[0]["id"]),
                    ),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO verify_stove_links (
                        discord_user_id, stove_profile_id, representative_character_name, verified_at
                    ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        str(request.user_id),
                        resolution.stove_profile_id,
                        resolution.representative_character_name,
                    ),
                )

            await db.execute(
                """
                UPDATE verify_stove_challenges
                SET status = 'VERIFIED', stove_profile_id = ?, used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (resolution.stove_profile_id, int(challenge["id"])),
            )
            await db.commit()

        return JSONResponse(
            content={
                "message": "stove_linked",
                "linked": True,
                "stove_profile_id": resolution.stove_profile_id,
                "representative_character_name": resolution.representative_character_name,
                "characters": top_characters,
            }
        )
    except HTTPException:
        raise
    except StoveProfileError as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=500, detail=f"Failed to confirm STOVE challenge: {e}")


@router.get("/stove/link/{user_id}")
async def get_stove_link(
    user_id: str = Path(..., description="Discord User ID"),
):
    try:
        async with get_db() as db:
            rows = await db.execute(
                """
                SELECT stove_profile_id, representative_character_name, verified_at, updated_at
                FROM verify_stove_links
                WHERE discord_user_id = ?
                LIMIT 1
                """,
                (str(user_id),),
            )
            if not rows:
                return JSONResponse(content={"linked": False, "user_id": str(user_id)})
            row = rows[0]
            stove_profile_id = str(row.get("stove_profile_id") or "").strip()
            if not stove_profile_id:
                raise HTTPException(status_code=409, detail="Linked STOVE profile is invalid")
            resolved_rep = str(row.get("representative_character_name") or "").strip()
            characters = _build_fallback_characters(resolved_rep)
            if resolved_rep:
                try:
                    siblings = await fetch_lostark_siblings_by_character_name(resolved_rep)
                    fetched = build_top_characters(siblings, limit=0)
                    if fetched:
                        characters = fetched
                except StoveProfileError as e:
                    if _is_lostark_unavailable_exception(e):
                        _raise_lostark_unavailable_http()
                    print(
                        f"[verify] sibling fetch degraded user={user_id} "
                        f"representative={resolved_rep!r} err={e!r}"
                    )
                except Exception as e:
                    if _is_lostark_unavailable_exception(e):
                        _raise_lostark_unavailable_http()
                    print(
                        f"[verify] sibling fetch degraded user={user_id} "
                        f"representative={resolved_rep!r} err={e!r}"
                    )

            return JSONResponse(
                content={
                    "linked": True,
                    "user_id": str(user_id),
                    "stove_profile_id": stove_profile_id,
                    "representative_character_name": resolved_rep or row.get("representative_character_name"),
                    "characters": characters,
                    "verified_at": str(row.get("verified_at") or ""),
                    "updated_at": str(row.get("updated_at") or ""),
                }
            )
    except HTTPException:
        raise
    except StoveProfileError as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=500, detail=f"Failed to fetch STOVE link: {e}")


@router.post("/stove/link/{user_id}/refresh")
async def refresh_stove_link(
    user_id: str = Path(..., description="Discord User ID"),
):
    try:
        async with get_db() as db:
            rows = await db.execute(
                """
                SELECT stove_profile_id, representative_character_name, verified_at
                FROM verify_stove_links
                WHERE discord_user_id = ?
                LIMIT 1
                """,
                (str(user_id),),
            )
            if not rows:
                raise HTTPException(status_code=404, detail="No linked STOVE profile found")

            row = rows[0]
            stove_profile_id = str(row.get("stove_profile_id") or "").strip()
            if not stove_profile_id:
                raise HTTPException(status_code=409, detail="Linked STOVE profile is invalid")

        resolution = await resolve_stove_profile(stove_profile_id)
        resolved_profile_id = str(resolution.stove_profile_id or stove_profile_id).strip() or stove_profile_id
        resolved_rep = str(
            resolution.representative_character_name or row.get("representative_character_name") or ""
        ).strip()
        characters = build_top_characters(resolution.siblings, limit=0)
        if not characters:
            characters = _build_fallback_characters(resolved_rep)

        async with get_db() as db:
            await db.execute(
                """
                UPDATE verify_stove_links
                SET stove_profile_id = ?, representative_character_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ?
                """,
                (resolved_profile_id, resolved_rep or None, str(user_id)),
            )
            await db.commit()
            refreshed_rows = await db.execute(
                """
                SELECT representative_character_name, verified_at, updated_at
                FROM verify_stove_links
                WHERE discord_user_id = ?
                LIMIT 1
                """,
                (str(user_id),),
            )
            refreshed = refreshed_rows[0] if refreshed_rows else {}

        return JSONResponse(
            content={
                "message": "stove_link_refreshed",
                "linked": True,
                "user_id": str(user_id),
                "stove_profile_id": resolved_profile_id,
                "representative_character_name": resolved_rep or refreshed.get("representative_character_name"),
                "characters": characters,
                "verified_at": str(refreshed.get("verified_at") or row.get("verified_at") or ""),
                "updated_at": str(refreshed.get("updated_at") or ""),
            }
        )
    except HTTPException:
        raise
    except StoveProfileError as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=500, detail=f"Failed to refresh STOVE link: {e}")


@router.delete("/stove/link/{user_id}")
async def disconnect_stove_link(
    user_id: str = Path(..., description="Discord User ID"),
):
    try:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM verify_stove_links WHERE discord_user_id = ?",
                (str(user_id),),
            )
            deleted = int(db.cursor.rowcount or 0)
            await db.execute(
                """
                DELETE FROM verify_stove_challenges
                WHERE discord_user_id = ?
                  AND status IN ('PENDING', 'CANCELED', 'EXPIRED')
                """,
                (str(user_id),),
            )
            await db.commit()
            return JSONResponse(
                content={
                    "message": "stove_link_disconnected",
                    "user_id": str(user_id),
                    "deleted": deleted > 0,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to disconnect STOVE link: {e}")


@router.get("/stove/resolve")
async def resolve_stove_profile_route(
    value: str = Query(..., description="stove_profile_id or profile.onstove.com url"),
):
    try:
        resolution = await resolve_stove_profile(value)
        return JSONResponse(
            content={
                "stove_profile_id": resolution.stove_profile_id,
                "stove_profile_bio": resolution.stove_profile_bio,
                "representative_character_name": resolution.representative_character_name,
                "profile": resolution.profile,
                "siblings": resolution.siblings,
                "top_characters": build_top_characters(resolution.siblings, limit=6),
            }
        )
    except StoveProfileError as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        if _is_lostark_unavailable_exception(e):
            _raise_lostark_unavailable_http()
        raise HTTPException(status_code=500, detail=f"Failed to resolve STOVE profile: {e}")


@router.get("/{guild_id}/logs")
async def get_verify_logs(
    guild_id: int = Path(..., description="Discord Guild ID", gt=0),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    cursor_id: Optional[int] = Query(None, description="keyset cursor (id less-than)"),
):
    try:
        async with get_db() as db:
            if cursor_id is not None and cursor_id > 0:
                logs = await db.execute(
                    """
                    SELECT id, guild_id, user_id, character_name, character_class,
                           character_server, character_guild, item_level, verified_at
                    FROM verify_logs
                    WHERE guild_id = ? AND id < ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (str(guild_id), int(cursor_id), limit),
                )
            else:
                logs = await db.execute(
                    """
                    SELECT id, guild_id, user_id, character_name, character_class,
                           character_server, character_guild, item_level, verified_at
                    FROM verify_logs
                    WHERE guild_id = ?
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (str(guild_id), limit, offset),
                )

            total = await db.execute(
                "SELECT COUNT(1) as count FROM verify_logs WHERE guild_id = ?",
                (str(guild_id),),
            )
            total_count = total[0]["count"] if total else 0

            return JSONResponse(
                content={
                    "guild_id": str(guild_id),
                    "logs": [_format_log_entry(log) for log in (logs or [])],
                    "total_count": total_count,
                    "limit": limit,
                    "offset": offset,
                    "cursor_id": cursor_id,
                    "next_cursor_id": (logs[-1]["id"] if logs else None),
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/{guild_id}/embed")
async def get_verification_embed(
    guild_id: int = Path(..., description="Discord Guild ID", gt=0),
):
    try:
        async with get_db() as db:
            config = await db.execute(
                """
                SELECT embed_title, embed_description, verification_channel_id, detailed_verify
                FROM verify WHERE guild_id = ? LIMIT 1
                """,
                (str(guild_id),),
            )
            if not config:
                return JSONResponse(content={"title": None, "description": None, "has_config": False})
            return JSONResponse(
                content={
                    "title": config[0].get("embed_title"),
                    "description": config[0].get("embed_description"),
                    "has_config": True,
                    "verification_channel_id": config[0].get("verification_channel_id"),
                    "detailed_verify": to_bool_out(config[0].get("detailed_verify")),
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.delete("/{guild_id}/config")
async def delete_verify_config(
    guild_id: int = Path(..., description="Discord Guild ID", gt=0),
):
    try:
        async with get_db() as db:
            await db.execute("DELETE FROM verify WHERE guild_id = ?", (str(guild_id),))
            if db.cursor.rowcount == 0:
                return JSONResponse(
                    status_code=404,
                    content={"message": "Verify configuration not found", "guild_id": str(guild_id)},
                )
            await db.commit()
            return JSONResponse(
                content={
                    "message": "Verify configuration deleted successfully",
                    "guild_id": str(guild_id),
                    "deleted": True,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")