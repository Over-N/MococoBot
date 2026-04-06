import discord
from core.http_client import http_client
from typing import Dict, Any, Optional, Iterable, List, Tuple

EMOJI_ON = "🟢"
EMOJI_OFF = "🔴"
MAX_OPTIONS = 25

def _to_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "on", "y", "t"}
    return default

def _to_int_or_none(v: Any) -> Optional[int]:
    if v in (None, "", 0, "0"):
        return None
    try:
        return int(v)
    except Exception:
        return None

def _as_interaction(ctx_or_inter: Any) -> discord.Interaction:
    return getattr(ctx_or_inter, "interaction", ctx_or_inter)

def _safe_json(resp) -> Dict[str, Any]:
    try:
        j = resp.json()
        return j or {}
    except Exception:
        return {}

async def _send_ephemeral(inter: discord.Interaction, content: str):
    try:
        await inter.followup.send(content, ephemeral=True)
    except Exception:
        # 이미 응답 완료/삭제 등으로 실패할 수 있음
        pass

async def _refresh_main(inter: discord.Interaction, guild_id: int, guild: discord.Guild):
    """서버 설정 재조회 후 메인 임베드/뷰로 갱신"""
    settings = await _fetch_server_settings(guild_id)
    view = ServerConfigView(guild_id, settings)
    embed = view._build_embed(guild)
    await inter.edit_original_response(embed=embed, view=view)
    return view

async def _fetch_server_settings(guild_id: int) -> Dict[str, Any]:
    """API 응답 래핑(data 유/무)과 타입 정규화 + 네트워크 예외 대비"""
    try:
        r = await http_client.get(f"/discord/server/{guild_id}")
    except Exception:
        return {}
    if getattr(r, "status_code", 0) != 200:
        return {}

    raw = _safe_json(r)
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    
    def _norm_mode(v, legacy_bool: Optional[bool]) -> int:
        try:
            return int(v) & 0b11
        except Exception:
            return 3 if legacy_bool else 0

    legacy = _to_bool(data.get("alert_timer"), default=False)
    mode = _norm_mode(data.get("alert_timer_mode"), legacy)
    
    return {
        "id": data.get("id"),
        "guild_id": str(data["guild_id"]) if data.get("guild_id") is not None else None,
        "forum_channel_id": _to_int_or_none(data.get("forum_channel_id")),
        "chat_channel_id": _to_int_or_none(data.get("chat_channel_id")),
        "cancel_join_channel_id": _to_int_or_none(data.get("cancel_join_channel_id")),
        "mention_role_id": _to_int_or_none(data.get("mention_role_id")),
        "alert_timer": (mode != 0),
        "alert_timer_mode": mode,
        "alert_start": _to_bool(data.get("alert_start"), default=False),
        "admin_roles": data.get("admin_roles"),
    }

async def _post_server_settings(guild_id: int, payload: Dict[str, Any]) -> bool:
    try:
        r = await http_client.post(f"/discord/server/{guild_id}", json=payload)
        return r.status_code in (200, 201)
    except Exception:
        return False

def _sort_text_channels(chs: Iterable[discord.TextChannel]) -> List[discord.TextChannel]:
    def sort_key(ch: discord.TextChannel):
        cat = getattr(ch, "category", None)
        cat_pos = getattr(cat, "position", None)
        if cat_pos is None:
            cat_pos = 10**9  # 카테고리 없는 채널은 뒤로
        ch_pos = getattr(ch, "position", None)
        if ch_pos is None:
            ch_pos = 10**9
        return (cat_pos, ch_pos, ch.name.lower())
    return sorted(list(chs), key=sort_key)

