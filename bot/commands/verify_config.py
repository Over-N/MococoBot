
import asyncio
import discord
from core.http_client import http_client
from core.config import SERVER_LIST
from typing import Dict, Any, List, Tuple, Optional, Iterable

MAX_OPTIONS = 25
ROLE_CREATE_SLEEP = 0.25

def _to_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None

def _resolve_role(guild: discord.Guild, role_id_like) -> Optional[discord.Role]:
    rid = _to_int(role_id_like)
    return guild.get_role(rid) if rid else None

def _resolve_channel(guild: discord.Guild, channel_id_like) -> Optional[discord.abc.GuildChannel]:
    cid = _to_int(channel_id_like)
    return guild.get_channel(cid) if cid else None

def _safe_json(resp) -> Dict[str, Any]:
    try:
        return resp.json() or {}
    except Exception:
        return {}

async def _send_ephemeral(inter: discord.Interaction, content: str):
    if not content:
        return
    try:
        await inter.followup.send(content, ephemeral=True)
    except Exception:
        pass

async def _fetch_config(guild_id: int) -> Dict[str, Any]:
    try:
        r = await http_client.get(f"/verify/{guild_id}/config")
        return _safe_json(r) if r.status_code == 200 else {}
    except Exception:
        return {}

async def _post_config(guild_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        r = await http_client.post(f"/verify/{guild_id}/config", json=payload)
        if r.status_code in (200, 201):
            return await _fetch_config(guild_id)
    except Exception:
        pass
    return None

async def _refresh_main(inter: discord.Interaction, view: "VerifyConfigView"):
    """현재 설정 재조회 후 메인 임베드/뷰를 '새 인스턴스'로 갱신"""
    settings = await _fetch_config(view.guild_id)
    new_view = VerifyConfigView(view.guild_id, settings or view.current_settings, inter.guild)
    embed = new_view._build_embed(inter.guild)
    await inter.edit_original_response(embed=embed, view=new_view)

async def _create_verify_roles(guild: discord.Guild) -> Tuple[List[str], List[str]]:
    created_roles: List[str] = []
    failed_roles: List[str] = []

    me = guild.me
    me_perms = me.guild_permissions if me else None
    if not me_perms or not me_perms.manage_roles:
        return [], ["역할 생성 실패: 봇에 역할 관리 권한이 없습니다."]

    # 이미 존재하는 이름 캐시
    existing_names = {r.name for r in guild.roles}

    # 서버 역할
    for name in SERVER_LIST:
        try:
            if name in existing_names:
                continue
            await guild.create_role(
                name=f"서버({name})",
                color=discord.Color.blue(),
                mentionable=True,
                reason="인증 시스템용 서버 역할 자동 생성"
            )
            existing_names.add(name)
            created_roles.append(name)
            await asyncio.sleep(ROLE_CREATE_SLEEP)
        except discord.Forbidden:
            failed_roles.append(f"{name} (권한 부족)")
        except discord.HTTPException as e:
            failed_roles.append(f"{name} (생성 실패: {e})")

    # 직업 역할
    try:
        r = await http_client.get("/character/class")
        if r.status_code == 200:
            for item in (_safe_json(r).get("data") or []):
                class_name = item.get("name")
                if not class_name or class_name in existing_names:
                    continue
                try:
                    await guild.create_role(
                        name=class_name,
                        color=discord.Color.green(),
                        mentionable=True,
                        reason="인증 시스템용 직업 역할 자동 생성"
                    )
                    existing_names.add(class_name)
                    created_roles.append(class_name)
                    await asyncio.sleep(ROLE_CREATE_SLEEP)
                except discord.Forbidden:
                    failed_roles.append(f"{class_name} (권한 부족)")
                except discord.HTTPException as e:
                    failed_roles.append(f"{class_name} (생성 실패: {e})")
        else:
            failed_roles.append("직업 목록 조회 실패")
    except Exception as e:
        failed_roles.append(f"직업 목록 조회 오류: {e}")

    return created_roles, failed_roles

class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="인증하기", style=discord.ButtonStyle.primary, emoji="✅", custom_id="verify_button")
    async def verify_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        pass  # main에서 처리

