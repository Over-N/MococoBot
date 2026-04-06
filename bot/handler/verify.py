from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord
import httpx

from core.http_client import http_client

STOVE_VERIFY_WEB_URL = "https://api.mococobot.kr/discord_login?next=https://mococobot.kr/verify"
LOSTARK_UNAVAILABLE_DETAIL = "lostark_service_unavailable"


def _safe_json(response) -> Dict[str, Any]:
    try:
        return response.json() or {}
    except Exception:
        return {}


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    return False


def _extract_error_detail(data: Dict[str, Any]) -> str:
    detail = data.get("detail")
    if detail is None:
        return ""
    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, dict):
        for key in ("message", "detail", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(detail, list):
        parts: List[str] = []
        for item in detail:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                msg = item.get("msg")
                if isinstance(msg, str) and msg.strip():
                    parts.append(msg.strip())
        return " ".join(parts).strip()
    return str(detail).strip()


def _friendly_issue_error_message(status_code: int, data: Dict[str, Any]) -> str:
    detail = _extract_error_detail(data).lower()

    if status_code == 429 or "too many" in detail:
        return "요청이 너무 많습니다. 잠시 후 다시 OTP를 발급해 주세요."
    if _is_lostark_unavailable_detail(detail) or status_code == 503:
        return _lostark_unavailable_notice_text()
    if status_code in (400, 422):
        return "STOVE 프로필 주소를 다시 확인해 주세요."
    if status_code == 409 or "already" in detail:
        return "이미 진행 중인 OTP가 있습니다. 프로필 반영 후 완료 확인을 눌러 주세요."
    return "OTP 발급에 실패했습니다. 잠시 후 다시 시도해 주세요."


def _friendly_confirm_error_message(status_code: int, data: Dict[str, Any]) -> str:
    detail = _extract_error_detail(data).lower()

    if status_code == 429 or "too many" in detail:
        return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
    if status_code == 404 or "not found" in detail:
        return "진행 중인 OTP가 없습니다. 먼저 OTP를 발급해 주세요."
    if status_code == 410 or "expired" in detail or "만료" in detail:
        return "OTP 유효 시간이 만료되었습니다. 새 OTP를 발급한 뒤 다시 확인해 주세요."
    if _is_lostark_unavailable_detail(detail) or status_code == 503:
        return _lostark_unavailable_notice_text()
    if status_code in (400, 422):
        return "프로필 소개글의 OTP 반영 여부를 확인한 뒤 다시 시도해 주세요."
    return "STOVE 인증에 실패했습니다. 잠시 후 다시 시도해 주세요."


def _is_lostark_unavailable_detail(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    keywords = (
        LOSTARK_UNAVAILABLE_DETAIL,
        "lost ark open api request failed",
        "lostark open api request failed",
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


def _lostark_unavailable_notice_text() -> str:
    return "로스트아크 서버에 접속할 수 없어요. 정기점검 이후 다시 시도해 주세요."


def _build_lostark_unavailable_embed() -> discord.Embed:
    return discord.Embed(
        title="로스트아크 점검 안내",
        description="현재 로스트아크 서버에 접속할 수 없어요.\n점검 중이거나 일시적으로 응답하지 않고 있어요.\n정기점검 이후 다시 시도해 주세요.",
        color=discord.Color.orange(),
    )


async def _fetch_lostark_class_role_names() -> set[str]:
    try:
        response = await http_client.get(
            "/character/class",
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0),
            max_attempts=2,
        )
    except Exception:
        return set()

    if response.status_code != 200:
        return set()

    data = _safe_json(response)
    rows = data.get("data") or []
    names: set[str] = set()
    for row in rows:
        name = str((row or {}).get("name") or "").strip()
        if name:
            names.add(name)
    return names
    
    
async def _send_progress_message(interaction: discord.Interaction, content: str):
    try:
        return await interaction.followup.send(content, ephemeral=True, wait=True)
    except TypeError:
        await interaction.followup.send(content, ephemeral=True)
        return None
    except Exception:
        return None


async def _send_ephemeral_message(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def _send_ephemeral_embed(interaction: discord.Interaction, embed: discord.Embed) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def _send_ephemeral_view(interaction: discord.Interaction, view: discord.ui.DesignerView) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def _edit_progress_message(progress_message, *, content: Optional[str] = None, embed=None, view=None) -> bool:
    if progress_message is None:
        return False
    if isinstance(view, discord.ui.DesignerView):
        content = None
        embed = None
    try:
        await progress_message.edit(content=content, embed=embed, view=view)
        return True
    except Exception:
        return False


async def _edit_stove_flow_message(
    interaction: discord.Interaction,
    *,
    view: discord.ui.DesignerView,
    source_message_id: Optional[int] = None,
) -> bool:
    target_message_id = source_message_id or getattr(getattr(interaction, "message", None), "id", None)

    if not interaction.response.is_done():
        try:
            await interaction.response.edit_message(view=view)
            return True
        except Exception:
            pass

    try:
        await interaction.edit_original_response(view=view)
        return True
    except Exception:
        pass

    if target_message_id is not None:
        try:
            await interaction.followup.edit_message(target_message_id, view=view)
            return True
        except Exception:
            pass

    message = getattr(interaction, "message", None)
    if message is not None:
        try:
            await message.edit(view=view)
            return True
        except Exception:
            pass

    return False


class CharacterVerifyModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="로스트아크 인증")
        self.add_item(
            discord.ui.InputText(
                label="캐릭터 이름",
                placeholder="인증할 캐릭터 이름을 입력하세요",
                required=True,
                max_length=16,
            )
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        character_name = (self.children[0].value or "").strip()
        await _run_character_verify(interaction, character_name)


class StoveProfileIssueModal(discord.ui.Modal):
    def __init__(self, source_message_id: Optional[int] = None):
        super().__init__(title="STOVE OTP 발급")
        self.source_message_id = source_message_id
        self.add_item(
            discord.ui.InputText(
                label="STOVE 프로필",
                placeholder="예: https://profile.onstove.com/ko/169891721",
                required=True,
                max_length=200,
            )
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(invisible=True)
        stove_profile_value = (self.children[0].value or "").strip()
        if not stove_profile_value:
            await interaction.followup.send("STOVE 프로필을 입력해 주세요.", ephemeral=True)
            return
        await _handle_issue_challenge(interaction, stove_profile_value, self.source_message_id)


class StoveConnectView(discord.ui.DesignerView):
    def __init__(
        self,
        *,
        challenge_key: Optional[str] = None,
        stove_profile_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        notice: Optional[str] = None,
        processing: bool = False,
    ):
        super().__init__(timeout=300)

        issued = bool(challenge_key)
        current_stove_profile_id = str(stove_profile_id or "").strip()
        profile_url = f"https://profile.onstove.com/ko/{current_stove_profile_id}" if current_stove_profile_id else "https://profile.onstove.com/"

        issue_button = discord.ui.Button(
            label="OTP 재발급" if issued else "OTP 발급",
            style=discord.ButtonStyle.secondary if issued else discord.ButtonStyle.primary,
            emoji="🔐",
            disabled=bool(issued and processing),
        )

        async def issue_callback(interaction: discord.Interaction):
            source_message_id = getattr(getattr(interaction, "message", None), "id", None)
            if issued and current_stove_profile_id:
                await interaction.response.defer(invisible=True)
                await _handle_issue_challenge(interaction, current_stove_profile_id, source_message_id)
                return
            await interaction.response.send_modal(StoveProfileIssueModal(source_message_id=source_message_id))

        issue_button.callback = issue_callback

        web_verify_button = discord.ui.Button(
            label="웹에서 인증하기",
            style=discord.ButtonStyle.link,
            emoji="🌐",
            url=STOVE_VERIFY_WEB_URL,
        )

        items: List[discord.ui.ViewItem] = [
            discord.ui.TextDisplay("## STOVE 상세 인증"),
            discord.ui.TextDisplay("STOVE 프로필 소개글에 OTP를 적고, 서버가 이를 확인하는 방식으로 연동됩니다."),
            discord.ui.Separator(),
        ]

        if notice:
            items.extend(
                [
                    discord.ui.TextDisplay(f"### 안내\n{notice}"),
                    discord.ui.Separator(),
                ]
            )

        if not issued:
            items.extend(
                [
                    discord.ui.TextDisplay(f"### STEP 1. OTP 발급\nOTP 발급 버튼을 누른 뒤 [STOVE 프로필]({profile_url})의 링크를 입력하세요."),
                    discord.ui.ActionRow(issue_button, web_verify_button),
                    discord.ui.TextDisplay("-# 웹에서 하면 편리하고, 상세 가이드를 확인하며 진행할 수 있어요."),
                ]
            )
        else:
            confirm_button = discord.ui.Button(
                label="완료 확인",
                style=discord.ButtonStyle.success,
                emoji="✅",
                disabled=bool(processing),
            )

            async def confirm_callback(interaction: discord.Interaction):
                await _handle_confirm_challenge(
                    interaction,
                    challenge_key=str(challenge_key or ""),
                    stove_profile_id=current_stove_profile_id,
                    ttl_seconds=ttl_seconds,
                )

            confirm_button.callback = confirm_callback

            otp_block = f"```{challenge_key}```"
            step2_text = (
                "### STEP 2. 서버 검증 중\n서버가 STOVE 프로필 소개글을 검증 중입니다. 잠시만 기다려 주세요."
                if processing
                else f"### STEP 2. 완료 확인\nOTP를 [STOVE 프로필 소개글]({profile_url})에 입력한 뒤 완료 확인을 눌러 주세요."
            )

            items.extend(
                [
                    discord.ui.TextDisplay(
                        "\n".join(
                            [
                                f"- 프로필 ID: {current_stove_profile_id or '-'}",
                                f"- 유효 시간: {ttl_seconds if ttl_seconds is not None else '-'}초",
                                otp_block,
                            ]
                        )
                    ),
                    discord.ui.Separator(),
                    discord.ui.TextDisplay(step2_text),
                    discord.ui.ActionRow(confirm_button),
                    discord.ui.TextDisplay("-# OTP가 만료됐거나 다시 발급이 필요하면 재발급을 눌러 주세요."),
                    discord.ui.ActionRow(issue_button),
                ]
            )

        container = discord.ui.Container(*items, color=discord.Color.blue())
        self.add_item(container)


class StoveLinkedDropdownView(discord.ui.DesignerView):
    def __init__(
        self,
        link_data: Dict[str, Any],
        characters: List[Dict[str, Any]],
        *,
        selected_character: Optional[str] = None,
        processing: bool = False,
        completed: bool = False,
        completed_guild_name: Optional[str] = None,
        notice: Optional[str] = None,
    ):
        super().__init__(timeout=300)

        normalized: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in characters or []:
            name = str((row or {}).get("character_name") or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "character_name": name,
                    "character_class": str((row or {}).get("character_class") or "Unknown"),
                    "item_level": row.get("item_level"),
                }
            )

        selected = str(selected_character or "").strip()

        settings_button = discord.ui.Button(
            label="스토브 설정",
            style=discord.ButtonStyle.secondary,
            emoji="⚙️",
            disabled=processing,
        )

        async def settings_callback(interaction: discord.Interaction):
            await _handle_show_settings(interaction, cached_link_data=link_data)

        settings_button.callback = settings_callback

        lines = [
            f"- STOVE 프로필 ID: `{link_data.get('stove_profile_id') or '-'}`",
            f"- 선택 캐릭터: `{selected}`" if selected else "- 선택 캐릭터: 없음",
        ]
        if notice:
            lines.append(f"-# {notice}")

        header_title = "## STOVE 연동 완료"
        if completed:
            guild_name = str(completed_guild_name or "").strip()
            header_title = f"## {guild_name} 인증 완료" if guild_name else "## 인증 완료"
        if processing:
            header_title = "## STOVE 정보로 서버 인증중.."

        items: List[discord.ui.ViewItem] = [
            discord.ui.TextDisplay(header_title),
            discord.ui.Section(discord.ui.TextDisplay("\n".join(lines)), accessory=settings_button),
            discord.ui.Separator(),
        ]

        if completed:
            self.add_item(discord.ui.Container(*items, color=discord.Color.green()))
            return

        if processing:
            self.add_item(discord.ui.Container(*items, color=discord.Color.yellow()))
            return

        if not normalized:
            items.append(discord.ui.TextDisplay("연동된 캐릭터 목록을 불러오지 못했습니다."))
            self.add_item(discord.ui.Container(*items, color=discord.Color.orange()))
            return

        chunks: List[List[Dict[str, Any]]] = [normalized[i : i + 25] for i in range(0, len(normalized), 25)]
        for chunk_idx, chunk in enumerate(chunks, start=1):
            options: List[discord.SelectOption] = []
            for row in chunk:
                name = str(row.get("character_name") or "").strip()
                if not name:
                    continue
                cls = str(row.get("character_class") or "Unknown")
                item_level = str(row.get("item_level") or "?")
                options.append(
                    discord.SelectOption(
                        label=name[:100],
                        description=f"{cls} | {item_level}"[:100],
                        value=name,
                        default=(selected == name),
                    )
                )
            if not options:
                continue

            select = discord.ui.Select(
                placeholder=f"캐릭터 선택 ({chunk_idx}/{len(chunks)})",
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"stove_verify_select_{chunk_idx}",
            )

            async def select_callback(interaction: discord.Interaction, component: discord.ui.Select = select):
                chosen = str(component.values[0] if component.values else "").strip()
                if not chosen:
                    await interaction.response.defer(invisible=True)
                    return
                await interaction.response.edit_message(
                    view=StoveLinkedDropdownView(
                        link_data,
                        normalized,
                        selected_character=chosen,
                        notice=f"> `{chosen}` 캐릭터를 선택했습니다. **[인증]** 버튼을 눌러 인증을 진행하세요.",
                    )
                )

            select.callback = select_callback
            items.append(discord.ui.ActionRow(select))

        verify_button = discord.ui.Button(
            label="인증",
            style=discord.ButtonStyle.primary,
            emoji="✅",
            disabled=not bool(selected),
        )

        async def verify_callback(interaction: discord.Interaction):
            if not selected:
                await interaction.response.send_message("먼저 캐릭터를 선택해 주세요.", ephemeral=True)
                return
            await interaction.response.defer(invisible=True)
            source_message_id = getattr(getattr(interaction, "message", None), "id", None)
            await _edit_stove_flow_message(
                interaction,
                view=StoveLinkedDropdownView(
                    link_data,
                    normalized,
                    selected_character=selected,
                    processing=True,
                    notice=f"`{selected}` 캐릭터로 인증 진행중입니다.",
                ),
                source_message_id=source_message_id,
            )
            await _run_character_verify_from_stove_selection(
                interaction=interaction,
                character_name=selected,
                source_message_id=source_message_id,
                link_data=link_data,
                characters=normalized,
            )

        verify_button.callback = verify_callback
        items.append(discord.ui.ActionRow(verify_button))

        self.add_item(discord.ui.Container(*items, color=discord.Color.green()))


class StoveSettingsView(discord.ui.DesignerView):
    def __init__(
        self,
        settings_data: Dict[str, Any],
        user_mention: str,
        *,
        processing: bool = False,
        notice: Optional[str] = None,
    ):
        super().__init__(timeout=300)

        refresh_button = discord.ui.Button(
            label="갱신 중" if processing else "갱신",
            style=discord.ButtonStyle.secondary if processing else discord.ButtonStyle.primary,
            emoji="⏳" if processing else "🔄",
            disabled=processing,
        )

        async def refresh_callback(interaction: discord.Interaction):
            await _handle_refresh_link(interaction, settings_data=settings_data)

        refresh_button.callback = refresh_callback

        disconnect_button = discord.ui.Button(
            label="연결 끊기",
            style=discord.ButtonStyle.danger,
            emoji="✂️",
            disabled=processing,
        )

        async def disconnect_callback(interaction: discord.Interaction):
            await _handle_disconnect(interaction)

        disconnect_button.callback = disconnect_callback

        info_lines = [
            f"- Discord 사용자: {user_mention}",
            f"- STOVE 프로필 ID: `{settings_data.get('stove_profile_id') or '-'}`",
            f"- 대표 캐릭터: {settings_data.get('representative_character_name') or '-'}",
            f"- 마지막 검증 시각: {settings_data.get('verified_at') or '-'}",
        ]

        items = [
            discord.ui.TextDisplay("## STOVE 연결 설정"),
            discord.ui.TextDisplay("\n".join(info_lines)),
        ]
        if notice:
            items.extend([discord.ui.Separator(), discord.ui.TextDisplay(notice)])
        items.extend(
            [
                discord.ui.Separator(),
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        "### 정보 갱신\n대표 캐릭터 정보를 최신 상태로 갱신합니다."
                        if not processing
                        else "### 정보 갱신\n대표 캐릭터 정보를 불러오고 있어요. 잠시만 기다려 주세요."
                    ),
                    accessory=refresh_button,
                ),
                discord.ui.Section(
                    discord.ui.TextDisplay("### 연결 해제\n현재 STOVE 연동을 해제합니다."),
                    accessory=disconnect_button,
                ),
            ]
        )

        container = discord.ui.Container(*items, color=discord.Color.orange())
        self.add_item(container)

async def _run_character_verify(interaction: discord.Interaction, character_name: str) -> None:
    name = (character_name or "").strip()
    if not name:
        await interaction.followup.send("캐릭터 이름을 입력하거나 선택해 주세요.", ephemeral=True)
        return

    try:
        response = await http_client.post(
            f"/verify/{interaction.guild_id}/verify",
            json={"user_id": str(interaction.user.id), "character_name": name},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
    except httpx.TimeoutException:
        await interaction.followup.send("인증 요청 시간이 초과되었습니다. 다시 시도해 주세요.", ephemeral=True)
        return
    except httpx.HTTPError:
        await interaction.followup.send("네트워크 오류로 인증 요청에 실패했습니다.", ephemeral=True)
        return
    except Exception:
        await interaction.followup.send("인증 처리 중 오류가 발생했습니다.", ephemeral=True)
        return

    data = _safe_json(response)
    if response.status_code == 200:
        result = data.get("verification_result") or {}
        await _apply_verification_result(interaction, result)
        return

    detail = data.get("detail") or response.status_code
    if response.status_code == 404:
        await interaction.followup.send(f"캐릭터를 찾을 수 없습니다: {name}", ephemeral=True)
        return
    if response.status_code == 503 or _is_lostark_unavailable_detail(detail):
        await interaction.followup.send(embed=_build_lostark_unavailable_embed(), ephemeral=True)
        return
    await interaction.followup.send(f"인증 실패: {detail}", ephemeral=True)


async def _run_character_verify_from_stove_selection(
    *,
    interaction: discord.Interaction,
    character_name: str,
    source_message_id: Optional[int],
    link_data: Dict[str, Any],
    characters: List[Dict[str, Any]],
) -> None:
    name = (character_name or "").strip()
    if not name:
        await interaction.followup.send("캐릭터를 선택해 주세요.", ephemeral=True)
        return

    try:
        response = await http_client.post(
            f"/verify/{interaction.guild_id}/verify",
            json={"user_id": str(interaction.user.id), "character_name": name},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
    except httpx.TimeoutException:
        msg = "인증 요청 시간이 초과되었습니다. 다시 시도해 주세요."
        await _edit_stove_flow_message(
            interaction,
            view=StoveLinkedDropdownView(link_data, characters, selected_character=name, notice=msg),
            source_message_id=source_message_id,
        )
        await interaction.followup.send(msg, ephemeral=True)
        return
    except httpx.HTTPError:
        msg = "네트워크 오류로 인증 요청에 실패했습니다."
        await _edit_stove_flow_message(
            interaction,
            view=StoveLinkedDropdownView(link_data, characters, selected_character=name, notice=msg),
            source_message_id=source_message_id,
        )
        await interaction.followup.send(msg, ephemeral=True)
        return
    except Exception:
        msg = "인증 처리 중 오류가 발생했습니다."
        await _edit_stove_flow_message(
            interaction,
            view=StoveLinkedDropdownView(link_data, characters, selected_character=name, notice=msg),
            source_message_id=source_message_id,
        )
        await interaction.followup.send(msg, ephemeral=True)
        return

    data = _safe_json(response)
    if response.status_code == 200:
        result = data.get("verification_result") or {}
        await _apply_verification_result(interaction, result)
        guild_name = str(getattr(getattr(interaction, "guild", None), "name", "") or "").strip()
        await _edit_stove_flow_message(
            interaction,
            view=StoveLinkedDropdownView(
                link_data,
                characters,
                selected_character=name,
                completed=True,
                completed_guild_name=guild_name,
                notice=f"`{name}` 캐릭터 인증이 완료되었습니다.",
            ),
            source_message_id=source_message_id,
        )
        return

    detail = data.get("detail") or response.status_code
    if response.status_code == 404:
        msg = f"캐릭터를 찾을 수 없습니다: {name}"
    elif response.status_code == 503 or _is_lostark_unavailable_detail(detail):
        msg = _lostark_unavailable_notice_text()
    else:
        msg = f"인증 실패: {detail}"

    await _edit_stove_flow_message(
        interaction,
        view=StoveLinkedDropdownView(link_data, characters, selected_character=name, notice=msg),
        source_message_id=source_message_id,
    )
    if response.status_code == 503 or _is_lostark_unavailable_detail(detail):
        await interaction.followup.send(embed=_build_lostark_unavailable_embed(), ephemeral=True)
        return
    await interaction.followup.send(msg, ephemeral=True)


async def _apply_verification_result(interaction: discord.Interaction, result: Dict[str, Any]) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("길드 정보를 확인할 수 없습니다.", ephemeral=True)
        return

    server_name = result.get("server_name")
    class_name = result.get("character_class")

    server_role = None
    class_role = None
    if server_name:
        server_role = discord.utils.get(guild.roles, name=f"서버({server_name})")
    if class_name:
        class_role = discord.utils.get(guild.roles, name=str(class_name))

    missing = []
    if not server_role:
        missing.append(f"서버({server_name})" if server_name else "서버 역할")
    if not class_role:
        missing.append(class_name if class_name else "직업 역할")
    if missing:
        await interaction.followup.send(
            f"필요한 역할이 없어 인증을 완료할 수 없습니다: {', '.join(missing)}",
            ephemeral=True,
        )
        return

    roles_to_assign_ids = set()
    for rid in result.get("roles_to_assign", []):
        parsed = _to_int(rid)
        if parsed:
            roles_to_assign_ids.add(parsed)
    roles_to_assign_ids.add(server_role.id)
    roles_to_assign_ids.add(class_role.id)

    verify_related_role_ids = set(roles_to_assign_ids)
    try:
        config_response = await http_client.get(
            f"/verify/{guild.id}/config",
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0),
        )
        if config_response.status_code == 200:
            config_data = _safe_json(config_response)
            for key in ("basic_role_id", "guild_role_id", "guest_role_id"):
                parsed = _to_int(config_data.get(key))
                if parsed:
                    verify_related_role_ids.add(parsed)
    except Exception:
        pass

    class_role_names = await _fetch_lostark_class_role_names()
    if class_name:
        class_role_names.add(str(class_name))

    roles_to_remove_map: Dict[int, discord.Role] = {}
    for role in interaction.user.roles:
        if role == guild.default_role:
            continue
        if role.id in verify_related_role_ids:
            roles_to_remove_map[role.id] = role
            continue
        if role.name == "미인증":
            roles_to_remove_map[role.id] = role
            continue
        if role.name.startswith("서버(") and role.name.endswith(")"):
            roles_to_remove_map[role.id] = role
            continue
        if role.name in class_role_names:
            roles_to_remove_map[role.id] = role
            continue

    if roles_to_remove_map:
        try:
            await interaction.user.remove_roles(*roles_to_remove_map.values(), reason="verify refresh")
        except Exception:
            for role in roles_to_remove_map.values():
                try:
                    await interaction.user.remove_roles(role, reason="verify refresh")
                except Exception:
                    pass

    assigned_roles: List[str] = []
    roles_to_add: List[discord.Role] = []
    for rid in roles_to_assign_ids:
        role = guild.get_role(rid)
        if role:
            roles_to_add.append(role)

    if roles_to_add:
        try:
            await interaction.user.add_roles(*roles_to_add, reason="verify completed")
            assigned_roles = [role.mention for role in roles_to_add]
        except Exception:
            for role in roles_to_add:
                try:
                    await interaction.user.add_roles(role, reason="verify completed")
                    assigned_roles.append(role.mention)
                except Exception:
                    continue

    if result.get("should_change_nickname"):
        nickname_mode = str(result.get("nickname_mode") or "{nickname}")
        base_name = str(result.get("new_nickname") or result.get("character_name") or "")
        item_level = result.get("item_level")
        try:
            item_level_text = str(int(float(item_level))) if item_level is not None else ""
        except Exception:
            item_level_text = str(item_level or "")
        built = (
            nickname_mode.replace("{nickname}", base_name)
            .replace("{itemlevel}", item_level_text)
            .replace("{classname}", str(result.get("character_class") or ""))
            .replace("{servername}", str(result.get("server_name") or ""))
        )
        if built:
            try:
                await interaction.user.edit(nick=built[:32])
            except Exception:
                pass

    complete_message = (result.get("complete_message") or "").strip()
    if complete_message:
        try:
            await interaction.user.send(complete_message)
        except Exception:
            pass

    embed = discord.Embed(
        title="인증 완료",
        description=f"**{result.get('character_name') or ''}** 캐릭터로 인증되었습니다.",
        color=discord.Color.green(),
    )
    if class_name:
        embed.add_field(name="직업", value=str(class_name), inline=True)
    if server_name:
        embed.add_field(name="서버", value=str(server_name), inline=True)
    if result.get("item_level") is not None:
        embed.add_field(name="아이템 레벨", value=str(result.get("item_level")), inline=True)
    if result.get("guild_name"):
        embed.add_field(name="길드", value=str(result.get("guild_name")), inline=True)
    if assigned_roles:
        embed.add_field(name="지급된 역할", value=", ".join(assigned_roles), inline=False)

    char_image = str(result.get("char_image") or "").strip()
    if char_image:
        embed.set_thumbnail(url=char_image)

    await interaction.followup.send(embed=embed, ephemeral=True)

    log_channel_id = _to_int(result.get("log_channel_id"))
    if not log_channel_id:
        return

    log_channel = guild.get_channel(log_channel_id)
    if not log_channel:
        return

    log_embed = discord.Embed(title="로스트아크 인증 완료", color=discord.Color.green())
    log_embed.add_field(name="사용자", value=interaction.user.mention, inline=True)
    log_embed.add_field(name="캐릭터", value=str(result.get("character_name") or ""), inline=True)
    if class_name:
        log_embed.add_field(name="직업", value=str(class_name), inline=True)
    if server_name:
        log_embed.add_field(name="서버", value=str(server_name), inline=True)
    if result.get("item_level") is not None:
        log_embed.add_field(name="아이템 레벨", value=str(result.get("item_level")), inline=True)
    if result.get("guild_name"):
        log_embed.add_field(name="길드", value=str(result.get("guild_name")), inline=True)
    if assigned_roles:
        log_embed.add_field(name="지급된 역할", value=", ".join(assigned_roles), inline=False)
    if char_image:
        log_embed.set_thumbnail(url=char_image)

    try:
        await log_channel.send(embed=log_embed)
    except Exception:
        pass


async def _show_stove_guide(interaction: discord.Interaction) -> None:
    view = StoveConnectView()
    if interaction.response.is_done():
        try:
            await interaction.edit_original_response(content=None, view=view)
            return
        except Exception:
            pass
    await _send_ephemeral_view(interaction, view)


async def _show_linked_view(interaction: discord.Interaction, link_data: Dict[str, Any]) -> None:
    characters = link_data.get("characters") or []
    view = StoveLinkedDropdownView(link_data, characters)
    if interaction.response.is_done():
        try:
            await interaction.edit_original_response(content=None, view=view)
            return
        except Exception:
            pass
    await _send_ephemeral_view(interaction, view)


async def _handle_verify_entry(interaction: discord.Interaction) -> None:
    try:
        config_response = await http_client.get(
            f"/verify/{interaction.guild_id}/config",
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0),
        )
    except Exception as e:
        await _send_ephemeral_message(interaction, "인증 설정 조회 중 오류가 발생했습니다.")
        print(f"[verify] config fetch failed guild={interaction.guild_id} err={e!r}")
        return

    if config_response.status_code != 200:
        await _send_ephemeral_message(interaction, "인증 설정이 없습니다. 관리자에게 문의해 주세요.")
        return

    config = _safe_json(config_response)
    detailed_verify = _to_bool(config.get("detailed_verify"))

    if not detailed_verify:
        try:
            await interaction.response.send_modal(CharacterVerifyModal())
        except Exception as e:
            print(f"[verify] send modal failed guild={interaction.guild_id} user={interaction.user.id} err={e!r}")
            await _send_ephemeral_message(interaction, "인증 창을 열지 못했습니다. 다시 눌러 주세요.")
        return

    if not interaction.response.is_done():
        try:
            await interaction.response.send_message(
                "🔎 로스트아크 캐릭터 정보를 가져오는 중이에요... 잠시만 기다려 주세요.",
                ephemeral=True,
            )
        except Exception as e:
            print(f"[verify] send loading message failed guild={interaction.guild_id} user={interaction.user.id} err={e!r}")
            return
    else:
        try:
            await interaction.edit_original_response(
                content="🔎 로스트아크 캐릭터 정보를 가져오는 중이에요... 잠시만 기다려 주세요.",
                view=None,
            )
        except Exception as e:
            print(f"[verify] set loading message failed guild={interaction.guild_id} user={interaction.user.id} err={e!r}")

    try:
        link_resp = await http_client.get(
            f"/verify/stove/link/{interaction.user.id}",
            timeout=httpx.Timeout(connect=3.0, read=20.0, write=5.0, pool=5.0),
            max_attempts=2,
        )
    except Exception as e:
        print(f"[verify] stove link fetch failed user={interaction.user.id} err={e!r}")
        await _send_ephemeral_message(
            interaction,
            "STOVE 연동 정보 조회가 지연되고 있습니다. 잠시 후 다시 시도해 주세요.",
        )
        return

    link_data = _safe_json(link_resp)
    if link_resp.status_code == 200:
        if _to_bool(link_data.get("linked")):
            await _show_linked_view(interaction, link_data)
            return
        await _show_stove_guide(interaction)
        return

    print(
        f"[verify] stove link fetch non-200 user={interaction.user.id} "
        f"status={link_resp.status_code} detail={link_data.get('detail')!r}"
    )
    detail = link_data.get("detail") or link_resp.status_code
    if link_resp.status_code == 503 or _is_lostark_unavailable_detail(detail):
        await _send_ephemeral_embed(interaction, _build_lostark_unavailable_embed())
        return
    await _send_ephemeral_message(
        interaction,
        "STOVE 연동 정보를 확인하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    )


async def _handle_issue_challenge(
    interaction: discord.Interaction,
    stove_profile_id: str,
    source_message_id: Optional[int] = None,
) -> None:
    try:
        resp = await http_client.post(
            "/verify/stove/challenge",
            json={"user_id": str(interaction.user.id), "stove_value": stove_profile_id},
            timeout=httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0),
            max_attempts=2,
        )
    except httpx.TimeoutException:
        message = "OTP 발급 응답이 지연되고 있습니다. 다시 시도해 주세요."
        updated = await _edit_stove_flow_message(
            interaction,
            view=StoveConnectView(notice=message),
            source_message_id=source_message_id,
        )
        if not updated:
            await interaction.followup.send(message, ephemeral=True)
        return
    except httpx.HTTPError:
        message = "네트워크 오류로 OTP 발급에 실패했습니다."
        updated = await _edit_stove_flow_message(
            interaction,
            view=StoveConnectView(notice=message),
            source_message_id=source_message_id,
        )
        if not updated:
            await interaction.followup.send(message, ephemeral=True)
        return
    except Exception:
        message = "OTP 발급 처리 중 오류가 발생했습니다."
        updated = await _edit_stove_flow_message(
            interaction,
            view=StoveConnectView(notice=message),
            source_message_id=source_message_id,
        )
        if not updated:
            await interaction.followup.send(message, ephemeral=True)
        return

    data = _safe_json(resp)
    if resp.status_code != 200:
        message = _friendly_issue_error_message(resp.status_code, data)
        updated = await _edit_stove_flow_message(
            interaction,
            view=StoveConnectView(notice=message),
            source_message_id=source_message_id,
        )
        if not updated:
            await interaction.followup.send(message, ephemeral=True)
        return

    view = StoveConnectView(
        challenge_key=str(data.get("challenge_key") or ""),
        stove_profile_id=str(data.get("stove_profile_id") or stove_profile_id),
        ttl_seconds=_to_int(data.get("ttl_seconds")),
        notice="OTP 발급이 완료되었습니다.",
    )
    updated = await _edit_stove_flow_message(
        interaction,
        view=view,
        source_message_id=source_message_id,
    )
    if not updated:
        await interaction.followup.send(
            view=view,
            ephemeral=True,
        )


async def _handle_confirm_challenge(
    interaction: discord.Interaction,
    *,
    challenge_key: Optional[str] = None,
    stove_profile_id: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> None:
    await interaction.response.defer(invisible=True)
    source_message_id = getattr(getattr(interaction, "message", None), "id", None)

    restore_view: Optional[discord.ui.DesignerView] = None
    if challenge_key:
        restore_view = StoveConnectView(
            challenge_key=str(challenge_key),
            stove_profile_id=str(stove_profile_id or ""),
            ttl_seconds=ttl_seconds,
            notice="OTP 발급이 완료되었습니다.",
            processing=False,
        )
        await _edit_stove_flow_message(
            interaction,
            view=StoveConnectView(
                challenge_key=str(challenge_key),
                stove_profile_id=str(stove_profile_id or ""),
                ttl_seconds=ttl_seconds,
                notice="서버에서 STOVE 프로필을 검증 중입니다.",
                processing=True,
            ),
            source_message_id=source_message_id,
        )

    try:
        resp = await http_client.post(
            "/verify/stove/confirm",
            json={"user_id": str(interaction.user.id)},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            max_attempts=2,
        )
    except httpx.TimeoutException:
        message = "STOVE 검증 응답이 지연되고 있습니다. 다시 시도해 주세요."
        if restore_view is not None:
            await _edit_stove_flow_message(interaction, view=restore_view, source_message_id=source_message_id)
        await interaction.followup.send(message, ephemeral=True)
        return
    except httpx.HTTPError:
        message = "네트워크 오류로 STOVE 검증에 실패했습니다."
        if restore_view is not None:
            await _edit_stove_flow_message(interaction, view=restore_view, source_message_id=source_message_id)
        await interaction.followup.send(message, ephemeral=True)
        return
    except Exception:
        message = "STOVE 검증 처리 중 오류가 발생했습니다."
        if restore_view is not None:
            await _edit_stove_flow_message(interaction, view=restore_view, source_message_id=source_message_id)
        await interaction.followup.send(message, ephemeral=True)
        return

    data = _safe_json(resp)
    if resp.status_code != 200:
        message = _friendly_confirm_error_message(resp.status_code, data)
        if restore_view is not None:
            await _edit_stove_flow_message(interaction, view=restore_view, source_message_id=source_message_id)
        await interaction.followup.send(message, ephemeral=True)
        return

    characters = data.get("characters") or []
    view = StoveLinkedDropdownView(data, characters)
    updated = await _edit_stove_flow_message(
        interaction,
        view=view,
        source_message_id=source_message_id,
    )
    if not updated:
        await interaction.followup.send(
            view=view,
            ephemeral=True,
        )


async def _handle_show_settings(
    interaction: discord.Interaction,
    cached_link_data: Optional[Dict[str, Any]] = None,
) -> None:
    await interaction.response.defer(ephemeral=True)

    cached_data = cached_link_data if isinstance(cached_link_data, dict) else {}
    if str(cached_data.get("stove_profile_id") or "").strip():
        await interaction.followup.send(
            view=StoveSettingsView(cached_data, interaction.user.mention),
            ephemeral=True,
        )
        return

    try:
        resp = await http_client.get(
            f"/verify/stove/link/{interaction.user.id}",
            timeout=httpx.Timeout(connect=3.0, read=15.0, write=5.0, pool=5.0),
            max_attempts=2,
        )
    except Exception:
        await interaction.followup.send("STOVE 연결 정보를 불러오는 중 오류가 발생했습니다.", ephemeral=True)
        return

    data = _safe_json(resp)
    if resp.status_code != 200 or not bool(data.get("linked")):
        await interaction.followup.send("연동된 STOVE 정보가 없습니다.", ephemeral=True)
        return

    await interaction.followup.send(
        view=StoveSettingsView(data, interaction.user.mention),
        ephemeral=True,
    )


async def _handle_disconnect(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        resp = await http_client.delete(f"/verify/stove/link/{interaction.user.id}")
    except Exception:
        await interaction.followup.send("연결 해제 처리 중 오류가 발생했습니다.", ephemeral=True)
        return

    data = _safe_json(resp)
    if resp.status_code != 200:
        await interaction.followup.send(
            f"연결 해제 실패: {data.get('detail') or resp.status_code}",
            ephemeral=True,
        )
        return

    await interaction.followup.send("STOVE 연결이 해제되었습니다.", ephemeral=True)


async def _handle_refresh_link(
    interaction: discord.Interaction,
    settings_data: Optional[Dict[str, Any]] = None,
) -> None:
    source_message_id = getattr(getattr(interaction, "message", None), "id", None)
    base_settings = settings_data if isinstance(settings_data, dict) else {}
    processing_view = StoveSettingsView(
        base_settings,
        interaction.user.mention,
        processing=True,
        notice="### 갱신 중\n대표 캐릭터 정보를 새로 확인하고 있어요.",
    )

    response_done = interaction.response.is_done()
    if not response_done:
        try:
            await interaction.response.edit_message(view=processing_view)
            response_done = True
        except Exception:
            pass

    if not response_done:
        try:
            await interaction.response.defer(ephemeral=True)
            response_done = True
        except Exception:
            pass

    if source_message_id is not None:
        await _edit_stove_flow_message(
            interaction,
            view=processing_view,
            source_message_id=source_message_id,
        )

    try:
        resp = await http_client.post(
            f"/verify/stove/link/{interaction.user.id}/refresh",
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
    except httpx.TimeoutException:
        message = "### 갱신 지연\n대표 캐릭터 정보 갱신이 지연되고 있어요. 잠시 후 다시 시도해 주세요."
        restored_view = StoveSettingsView(base_settings, interaction.user.mention, notice=message)
        updated = await _edit_stove_flow_message(interaction, view=restored_view, source_message_id=source_message_id)
        if not updated and interaction.response.is_done():
            await interaction.followup.send("대표 캐릭터 정보 갱신이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.", ephemeral=True)
        return
    except Exception:
        message = "### 갱신 실패\n대표 캐릭터 정보 갱신 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."
        restored_view = StoveSettingsView(base_settings, interaction.user.mention, notice=message)
        updated = await _edit_stove_flow_message(interaction, view=restored_view, source_message_id=source_message_id)
        if not updated and interaction.response.is_done():
            await interaction.followup.send("대표 캐릭터 정보 갱신 중 오류가 발생했습니다.", ephemeral=True)
        return

    data = _safe_json(resp)
    if resp.status_code != 200:
        detail = data.get("detail") or resp.status_code
        if resp.status_code == 503 or _is_lostark_unavailable_detail(detail):
            message = "### 로스트아크 점검 안내\n현재 로스트아크 서버에 접속할 수 없어요.\n정기점검 이후 다시 시도해 주세요."
        else:
            message = f"### 갱신 실패\n{detail}"
        restored_view = StoveSettingsView(base_settings, interaction.user.mention, notice=message)
        updated = await _edit_stove_flow_message(interaction, view=restored_view, source_message_id=source_message_id)
        if not updated and interaction.response.is_done():
            if resp.status_code == 503 or _is_lostark_unavailable_detail(detail):
                await interaction.followup.send(embed=_build_lostark_unavailable_embed(), ephemeral=True)
            else:
                await interaction.followup.send(f"대표 캐릭터 갱신 실패: {detail}", ephemeral=True)
        return

    refreshed_view = StoveSettingsView(
        data,
        interaction.user.mention,
        notice="### 갱신 완료\n대표 캐릭터 정보를 최신 상태로 반영했어요.",
    )
    updated = await _edit_stove_flow_message(
        interaction,
        view=refreshed_view,
        source_message_id=source_message_id,
    )
    if not updated and interaction.response.is_done():
        await interaction.followup.send(view=refreshed_view, ephemeral=True)

async def handle_verify_button(interaction: discord.Interaction):
    try:
        custom_id = str((getattr(interaction, "data", None) or {}).get("custom_id") or "")
        if custom_id == "verify_button":
            await _handle_verify_entry(interaction)
            return
    except Exception as e:
        print(f"[verify] handle_verify_button error guild={getattr(interaction, 'guild_id', None)} user={getattr(getattr(interaction, 'user', None), 'id', None)} err={e!r}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"인증 처리 중 오류가 발생했습니다: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"인증 처리 중 오류가 발생했습니다: {e}",
                    ephemeral=True,
                )
        except Exception:
            pass


async def send_verification_embed(channel: discord.TextChannel, guild_id: int):
    try:
        response = await http_client.get(f"/verify/{guild_id}/embed")
        if response.status_code != 200:
            return False

        data = response.json()
        title = data.get("title", "로스트아크 인증하기!")
        description = data.get("description")
        if not description or not data.get("has_config"):
            title = "로스트아크 인증하기!"
            description = (
                "버튼을 누르면 닉네임을 입력받는 창이 표시돼요.\n"
                "닉네임을 입력하면 해당 캐릭터의 정보를 바탕으로,\n"
                "직업 `역할`, `서버 역할`, `별명 변경`을 자동으로 진행해요.\n"
                f"모두 완료시 **{channel.guild.name}** 에 인증이 완료됩니다!"
            )

            if _to_bool(data.get("detailed_verify")):
                stove_notice = (
                    "\n\n### STOVE 연동 안내\n"
                    "이 서버는 상세 인증이 활성화되어 있어 STOVE 연동이 필요해요.\n"
                    "인증 버튼을 누른 뒤 안내에 따라 STOVE 연동을 완료해 주세요.\n"
                    "-# TIP : STOVE 연동은 계정 기준 1회만 하면 되고, 다른 서버에서는 다시 진행하지 않아도 돼요."
                )
                description = f"{str(description or '').rstrip()}{stove_notice}"

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())

        from commands.verify_config import VerifyButton

        view = VerifyButton()
        await channel.send(embed=embed, view=view)
        return True
    except Exception as e:
        print(f"인증 embed 전송 오류: {e}")
        return False