class CancelJoinChannelSelect(discord.ui.Select):
    """참가 취소 채널 선택"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild):
        current_id = current_settings.get("cancel_join_channel_id")
        options: List[discord.SelectOption] = []
        for ch in _sort_text_channels(guild.text_channels)[:MAX_OPTIONS]:
            options.append(discord.SelectOption(
                label=f"#{ch.name}",
                description=(ch.topic or "설명 없음")[:50],
                value=str(ch.id),
                default=(current_id == ch.id),
            ))

        super().__init__(
            placeholder="참가 취소 알림을 받을 채널을 선택하세요...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            channel_id = int(self.values[0])
            selected_channel = self.guild.get_channel(channel_id)
            if not selected_channel:
                return await _send_ephemeral(interaction, "❌ 선택한 채널을 찾을 수 없습니다.")

            ok = await _post_server_settings(self.guild_id, {"cancel_join_channel_id": channel_id})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 설정 저장에 실패했습니다.")

            self.current_settings["cancel_join_channel_id"] = channel_id
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, f"✅ 참가 취소 알림 채널이 {selected_channel.mention}로 설정되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"1 ❌ 오류가 발생했습니다: {e}")

class CancelJoinChannelView(discord.ui.View):
    """참가 취소 채널 선택/삭제 뷰 (메인 메시지를 교체 편집)"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        
        # 채널 수가 많을 경우 안내 메시지 추가
        text_channels = _sort_text_channels(guild.text_channels)
        if len(text_channels) > MAX_OPTIONS:
            too_many_channels = True
        else:
            too_many_channels = False
            self.add_item(CancelJoinChannelSelect(guild_id, current_settings, guild))
        
        # 채널이 너무 많으면 검색 버튼만 활성화
        if too_many_channels:
            placeholder = discord.ui.Button(
                label=f"채널이 너무 많아요 ({len(text_channels)}개)", 
                style=discord.ButtonStyle.secondary, 
                disabled=True
            )
            self.add_item(placeholder)

    @discord.ui.button(label="채널 검색", style=discord.ButtonStyle.primary, emoji="🔍")
    async def search_channel(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = ChannelSearchModal(self.guild_id, self.current_settings, self.guild)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def clear_cancel_join_channel(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            ok = await _post_server_settings(self.guild_id, {"cancel_join_channel_id": None})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 삭제에 실패했습니다.")

            self.current_settings["cancel_join_channel_id"] = None
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, "🗑️ 참가 취소 알림 채널 설정이 삭제되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 오류가 발생했습니다: {e}")

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back_to_main(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        await _refresh_main(interaction, self.guild_id, self.guild)


class RoleSearchModal(discord.ui.Modal):
    """역할 검색 모달"""
    def __init__(self, role_type: str, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild):
        super().__init__(title=f"{role_type} 역할 검색")
        self.role_type = role_type
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        self.add_item(discord.ui.InputText(
            label="역할 이름",
            placeholder="검색할 역할 이름을 입력하세요 (일부만 입력해도 됩니다)",
            required=True,
            max_length=50
        ))

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            term = (self.children[0].value or "").strip().lower()
            if not term:
                return await _send_ephemeral(interaction, "❌ 검색어를 입력해주세요.")

            roles = [
                r for r in self.guild.roles
                if (r.name != "@everyone" and not r.managed and term in r.name.lower())
            ]
            if not roles:
                return await _send_ephemeral(interaction, f"❌ '{term}'를 포함하는 역할을 찾을 수 없습니다.")

            limited = roles[:MAX_OPTIONS]
            limit_msg = "" if len(roles) <= MAX_OPTIONS else "\n⚠️ 검색 결과가 많아 상위 25개만 표시됩니다."

            embed = discord.Embed(
                title=f"🔍 '{term}' 검색 결과 - {self.role_type} 역할",
                description=f"검색된 {len(roles)}개의 역할 중에서 선택하세요.{limit_msg}",
                color=discord.Color.green()
            )

            if self.role_type == "관리자":
                view = AdminRoleSearchView(self.guild_id, self.current_settings, self.guild, limited)
            else:
                view = MentionRoleSearchView(self.guild_id, self.current_settings, self.guild, limited)

            await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await _send_ephemeral(interaction, f"3 ❌ 오류가 발생했습니다: {e}")

class AdminRoleSearchSelect(discord.ui.Select):
    """검색된 관리자 역할 선택"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild, roles: List[discord.Role]):
        current_admin_role = str(current_settings.get("admin_roles")) if current_settings.get("admin_roles") else None

        options = [
            discord.SelectOption(
                label=r.name,
                description=f"멤버 수: {len(r.members)}명" + (" | 관리자 권한" if r.permissions.administrator else ""),
                value=str(r.id),
                default=(str(r.id) == current_admin_role),
            )
            for r in roles
        ]

        super().__init__(placeholder="관리자 역할을 선택하세요...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            role_id = self.values[0]
            selected = self.guild.get_role(int(role_id))
            if not selected:
                return await _send_ephemeral(interaction, "❌ 선택한 역할을 찾을 수 없습니다.")

            ok = await _post_server_settings(self.guild_id, {"admin_roles": role_id})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 설정 저장에 실패했습니다.")

            self.current_settings["admin_roles"] = role_id
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, f"✅ 관리자 역할이 {selected.mention}로 설정되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"4 ❌ 오류가 발생했습니다: {e}")

class MentionRoleSearchSelect(discord.ui.Select):
    """검색된 멘션 역할 선택"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild, roles: List[discord.Role]):
        current_id = current_settings.get("mention_role_id")

        options = [
            discord.SelectOption(
                label=r.name,
                description=f"멤버 수: {len(r.members)}명",
                value=str(r.id),
                default=(current_id == r.id),
            )
            for r in roles
        ]

        super().__init__(placeholder="기본 멘션 역할을 선택하세요...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            role_id = int(self.values[0])
            selected = self.guild.get_role(role_id)
            if not selected:
                return await _send_ephemeral(interaction, "❌ 선택한 역할을 찾을 수 없습니다.")

            ok = await _post_server_settings(self.guild_id, {"mention_role_id": role_id})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 설정 저장에 실패했습니다.")

            self.current_settings["mention_role_id"] = role_id
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, f"✅ 기본 멘션 역할이 {selected.mention}로 설정되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"5 ❌ 오류가 발생했습니다: {e}")

class AdminRoleSearchView(discord.ui.View):
    """관리자 역할 검색 결과 뷰"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild, roles: List[discord.Role]):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        self.add_item(AdminRoleSearchSelect(guild_id, current_settings, guild, roles))

    @discord.ui.button(label="다시 검색", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def search_again(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleSearchModal("관리자", self.guild_id, self.current_settings, self.guild))

    @discord.ui.button(label="설정 삭제", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_admin_role(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            ok = await _post_server_settings(self.guild_id, {"admin_roles": None})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 삭제에 실패했습니다.")

            self.current_settings["admin_roles"] = None
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, "🗑️ 관리자 역할 설정이 삭제되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"6 ❌ 오류가 발생했습니다: {e}")

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back_to_main(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        await _refresh_main(interaction, self.guild_id, self.guild)

class MentionRoleSearchView(discord.ui.View):
    """멘션 역할 검색 결과 뷰"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild, roles: List[discord.Role]):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        self.add_item(MentionRoleSearchSelect(guild_id, current_settings, guild, roles))

    @discord.ui.button(label="다시 검색", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def search_again(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleSearchModal("멘션", self.guild_id, self.current_settings, self.guild))

    @discord.ui.button(label="설정 삭제", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_mention_role(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            ok = await _post_server_settings(self.guild_id, {"mention_role_id": None})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 삭제에 실패했습니다.")

            self.current_settings["mention_role_id"] = None
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, "🗑️ 기본 멘션 역할 설정이 삭제되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"7 ❌ 오류가 발생했습니다: {e}")

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back_to_main(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        await _refresh_main(interaction, self.guild_id, self.guild)

# ==============================
# Channel Search Modal/Views
# ==============================
class ChannelSearchModal(discord.ui.Modal):
    """채널 검색 모달"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild):
        super().__init__(title="참가 취소 채널 검색")
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        self.add_item(discord.ui.InputText(
            label="채널 이름",
            placeholder="검색할 채널 이름을 입력하세요 (일부만 입력해도 됩니다)",
            required=True,
            max_length=50
        ))

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            search_term = (self.children[0].value or "").strip().lower()
            if not search_term:
                return await _send_ephemeral(interaction, "❌ 검색어를 입력해주세요.")

            # 채널 검색
            matching_channels = []
            for channel in self.guild.text_channels:
                if search_term in channel.name.lower():
                    matching_channels.append(channel)

            if not matching_channels:
                return await _send_ephemeral(interaction, f"❌ '{search_term}'를 포함하는 채널을 찾을 수 없습니다.")

            # 검색 결과가 너무 많으면 제한
            limited = matching_channels[:MAX_OPTIONS]
            limit_msg = "" if len(matching_channels) <= MAX_OPTIONS else f"\n⚠️ 검색 결과가 많아 상위 {MAX_OPTIONS}개만 표시됩니다."

            embed = discord.Embed(
                title=f"🔍 '{search_term}' 검색 결과 - 참가 취소 채널",
                description=f"검색된 {len(matching_channels)}개의 채널 중에서 선택하세요.{limit_msg}",
                color=discord.Color.green()
            )

            view = CancelJoinChannelSearchResultView(self.guild_id, self.current_settings, self.guild, limited)
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 채널 검색 중 오류가 발생했습니다: {e}")

class CancelJoinChannelSearchSelect(discord.ui.Select):
    """검색된 참가 취소 채널 선택"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild, channels: List[discord.TextChannel]):
        current_id = current_settings.get("cancel_join_channel_id")

        options = []
        for ch in channels:
            options.append(discord.SelectOption(
                label=f"#{ch.name}",
                description=(ch.topic or "설명 없음")[:50],
                value=str(ch.id),
                default=(current_id == ch.id),
            ))

        super().__init__(placeholder="참가 취소 알림을 받을 채널을 선택하세요...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            channel_id = int(self.values[0])
            selected_channel = self.guild.get_channel(channel_id)
            if not selected_channel:
                return await _send_ephemeral(interaction, "❌ 선택한 채널을 찾을 수 없습니다.")

            ok = await _post_server_settings(self.guild_id, {"cancel_join_channel_id": channel_id})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 설정 저장에 실패했습니다.")

            self.current_settings["cancel_join_channel_id"] = channel_id
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, f"✅ 참가 취소 알림 채널이 {selected_channel.mention}로 설정되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 채널 설정 중 오류가 발생했습니다: {e}")

class CancelJoinChannelSearchResultView(discord.ui.View):
    """채널 검색 결과 뷰"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild, channels: List[discord.TextChannel]):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        self.add_item(CancelJoinChannelSearchSelect(guild_id, current_settings, guild, channels))

    @discord.ui.button(label="다시 검색", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def search_again(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = ChannelSearchModal(self.guild_id, self.current_settings, self.guild)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="설정 삭제", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_channel_setting(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            ok = await _post_server_settings(self.guild_id, {"cancel_join_channel_id": None})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 삭제에 실패했습니다.")

            self.current_settings["cancel_join_channel_id"] = None
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, "🗑️ 참가 취소 알림 채널 설정이 삭제되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 채널 설정 삭제 중 오류가 발생했습니다: {e}")

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back_to_main(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        await _refresh_main(interaction, self.guild_id, self.guild)

# ==============================
# Main View
# ==============================
class ServerConfigView(discord.ui.View):
    """서버 설정 메인 뷰"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self._apply_button_states()

    # 공용 embed 빌더
    def _build_embed(self, guild: discord.Guild) -> discord.Embed:
        mode = int(self.current_settings.get("alert_timer_mode") or 0) & 0b11
        start_enabled = _to_bool(self.current_settings.get("alert_start"), False)
        
        def _mode_text(m: int) -> str:
            ten = "ON" if (m & 0b01) else "OFF"
            hour = "ON" if (m & 0b10) else "OFF"
            onoff = f"{EMOJI_ON} 켜짐" if m != 0 else f"{EMOJI_OFF} 꺼짐"
            return f"{onoff}  —  (10분:{ten}, 1시간:{hour})"

        embed = discord.Embed(
            title="⚙️ 서버 설정 관리",
            description="현재 서버의 레이드 시스템 설정 상태입니다.\n아래 버튼들로 설정을 변경할 수 있어요.",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="🔔 알림 설정",
            value=(
                f"**타이머 알림**: {_mode_text(mode)}\n" +
                (
                    f"**시작 알림** (개인 DM): {EMOJI_ON} 켜짐" if start_enabled
                    else f"**시작 알림** (개인 DM): {EMOJI_OFF} 꺼짐"
                )
            ),
            inline=False,
        )

        # 채널 설정
        get_ch = guild.get_channel
        forum_channel = get_ch(self.current_settings.get("forum_channel_id")) if self.current_settings.get("forum_channel_id") else None
        chat_channel = get_ch(self.current_settings.get("chat_channel_id")) if self.current_settings.get("chat_channel_id") else None
        cancel_join_channel = get_ch(self.current_settings.get("cancel_join_channel_id")) if self.current_settings.get("cancel_join_channel_id") else None

        channel_info = [
            f"**포럼 채널**: {forum_channel.mention if forum_channel else '❌ 미설정 (/setups 명령어로 설정)'}",
            f"**채팅 채널**: {chat_channel.mention if chat_channel else '❌ 미설정 (/setups 명령어로 설정)'}",
            f"**참가 취소 채널**: {cancel_join_channel.mention if cancel_join_channel else '❌ 미설정'}",
        ]
        embed.add_field(name="📂 채널 설정", value="\n".join(channel_info), inline=False)

        # 역할 설정 (단일 관리자 역할 + 멘션 역할)
        mention_role = guild.get_role(self.current_settings.get("mention_role_id")) if self.current_settings.get("mention_role_id") else None

        admin_role = None
        admin_role_id = self.current_settings.get("admin_roles")
        if admin_role_id:
            try:
                admin_role = guild.get_role(int(admin_role_id))
            except (ValueError, TypeError):
                admin_role = None

        role_info = [
            f"**관리자 역할**: {admin_role.mention if admin_role else '❌ 미설정'}",
            f"**기본 멘션 역할**: {mention_role.mention if mention_role else '❌ 미설정'}",
        ]
        embed.add_field(name="👥 역할 설정", value="\n".join(role_info), inline=False)

        embed.set_footer(text="💡 포럼/채팅 채널은 /setups 명령어로 자동 설정됩니다.")
        return embed

    def _apply_button_states(self):
        mode = int(self.current_settings.get("alert_timer_mode") or 0) & 0b11
        start_enabled = _to_bool(self.current_settings.get("alert_start"), False)

        # ON/OFF 버튼 표시는 "모드 != 0" 기준
        self.timer_button.label = f"타이머 알림 {'🟢' if mode != 0 else '🔴'}"
        self.timer_button.style = discord.ButtonStyle.success if mode != 0 else discord.ButtonStyle.danger

        self.start_button.label = f"시작 알림 {'🟢' if start_enabled else '🔴'}"
        self.start_button.style = discord.ButtonStyle.success if start_enabled else discord.ButtonStyle.danger

    @staticmethod
    async def show_config_menu(ctx):
        """서버 설정 메뉴 표시"""
        inter = _as_interaction(ctx)
        try:
            await inter.response.defer(ephemeral=True)
            settings = await _fetch_server_settings(inter.guild_id)
            view = ServerConfigView(inter.guild_id, settings)
            embed = view._build_embed(inter.guild)
            await inter.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await inter.edit_original_response(content=f"8 ❌ 오류가 발생했습니다: {e}")

    def _update_view(self, guild: discord.Guild) -> discord.Embed:
        self._apply_button_states()
        return self._build_embed(guild)

    @discord.ui.button(label="타이머 알림", style=discord.ButtonStyle.success, emoji="⏰")
    async def timer_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            cur_mode = int(self.current_settings.get("alert_timer_mode") or 0) & 0b11
            new_mode = 0 if cur_mode != 0 else 3   # OFF ↔ 둘 다
            ok = await _post_server_settings(self.guild_id, {"alert_timer_mode": new_mode})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 타이머 알림 설정 저장에 실패했습니다.")
            # 로컬 상태 반영(레거시 bool도 같이 갱신)
            self.current_settings["alert_timer_mode"] = new_mode
            self.current_settings["alert_timer"] = (new_mode != 0)
            await interaction.edit_original_response(embed=self._update_view(interaction.guild), view=self)
        except Exception as e:
            await _send_ephemeral(interaction, f"9 ❌ 오류가 발생했습니다: {e}")
            
    @discord.ui.button(label="타이머 상세 설정", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def open_timer_detail(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            embed = discord.Embed(
                title="⏱️ 타이머 상세 설정",
                description="10분 전 / 1시간 전 알림을 개별로 켜고 끌 수 있어요.",
                color=discord.Color.blurple(),
            )
            view = AlertTimerConfigView(self.guild_id, self.current_settings, interaction.guild)
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 상세 설정 열기 중 오류: {e}")

    @discord.ui.button(label="시작 알림", style=discord.ButtonStyle.success, emoji="🔔")
    async def start_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            new_status = not _to_bool(self.current_settings.get("alert_start"), False)
            ok = await _post_server_settings(self.guild_id, {"alert_start": new_status})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 시작 알림 설정 저장에 실패했습니다.")
            self.current_settings["alert_start"] = new_status
            await interaction.edit_original_response(embed=self._update_view(interaction.guild), view=self)
        except Exception as e:
            await _send_ephemeral(interaction, f"10 ❌ 오류가 발생했습니다: {e}")

    @discord.ui.button(label="관리자 역할 설정", style=discord.ButtonStyle.secondary, emoji="👑")
    async def set_admin_roles(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleSearchModal("관리자", self.guild_id, self.current_settings, interaction.guild))

    @discord.ui.button(label="참가 취소 채널 설정", style=discord.ButtonStyle.secondary, emoji="📂")
    async def set_cancel_channel(self, button: discord.ui.Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📂 참가 취소 채널 설정",
            description="참가 취소 알림을 받을 채널을 선택하거나, 설정을 삭제할 수 있어요.",
            color=discord.Color.blue(),
        )
        view = CancelJoinChannelView(self.guild_id, self.current_settings, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="멘션 역할 설정", style=discord.ButtonStyle.secondary, emoji="👥")
    async def set_mention_role(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleSearchModal("멘션", self.guild_id, self.current_settings, interaction.guild))

    @discord.ui.button(label="현재 설정 새로고침", style=discord.ButtonStyle.primary, emoji="🔄")
    async def refresh_settings(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            self.current_settings = await _fetch_server_settings(self.guild_id)
            await interaction.edit_original_response(embed=self._update_view(interaction.guild), view=self)
        except Exception as e:
            await _send_ephemeral(interaction, f"11 ❌ 오류가 발생했습니다: {e}")

class AlertTimerSelect(discord.ui.Select):
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild):
        mode = int(current_settings.get("alert_timer_mode") or 0) & 0b11
        options = [
            discord.SelectOption(label="끄기 (OFF)", value="0", description="모든 타이머 알림 끔", default=(mode == 0)),
            discord.SelectOption(label="10분 전만", value="1", description="10분 전 알림만 켬", default=(mode == 1)),
            discord.SelectOption(label="1시간 전만", value="2", description="1시간 전 알림만 켬", default=(mode == 2)),
            discord.SelectOption(label="둘 다 켜기", value="3", description="10분 전 + 1시간 전", default=(mode == 3)),
        ]
        super().__init__(placeholder="타이머 모드를 선택하세요...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            new_mode = int(self.values[0]) & 0b11
            ok = await _post_server_settings(self.guild_id, {"alert_timer_mode": new_mode})
            if not ok:
                return await _send_ephemeral(interaction, "❌ 저장에 실패했습니다.")
            # 로컬 반영
            self.current_settings["alert_timer_mode"] = new_mode
            self.current_settings["alert_timer"] = (new_mode != 0)
            # 메인으로 복귀
            await _refresh_main(interaction, self.guild_id, self.guild)
            await _send_ephemeral(interaction, "✅ 타이머 상세 설정이 저장되었습니다.")
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 타이머 설정 저장 중 오류: {e}")

class AlertTimerConfigView(discord.ui.View):
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings
        self.guild = guild
        self.add_item(AlertTimerSelect(guild_id, current_settings, guild))

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back_to_main(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        await _refresh_main(interaction, self.guild_id, self.guild)