class VerifyConfigView(discord.ui.View):
    """인증 설정 메인 뷰"""
    def __init__(self, guild_id: int, current_settings: Dict[str, Any], guild: discord.Guild = None):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_settings = current_settings or {}
        self.guild = guild
        self._apply_button_states()

    def _build_embed(self, guild: discord.Guild) -> discord.Embed:
        self.guild = guild
        embed = discord.Embed(
            title="⚙️ 로스트아크 인증 설정 정보",
            description="🔗 인증하기 설정은 https://mococobot.kr → 서버설정 에서 가능해요!\n\n인증 상세 설정 방법은 [여기(클릭)](https://mococobot.notion.site/2b69873af556808ba58aeced57cc6a15?source=copy_link)를 참고하세요.",
            color=discord.Color.blue()
        )
        # 기본 설정
        auto_nickname = bool(self.current_settings.get('auto_nickname', True))
        embed.add_field(
            name="🔧 기본 설정",
            value=f"**별명 자동 변경**: {'🟢 켜짐' if auto_nickname else '🔴 꺼짐'}",
            inline=False
        )
        # 역할 설정
        basic_role = _resolve_role(guild, self.current_settings.get('basic_role_id'))
        guild_role = _resolve_role(guild, self.current_settings.get('guild_role_id'))
        guest_role = _resolve_role(guild, self.current_settings.get('guest_role_id'))
        embed.add_field(
            name="👥 역할 설정",
            value="\n".join([
                f"**기본 역할**: {basic_role.mention if basic_role else '❌ 미설정'}",
                f"**길드 역할**: {guild_role.mention if guild_role else '❌ 미설정'}",
                f"**게스트 역할**: {guest_role.mention if guest_role else '❌ 미설정'}",
            ]),
            inline=False
        )
        # 길드명
        guild_name = (self.current_settings.get('guild_name') or '').strip()
        embed.add_field(
            name="⚔️ 길드 설정",
            value=f"**길드명**: {guild_name if guild_name else '❌ 미설정'}",
            inline=False
        )
        # 채널 설정
        log_channel = _resolve_channel(guild, self.current_settings.get('log_channel_id'))
        embed.add_field(
            name="📋 채널 설정",
            value=f"**로그 채널**: {log_channel.mention if log_channel else '❌ 미설정'}",
            inline=False
        )
        # 메시지 설정
        has_custom_message = bool(self.current_settings.get('embed_title') or self.current_settings.get('embed_description'))
        has_complete_message = bool(self.current_settings.get('complete_message'))
        embed.add_field(
            name="💌 메시지 설정",
            value="\n".join([
                f"**커스텀 메시지**: {'✅ 설정됨' if has_custom_message else '❌ 기본값 사용'}",
                f"**완료 메시지**: {'✅ 설정됨' if has_complete_message else '❌ 미설정'}",
            ]),
            inline=False
        )
        return embed

    def _apply_button_states(self):
        auto_nickname = bool(self.current_settings.get('auto_nickname', True))
        if hasattr(self, 'nickname_button'):
            self.nickname_button.label = "별명 자동 변경 🟢" if auto_nickname else "별명 자동 변경 🔴"
            self.nickname_button.style = discord.ButtonStyle.success if auto_nickname else discord.ButtonStyle.danger

    def _update_view(self, guild: discord.Guild) -> discord.Embed:
        self._apply_button_states()
        return self._build_embed(guild)

    @staticmethod
    async def show_config_menu(ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        current_settings = await _fetch_config(ctx.guild_id)
        if not current_settings:
            embed = discord.Embed(
                title="❌ 인증 기능 비활성화",
                description=("인증하기 기능이 활성화되어있지 않아요. 활성화 하시겠어요?\n\n"
                             "🔗 인증하기 활성화 후 인증 기능 설정은 https://mococobot.kr → 서버설정 에서 가능해요!\n\n활성화시, 인증에 필요한 역할들을 생성해요.\n생성하는 역할은 서버명, 직업 역할을 생성합니다.\n위 내용 참고하여 정말로 활성화 하실거라면 아래 버튼을 눌러 활성화를 진행해주세요.\n\n인증 상세 설정 방법은 [여기(클릭)](https://mococobot.notion.site/2b69873af556808ba58aeced57cc6a15?source=copy_link)를 참고하세요."),
                color=discord.Color.red()
            )
            view = VerifySetupView(ctx.guild_id)
            return await ctx.followup.send(embed=embed, view=view)

        view = VerifyConfigView(ctx.guild_id, current_settings, ctx.guild)
        return await ctx.followup.send(embed=view._build_embed(ctx.guild), view=view)

    @discord.ui.button(label="인증 메시지 출력", style=discord.ButtonStyle.success, emoji="📤", row=1)
    async def send_verification_message(self, button: discord.ui.Button, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📤 인증 메시지 출력",
            description="인증하기 버튼과 메시지를 보낼 채널을 선택해주세요.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self, "verification"))

    @discord.ui.button(label="역할 생성", style=discord.ButtonStyle.primary, emoji="🛠️", row=2)
    async def create_verify_roles(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            await _send_ephemeral(interaction, "🔄 로스트아크 서버 및 직업 역할을 생성 중...")
            created, failed = await _create_verify_roles(interaction.guild)

            result = discord.Embed(
                title="🛠️ 역할 생성 결과",
                color=discord.Color.green() if not failed else discord.Color.orange()
            )
            if created:
                more = f"\n... 및 {len(created)-10}개 더" if len(created) > 10 else ""
                result.add_field(name="✅ 생성됨", value="\n".join(f"• {n}" for n in created[:10]) + more, inline=False)
            if failed:
                more_f = f"\n... 및 {len(failed)-10}개 더" if len(failed) > 10 else ""
                result.add_field(name="❌ 실패", value="\n".join(f"• {n}" for n in failed[:10]) + more_f, inline=False)
            if not created and not failed:
                result.description = "생성할 새로운 역할이 없습니다. (이미 존재)"
            await _send_ephemeral(interaction, "")  # 위 알림 유지용
            await interaction.followup.send(embed=result, ephemeral=True)
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 역할 생성 중 오류: {e}")

    @discord.ui.button(label="새로고침", style=discord.ButtonStyle.primary, emoji="🔄", row=2)
    async def refresh_settings(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            await _refresh_main(interaction, self)
        except Exception as e:
            await _send_ephemeral(interaction, f"5 ❌ 오류가 발생했습니다: {e}")

class VerifySetupView(discord.ui.View):
    """인증 활성화 뷰"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="활성화 하기", style=discord.ButtonStyle.success, emoji="🟢")
    async def activate_verify(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            embed = discord.Embed(
                title="🔄 로스트아크 서버 및 직업 역할을 생성 중...",
                description="기초 설정을 진행하고 있습니다. 잠시만 기다려주세요.",
                color=discord.Color.yellow()
            )
            await interaction.edit_original_response(embed=embed, view=None)
            
            await _create_verify_roles(interaction.guild)

            embed = discord.Embed(
                title="⚙️ 인증 기능 활성화 중...",
                description="기초 설정을 진행하고 있습니다. 잠시만 기다려주세요.",
                color=discord.Color.yellow()
            )
            await interaction.edit_original_response(embed=embed)

            payload = {
                "auto_nickname": True
            }
            new_settings = await _post_config(self.guild_id, payload)
            
            if new_settings:
                view = VerifyConfigView(self.guild_id, new_settings, interaction.guild)
                embed = view._build_embed(interaction.guild)
                embed.set_footer(text="✅ 인증 기능이 활성화되었습니다! 필요한 설정들을 진행해주세요.")
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.edit_original_response(content="❌ 활성화에 실패했습니다.")
        except Exception as e:
            await interaction.edit_original_response(content=f"6 ❌ 오류가 발생했습니다: {e}")

class ChannelSelectView(discord.ui.View):
    """채널 선택 서브뷰 (verification)"""
    def __init__(self, parent_view: "VerifyConfigView", channel_type: str):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.channel_type = channel_type

        me = parent_view.guild.me
        channels = [
            c for c in parent_view.guild.text_channels
            if me and c.permissions_for(me).send_messages
        ]
        channels.sort(key=lambda c: (c.category.position if c.category else 10**9, c.position, c.name.lower()))
        options = [discord.SelectOption(label=f"#{c.name}", value=str(c.id)) for c in channels[:MAX_OPTIONS]]

        if options:
            select = discord.ui.Select(
                placeholder=f"{'인증' if channel_type == 'verification' else '로그'} 채널을 선택하세요...",
                options=options, min_values=1, max_values=1
            )
            select.callback = self.channel_selected
            self.add_item(select)

    async def channel_selected(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()

            values = (interaction.data or {}).get('values') if hasattr(interaction, "data") else None
            if not values:
                return await _send_ephemeral(interaction, "❌ 채널을 선택하지 않았습니다.")
            channel_id = int(values[0])

            selected = self.parent_view.guild.get_channel(channel_id)
            if not selected:
                return await _send_ephemeral(interaction, "❌ 선택한 채널을 찾을 수 없습니다.")

            if self.channel_type == "verification":
                try:
                    from handler.verify import send_verification_embed
                    ok = await send_verification_embed(selected, self.parent_view.guild_id)
                    if not ok:
                        return await _send_ephemeral(interaction, "❌ 인증 메시지 전송 실패")
                except Exception as e:
                    return await _send_ephemeral(interaction, f"❌ 인증 메시지 전송 오류: {e}")

                # 인증 메시지를 보낸 후 메인 설정 뷰로 돌아갑니다.
                try:
                    # 컴포넌트 상호작용의 메시지를 수정
                    if getattr(interaction, 'message', None):
                        await interaction.message.edit(
                            embed=self.parent_view._build_embed(self.parent_view.guild),
                            view=self.parent_view
                        )
                    else:
                        await interaction.edit_original_response(
                            embed=self.parent_view._build_embed(self.parent_view.guild),
                            view=self.parent_view
                        )
                except Exception:
                    pass
                return await _send_ephemeral(interaction, f"✅ {selected.mention} 에 인증 메시지를 보냈습니다.")

        except Exception as e:
            await _send_ephemeral(interaction, f"❌ 오류: {e}")

    @discord.ui.button(label="돌아가기", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back_to_main(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            if getattr(interaction, 'message', None):
                await interaction.message.edit(
                    embed=self.parent_view._build_embed(self.parent_view.guild),
                    view=self.parent_view
                )
            else:
                await interaction.edit_original_response(
                    embed=self.parent_view._build_embed(self.parent_view.guild),
                    view=self.parent_view
                )
        except Exception:
            pass