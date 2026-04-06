import discord
from discord import option, OptionChoice
from discord.ext import commands
from datetime import datetime, timedelta
from typing import Optional
import secrets
import contextlib
import time

from core.raid_data import raid_list, raid_difficulty_map
from core.http_client import http_client

from commands.raid_commands import RaidCommands
from handler.party import CharacterNicknameModal
from commands.party_manage import ManagePartyView, permission_check
from commands.raid_role import RaidRoleButtonView
from commands.server_config import ServerConfigView


WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
RAIDS = raid_list[:25]
print(RAIDS)
_seen = set()
DIFFICULTIES = []
for v in raid_difficulty_map.values():
    for d in v:
        if d not in _seen:
            _seen.add(d)
            DIFFICULTIES.append(d)
DIFFICULTIES = DIFFICULTIES[:25]


_PARTY_LIST_CACHE: dict[int, tuple[float, list[dict]]] = {}
_PARTY_LIST_TTL = 12.0

_SERVER_CONFIG_CACHE: dict[int, tuple[float, dict]] = {}
_SERVER_CONFIG_TTL = 60.0


def get_date_options():
    now = datetime.now()
    out = []
    for i in range(22):
        d = now + timedelta(days=i)
        weekday_str = WEEKDAYS[d.weekday()]
        formatted_date_base = d.strftime("%y.%m.%d")
        out.append(f"{formatted_date_base}({weekday_str})")
    return out


async def fetch_party_list(guild_id: int) -> list[dict]:
    now = time.monotonic()
    cached = _PARTY_LIST_CACHE.get(guild_id)
    if cached and (now - cached[0]) < _PARTY_LIST_TTL:
        return cached[1]

    resp = await http_client.get(f"/party/list?guild_id={guild_id}")
    if resp is None or resp.status_code != 200:
        _PARTY_LIST_CACHE[guild_id] = (now, [])
        return []

    try:
        data = (resp.json() or {}).get("data") or []
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []

    _PARTY_LIST_CACHE[guild_id] = (now, data)
    return data


async def fetch_server_config(guild_id: int) -> dict:
    now = time.monotonic()
    cached = _SERVER_CONFIG_CACHE.get(guild_id)
    if cached and (now - cached[0]) < _SERVER_CONFIG_TTL:
        return cached[1]

    resp = await http_client.get(f"/discord/server/{guild_id}")
    if resp is None or resp.status_code != 200:
        _SERVER_CONFIG_CACHE[guild_id] = (now, {})
        return {}

    try:
        j = resp.json()
        if isinstance(j, dict):
            data = j.get("data")
            cfg = data if isinstance(data, dict) else j
        else:
            cfg = {}
    except Exception:
        cfg = {}

    _SERVER_CONFIG_CACHE[guild_id] = (now, cfg)
    return cfg


async def announcement_autocomplete(ctx: discord.AutocompleteContext):
    parties = await fetch_party_list(ctx.interaction.guild_id)
    out = []
    for p in parties[:25]:
        title = str(p.get("title") or "")
        pid = str(p.get("id") or "")
        if title and pid:
            out.append(OptionChoice(name=title, value=pid))
    return out


def date_option_autocomplete():
    async def wrapper(ctx: discord.AutocompleteContext):
        return get_date_options()
    return discord.utils.basic_autocomplete(wrapper)


def difficulty_autocomplete():
    async def wrapper(ctx: discord.AutocompleteContext):
        selected_raid = ctx.options.get("레이드")
        return raid_difficulty_map.get(selected_raid, ["레이드를 선택해주세요."])
    return discord.utils.basic_autocomplete(wrapper)


def raid_autocomplete():
    async def wrapper(ctx: discord.AutocompleteContext):
        return raid_list[:25]
    return discord.utils.basic_autocomplete(wrapper)
    

class RaidCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.raid_commands = RaidCommands()

    async def _ephemeral(self, ctx_or_inter, content: str):
        try:
            if hasattr(ctx_or_inter, "respond"):
                if hasattr(ctx_or_inter, "response") and ctx_or_inter.response.is_done():
                    return await ctx_or_inter.followup.send(content, ephemeral=True)
                return await ctx_or_inter.respond(content, ephemeral=True)
            inter = ctx_or_inter
            if inter.response.is_done():
                return await inter.followup.send(content, ephemeral=True)
            return await inter.response.send_message(content, ephemeral=True)
        except Exception:
            with contextlib.suppress(Exception):
                if hasattr(ctx_or_inter, "followup"):
                    return await ctx_or_inter.followup.send(content, ephemeral=True)

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error):
        err = getattr(error, "original", error)

        if isinstance(err, commands.MissingPermissions):
            await self._ephemeral(ctx, ":redTick: 권한이 없어 해당 명령어 사용이 불가능해요!")
            return

        if isinstance(err, commands.BotMissingPermissions):
            await self._ephemeral(ctx, ":redTick: 봇 권한이 부족해요! 서버 권한을 확인해 주세요.")
            return

        if isinstance(err, discord.Forbidden):
            await self._ephemeral(ctx, ":redTick: 권한 문제로 작업을 완료할 수 없어요! (역할/채널 권한, 역할 계층을 확인해 주세요.)")
            return

        if isinstance(err, commands.CheckFailure):
            await self._ephemeral(ctx, ":redTick: 권한이 없어 해당 명령어 사용이 불가능해요!")
            return

        await self._ephemeral(ctx, f"❌ 오류가 발생했습니다: {err}")
        print(f"[RaidCog] command error: {repr(err)}")

    @discord.slash_command(name="setups", description="레이드 일정 기능을 사용하기 위하여 기초 세팅을 진행해요. (관리자 전용)")
    @commands.has_permissions(administrator=True)
    async def setups(self, ctx: discord.ApplicationContext):
        await self.raid_commands.setup_raid_system(ctx, raid_list)

    @discord.slash_command(name="레이드", description="로스트아크 레이드 일정을 등록해요.")
    @option("메세지", description="일정 제목에 반영될 메세지를 입력해주세요.", required=False)
    @option("닉네임", description="입력 시 일정 생성과 동시에 해당 캐릭터로 자동 참가합니다.", required=False)
    async def 레이드(self, ctx: discord.ApplicationContext, 메세지: str = None, 닉네임: str = None):
        date_values = get_date_options()
        hours = [f"{i:02d}" for i in range(24)]
        minutes = [f"{i:02d}" for i in range(0, 60, 5)]

        _cog, _msg = self, 메세지
        _nick = 닉네임.strip() if 닉네임 else None

        class RaidCreateModal(discord.ui.DesignerModal):
            def __init__(self):
                super().__init__(title="레이드 일정 등록", custom_id="raid_modal_v3")
                self.date = discord.ui.Select(
                    placeholder="날짜 선택", custom_id="date",
                    options=[discord.SelectOption(label=v, value=v) for v in date_values],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("날짜", self.date))
                self.hour = discord.ui.Select(
                    placeholder="시 선택 (00~23)", custom_id="hour",
                    options=[discord.SelectOption(label=v, value=v) for v in hours],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("시", self.hour))
                self.minute = discord.ui.Select(
                    placeholder="분 선택 (00~59, 5분 단위)", custom_id="minute",
                    options=[discord.SelectOption(label=v, value=v) for v in minutes],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("분", self.minute))
                self.raid = discord.ui.Select(
                    placeholder="레이드 선택", custom_id="raid",
                    options=[discord.SelectOption(label=v, value=v) for v in RAIDS],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("레이드", self.raid))
                self.difficulty = discord.ui.Select(
                    placeholder="난이도 선택", custom_id="difficulty",
                    options=[discord.SelectOption(label=v, value=v) for v in DIFFICULTIES],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("난이도", self.difficulty))

            async def callback(self, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                날짜, 시, 분 = self.date.values[0], self.hour.values[0], self.minute.values[0]
                레이드, 난이도 = self.raid.values[0], self.difficulty.values[0]
                await _cog.raid_commands.party_create(interaction, 날짜, 시, 분, 레이드, 난이도, _msg, auto_join_nickname=_nick)

        await ctx.send_modal(RaidCreateModal())

    @discord.slash_command(name="모집", description="로스트아크 레이드 모집을 등록해요.")
    @option("닉네임", description="입력 시 일정 생성과 동시에 해당 캐릭터로 자동 참가합니다.", required=False)
    async def 모집(self, ctx: discord.ApplicationContext, 닉네임: str = None):
        _cog = self
        _nick = 닉네임.strip() if 닉네임 else None

        class RecruitModal(discord.ui.DesignerModal):
            def __init__(self):
                super().__init__(title="레이드 모집 등록", custom_id="recruit_modal_v1")
                self.raid = discord.ui.Select(
                    placeholder="레이드 선택", custom_id="raid",
                    options=[discord.SelectOption(label=v, value=v) for v in RAIDS],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("레이드", self.raid))
                self.difficulty = discord.ui.Select(
                    placeholder="난이도 선택", custom_id="difficulty",
                    options=[discord.SelectOption(label=v, value=v) for v in DIFFICULTIES],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("난이도", self.difficulty))
                self.message = discord.ui.InputText(
style=discord.InputTextStyle.long,
                    required=False, placeholder="일정 제목에 반영될 메세지"
                )
                self.add_item(discord.ui.Label("메세지", self.message))

            async def callback(self, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                레이드, 난이도, 메세지 = self.raid.values[0], self.difficulty.values[0], self.message.value
                await _cog.raid_commands.party_create(interaction, None, None, None, 레이드, 난이도, 메세지, auto_join_nickname=_nick)

        await ctx.send_modal(RecruitModal())

    @discord.slash_command(name="강제참가", description="유저를 특정 레이드 일정에 강제로 참가시킵니다.")
    @option("공지", description="강제 참가시킬 레이드 공지를 선택하세요.", autocomplete=announcement_autocomplete)
    @option("유저", description="강제 참가시킬 디스코드 유저를 선택하세요.", type=discord.User, required=False)
    async def 강제참가(self, ctx: discord.ApplicationContext, 공지: str, 유저: Optional[discord.User] = None):
        data = await permission_check(ctx.interaction, int(공지))
        if not data:
            return

        if 유저:
            target_user_id = str(유저.id)
        else:
            base_user = getattr(ctx, "author", None) or getattr(ctx, "user", None) or ctx.interaction.user
            epoch = int(time.time())
            rand = secrets.token_hex(2)
            target_user_id = f"TEMP-{base_user.id}-{epoch}-{rand}"

        modal = CharacterNicknameModal(int(공지), target_user_id)
        await ctx.send_modal(modal)

    @discord.slash_command(name="일정관리", description="레이드 일정을 관리해요.")
    @option("공지", description="관리할 레이드 공지를 선택하세요.", autocomplete=announcement_autocomplete)
    async def 일정관리(self, ctx: discord.ApplicationContext, 공지: str):
        await ctx.defer(ephemeral=True)

        data = await permission_check(ctx.interaction, int(공지))
        if not data:
            return

        participants = []
        for role, plist in (data.get("participants") or {}).items():
            for p in plist or []:
                p["role"] = 0 if role == "dealers" else 1
                participants.append(p)

        view = ManagePartyView(공지, participants, data.get("thread_manage_id"))
        await ctx.followup.send(f"{공지} 일정 관리 메뉴입니다.", view=view, ephemeral=True)

    @discord.slash_command(name="일정변경", description="기존에 등록했던 레이드 일정을 변경해요.")
    async def 일정변경(self, ctx: discord.ApplicationContext):
        date_values = get_date_options()
        hours = [f"{i:02d}" for i in range(24)]
        minutes = [f"{i:02d}" for i in range(0, 60, 5)]

        ann_choices = await announcement_autocomplete(ctx)
        ann_options = [
            discord.SelectOption(
                label=(c.name[:100] if isinstance(c.name, str) else str(c.name)[:100]),
                value=str(c.value)[:100],
            )
            for c in ann_choices
        ][:25]

        if not ann_options:
            await ctx.respond("변경할 공지가 없습니다.", ephemeral=True)
            return

        _cog = self

        class EditRaidModal(discord.ui.DesignerModal):
            def __init__(self):
                super().__init__(title="레이드 일정 변경", custom_id="raid_edit_modal_v2")

                self.ann = discord.ui.Select(
                    placeholder="공지 선택", custom_id="ann",
                    options=ann_options, min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("공지", self.ann))

                self.date = discord.ui.Select(
                    placeholder="날짜 선택", custom_id="date",
                    options=[discord.SelectOption(label=v, value=v) for v in date_values],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("날짜", self.date))

                self.hour = discord.ui.Select(
                    placeholder="시 선택 (00~23)", custom_id="hour",
                    options=[discord.SelectOption(label=v, value=v) for v in hours],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("시", self.hour))

                self.minute = discord.ui.Select(
                    placeholder="분 선택 (00~59, 5분 단위)", custom_id="minute",
                    options=[discord.SelectOption(label=v, value=v) for v in minutes],
                    min_values=1, max_values=1
                )
                self.add_item(discord.ui.Label("분", self.minute))

                self.msg = discord.ui.InputText(
                    style=discord.InputTextStyle.long,
                    required=False, placeholder="일정 제목에 반영될 메세지"
                )
                self.add_item(discord.ui.Label("메세지", self.msg))

            async def callback(self, interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)

                공지 = self.ann.values[0]
                data = await permission_check(interaction, int(공지))
                if not data:
                    return

                날짜 = self.date.values[0]
                시 = self.hour.values[0]
                분 = self.minute.values[0]
                메세지 = (self.msg.value or None)

                try:
                    await _cog.raid_commands.party_edit(interaction, data, 날짜, 시, 분, 메세지)
                except Exception as e:
                    with contextlib.suppress(Exception):
                        await interaction.followup.send(content=f"오류가 발생했습니다: {e}", ephemeral=True)

        await ctx.send_modal(EditRaidModal())

    @discord.slash_command(name="공격대", description="로스트아크 레이드 일정을 간편 등록해요. (레이드 명령어와 동일)")
    @option("날짜", description="레이드 날짜를 선택하세요.", autocomplete=date_option_autocomplete())
    @option("시", description="시 (00~23)", choices=[OptionChoice(name=f"{i:02d}", value=f"{i:02d}") for i in range(24)])
    @option("분", description="분 (00~59, 5분 단위)", choices=[OptionChoice(name=f"{i:02d}", value=f"{i:02d}") for i in range(0, 60, 5)])
    @option("레이드", description="레이드를 선택하세요.", autocomplete=raid_autocomplete())
    @option("난이도", description="난이도를 선택하세요.", autocomplete=difficulty_autocomplete())
    @option("메세지", description="일정 제목에 반영될 메세지를 입력하세요. (선택)", required=False)
    @option("닉네임", description="입력 시 일정 생성과 동시에 해당 캐릭터로 자동 참가합니다.", required=False)
    async def 공격대(self, ctx: discord.ApplicationContext, 날짜: str, 시: str, 분: str, 레이드: str, 난이도: str, 메세지: str = None, 닉네임: str = None):
        await ctx.defer(ephemeral=True)
        await self.raid_commands.party_create(ctx, 날짜, 시, 분, 레이드, 난이도, 메세지, auto_join_nickname=닉네임.strip() if 닉네임 else None)

    @discord.slash_command(name="레이드역할", description="레이드 보스별 역할을 생성해요. (관리자 전용)")
    @commands.has_permissions(administrator=True)
    async def 레이드역할(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)

        created_roles = []
        skipped_roles = []

        for raid_name in raid_list:
            existing_role = discord.utils.get(ctx.guild.roles, name=raid_name)
            if existing_role:
                skipped_roles.append(raid_name)
                continue

            try:
                await ctx.guild.create_role(
                    name=raid_name,
                    color=discord.Color.random(),
                    mentionable=True,
                    reason="레이드 역할 자동 생성"
                )
                created_roles.append(raid_name)
            except Exception as e:
                print(f"역할 생성 실패 ({raid_name}): {e}")

        embed = discord.Embed(title="레이드 역할 생성 완료했어요.", color=discord.Color.green())
        if created_roles:
            embed.add_field(name="✅ 생성된 역할", value="\n".join(created_roles), inline=False)
        if skipped_roles:
            embed.add_field(name="⏭️ 이미 존재하는 역할", value="\n".join(skipped_roles), inline=False)

        await ctx.interaction.edit_original_response(embed=embed)

    @discord.slash_command(name="레이드역할지급채널", description="레이드 역할을 받을 수 있는 버튼을 생성해요. (관리자 전용)")
    @commands.has_permissions(administrator=True)
    async def 레이드역할지급채널(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)

        guild_id = ctx.guild_id
        server_config = await fetch_server_config(guild_id)

        if not server_config:
            await ctx.followup.send("당신의 서버는 아직 기초 설정이 되지 않았습니다. 관리자에게 문의하세요.", ephemeral=True)
            return

        forum_channel_id = server_config.get("forum_channel_id")
        if not forum_channel_id:
            await ctx.followup.send("서버 설정에 레이드 포럼 채널(forum_channel_id)이 없습니다. 관리자 페이지에서 먼저 설정해 주세요.", ephemeral=True)
            return

        embed = discord.Embed(
            title="",
            description=f"""
<:member:1252960227599585321>  레이드 역할 알림 설정 안내

<a:32877animatedarrowbluelite:1252962176289869855> 키워드 알림 설정을 통하여 레이드 공지 알림을 받을 수 있습니다.
<#{forum_channel_id}> 채널에 올라가는 공지의 알림을 받을 수 있어요.

<:chat:1254823790186074303>  알림 설정 방법

<a:15770animatedarrowyellow:1252962149211443303> 아래 키워드 알림 설정하기 버튼을 클릭해 주세요.
목록의 순서는 **로스트아크 레이드 카테고리** 입니다.

<a:15770animatedarrowyellow:1252962149211443303> 원하는 알림을 선택할 경우, 체크 표시와 함께 역할이 추가됩니다.
불필요해진 알림은 한 번 더 클릭하시어 역할을 제거해 주세요.

<:ticketemoji:1252801028231921777>  기타 알림 오류 안내

<a:73288animatedarrowred:1252962235006062622> 디스코드 역할 지급이 간헐적으로 작동하지 않을 수 있습니다.
버그, 오류, 건의는 [모코코봇 지원 서버](https://discord.gg/NGY82F5NtV)에 티켓 문의 부탁드립니다.
""",
            color=discord.Color.from_rgb(168, 128, 224),
        )
        embed.set_footer(text="Create By 조교병(카제로스)")

        view = RaidRoleButtonView()
        channel = ctx.channel

        await channel.send(embed=embed, view=view)
        await ctx.followup.send(f"{channel.mention} 채널에 레이드 역할 지급 버튼을 생성했습니다!", ephemeral=True)

    @discord.slash_command(name="갱신", description="캐릭터 정보를 갱신합니다.")
    @option("닉네임", description="갱신할 캐릭터의 닉네임을 입력하세요.", required=True)
    async def 갱신(self, ctx: discord.ApplicationContext, 닉네임: str):
        try:
            await ctx.response.defer(ephemeral=True)

            response = await http_client.patch("/character/update", json={
                "char_name": 닉네임,
                "update_discord": True
            })

            if response.status_code != 200:
                await ctx.interaction.edit_original_response(
                    content=f"❌ 캐릭터 갱신에 실패했습니다. (상태 코드: {response.status_code})"
                )
                return

            data = response.json()

            if data.get("total_updated", 0) == 0:
                embed = discord.Embed(
                    title="⚠️ 갱신 결과",
                    description=f"'{닉네임}' 캐릭터를 찾을 수 없거나 갱신할 데이터가 없어요.",
                    color=discord.Color.orange()
                )
            else:
                embed = discord.Embed(
                    title="✅ 캐릭터 갱신 완료!",
                    description=data.get("message", "캐릭터 정보가 성공적으로 갱신되었어요!"),
                    color=discord.Color.green()
                )

                characters = data.get("characters", [])
                for char in characters:
                    changes = char.get("changes", {})
                    old_data = char.get("old_data", {})

                    change_info = []
                    if changes.get("item_lvl_changed"):
                        change_info.append(f"아이템 레벨: {old_data.get('item_lvl')} → {char.get('item_lvl')}")
                    if changes.get("combat_power_changed"):
                        change_info.append(f"전투력: {old_data.get('combat_power')} → {char.get('combat_power')}")
                    if changes.get("class_changed"):
                        change_info.append(f"직업: {old_data.get('class_name')} → {char.get('class_name')}")

                    field_value = f"**직업:** {char.get('class_name')}\n**아이템 레벨:** {char.get('item_lvl')}\n**전투력:** {char.get('combat_power')}"

                    if change_info:
                        field_value += f"\n\n**📈 변경사항:**\n" + "\n".join(change_info)
                    else:
                        field_value += f"\n\n*변경사항 없음*"

                    embed.add_field(
                        name=f"{char.get('char_name')}",
                        value=field_value,
                        inline=False
                    )

                discord_update = data.get("discord_update")
                if discord_update:
                    summary = discord_update.get("summary", {})
                    embed.add_field(
                        name="📊 디스코드 업데이트 결과",
                        value=f"**총 캐릭터:** {summary.get('total_characters', 0)}개\n**총 파티:** {summary.get('total_parties', 0)}개\n**성공:** {summary.get('total_success', 0)}개\n**실패:** {summary.get('total_failed', 0)}개",
                        inline=True
                    )

                embed.set_footer(text=f"데이터 소스: {data.get('source', 'unknown')}")

            await ctx.interaction.edit_original_response(embed=embed)

        except Exception as e:
            await ctx.interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}"
            )

    @discord.slash_command(name="서버설정", description="서버의 레이드 시스템 설정을 관리합니다. (관리자 전용)")
    @commands.has_permissions(administrator=True)
    async def 서버설정(self, ctx: discord.ApplicationContext):
        await ServerConfigView.show_config_menu(ctx)

    @discord.slash_command(name="일정", description="등록된 레이드 일정을 요약해서 보여줘요.")
    @discord.option("공개", description="모두에게 보일까요? (기본: 비공개)", type=bool, required=False, default=False)
    @discord.option(
        "표기",
        description="일정 표기 방식 (전체: 모든 일정, 주: 7일 단위)",
        required=False,
        default="all",
        choices=[
            discord.OptionChoice(name="전체", value="all"),
            discord.OptionChoice(name="주", value="week"),
        ],
    )
    async def schedule(self, ctx: discord.ApplicationContext, 공개: bool = False, 표기: str = "all"):
        await ctx.defer(ephemeral=not 공개)

        parties = await fetch_party_list(ctx.interaction.guild_id)
        if not parties:
            await ctx.followup.send("등록된 레이드 일정이 없습니다.", ephemeral=not 공개)
            return

        undated = [p for p in parties if not p.get("start_date")]
        dated_raw = [p for p in parties if p.get("start_date")]

        dated: list[tuple[datetime, dict]] = []
        for p in dated_raw:
            s = p.get("start_date")
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            dated.append((dt, p))

        if not undated and not dated:
            await ctx.followup.send("등록된 레이드 일정이 없습니다.", ephemeral=not 공개)
            return

        dated.sort(key=lambda x: x[0])

        desc_lines: list[str] = []
        desc_lines.append("레이드 일정을 확인하고, 제목 클릭시 해당 일정으로 이동해요.")
        desc_lines.append("")

        if undated:
            desc_lines.append("일정 미정")
            for p in undated:
                raid_name = (p.get("raid_name") or "").strip()
                difficulty = (p.get("difficulty") or "").strip()
                msg = (p.get("message") or "").strip()
                label_text = f"{raid_name} {difficulty}".strip()
                if msg:
                    label_text = f"{label_text} {msg}"
                thread_id = (p.get("thread_manage_id") or "").strip()
                if thread_id:
                    guild_id = str(p.get("guild_id") or ctx.interaction.guild_id)
                    url = f"https://discord.com/channels/{guild_id}/{thread_id}"
                    label = f"[{label_text}]({url})"
                else:
                    label = label_text
                desc_lines.append(f"> {label}")
            desc_lines.append("")

        today = datetime.now().date()

        if 표기 == "all":
            current_date_key = None
            for dt, p in dated:
                d = dt.date()
                if d != current_date_key:
                    current_date_key = d
                    month = d.month
                    day = d.day
                    weekday_name = WEEKDAYS[dt.weekday()]
                    if d == today:
                        header = f"**Today [{month}월 {day}일 {weekday_name}요일]**"
                    else:
                        header = f"**{weekday_name}요일 [{month}월 {day}일]**"
                    if desc_lines and desc_lines[-1] != "":
                        desc_lines.append("")
                    desc_lines.append(header)

                raid_name = (p.get("raid_name") or "").strip()
                difficulty = (p.get("difficulty") or "").strip()
                msg = (p.get("message") or "").strip()
                time_str = dt.strftime("%H:%M")

                label_text = f"{raid_name} {difficulty}".strip()
                if msg:
                    label_text = f"{label_text} {msg}"

                thread_id = (p.get("thread_manage_id") or "").strip()
                if thread_id:
                    guild_id = str(p.get("guild_id") or ctx.interaction.guild_id)
                    url = f"https://discord.com/channels/{guild_id}/{thread_id}"
                    label = f"[{label_text}]({url})"
                else:
                    label = label_text

                ts = int(dt.timestamp())
                desc_lines.append(f"> **`{time_str}`** {label} <t:{ts}:R>")
        else:
            by_date: dict[datetime.date, list[tuple[datetime, dict]]] = {}
            for dt, p in dated:
                by_date.setdefault(dt.date(), []).append((dt, p))
            for dlist in by_date.values():
                dlist.sort(key=lambda x: x[0])

            for i in range(7):
                day_date = today + timedelta(days=i)
                month = day_date.month
                day_num = day_date.day
                weekday_name = WEEKDAYS[day_date.weekday()]

                if desc_lines and desc_lines[-1] != "":
                    desc_lines.append("")

                if day_date == today:
                    desc_lines.append(f"**Today [{month}월 {day_num}일 {weekday_name}요일]**")
                else:
                    desc_lines.append(f"**{weekday_name}요일 [{month}월 {day_num}일]**")

                entries = by_date.get(day_date, [])
                if not entries:
                    desc_lines.append(">")
                    continue

                for dt, p in entries:
                    raid_name = (p.get("raid_name") or "").strip()
                    difficulty = (p.get("difficulty") or "").strip()
                    msg = (p.get("message") or "").strip()
                    time_str = dt.strftime("%H:%M")

                    label_text = f"{raid_name} {difficulty}".strip()
                    if msg:
                        label_text = f"{label_text} {msg}"

                    thread_id = (p.get("thread_manage_id") or "").strip()
                    if thread_id:
                        guild_id = str(p.get("guild_id") or ctx.interaction.guild_id)
                        url = f"https://discord.com/channels/{guild_id}/{thread_id}"
                        label = f"[{label_text}]({url})"
                    else:
                        label = label_text

                    ts = int(dt.timestamp())
                    desc_lines.append(f"> **`{time_str}`** {label} <t:{ts}:R>")

        if len(desc_lines) <= 2:
            await ctx.followup.send("등록된 레이드 일정이 없습니다.", ephemeral=not 공개)
            return

        max_len = 4000
        chunks: list[list[str]] = []
        current_chunk: list[str] = []
        current_len = 0

        for line in desc_lines:
            extra = 1 if current_chunk else 0
            if current_len + extra + len(line) > max_len:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = [line]
                current_len = len(line)
            else:
                if current_chunk:
                    current_chunk.append(line)
                    current_len += extra + len(line)
                else:
                    current_chunk = [line]
                    current_len = len(line)

        if current_chunk:
            chunks.append(current_chunk)

        server_name = ctx.guild.name if ctx.guild else "서버"

        for idx, lines_chunk in enumerate(chunks):
            description = "\n".join(lines_chunk)
            if idx == 0:
                embed = discord.Embed(
                    title=f"<:mococo_logo_symbol:1440231382222639165> {server_name} 의 일정이예요.",
                    description=description,
                )
            else:
                embed = discord.Embed(description=description)
            await ctx.followup.send(embed=embed, ephemeral=not 공개)

    @discord.slash_command(name="확인", description="특정 유저가 참가한 레이드 일정을 확인해요.")
    @discord.option("유저", description="참가 일정을 확인할 디스코드 유저 (기본: 나)", type=discord.Member, required=False)
    async def check_participation(self, ctx: discord.ApplicationContext, 유저: discord.Member = None):
        await ctx.defer(ephemeral=True)

        target = 유저 or ctx.author
        target_id = str(target.id)

        parties = await fetch_party_list(ctx.interaction.guild_id)
        if not parties:
            await ctx.followup.send(f"{target.mention} 님이 참가한 레이드 일정이 없습니다.", ephemeral=True)
            return

        def is_participant(p: dict) -> bool:
            participants = (p.get("participants") or {})
            dealers = participants.get("dealers") or []
            supporters = participants.get("supporters") or []
            for entry in dealers:
                if str(entry.get("user_id") or "") == target_id:
                    return True
            for entry in supporters:
                if str(entry.get("user_id") or "") == target_id:
                    return True
            return False

        def format_number(v):
            try:
                if v is None:
                    return "-"
                if isinstance(v, int):
                    return str(v)
                if isinstance(v, float):
                    s = f"{v:.2f}".rstrip("0").rstrip(".")
                    return s
                return str(v)
            except Exception:
                return str(v)

        def character_line(p: dict) -> str | None:
            participants = p.get("participants") or {}
            dealers = participants.get("dealers") or []
            supporters = participants.get("supporters") or []
            for entry in dealers + supporters:
                if str(entry.get("user_id") or "") != target_id:
                    continue
                name = (entry.get("name") or "").strip()
                class_name = (entry.get("class_name") or "").strip()
                item_level = format_number(entry.get("item_level"))
                combat_power = format_number(entry.get("combat_power"))
                return f"-# {name} | {class_name} | {item_level} | {combat_power}"
            return None

        target_parties = [p for p in parties if is_participant(p)]
        if not target_parties:
            await ctx.followup.send(f"{target.mention} 님이 참가한 레이드 일정이 없습니다.", ephemeral=True)
            return

        undated = [p for p in target_parties if not p.get("start_date")]
        dated_raw = [p for p in target_parties if p.get("start_date")]

        dated: list[tuple[datetime, dict]] = []
        for p in dated_raw:
            s = p.get("start_date")
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            dated.append((dt, p))

        dated.sort(key=lambda x: x[0])

        lines: list[str] = []
        lines.append(f"**{target.display_name}** 님이 참가한 레이드 일정")
        lines.append("")

        if undated:
            lines.append("▶ 일정 미정")
            for p in undated:
                raid_name = (p.get("raid_name") or "").strip()
                difficulty = (p.get("difficulty") or "").strip()
                msg = (p.get("message") or "").strip()
                label_text = f"{raid_name} {difficulty}".strip()
                if msg:
                    label_text = f"{label_text} {msg}"
                thread_id = (p.get("thread_manage_id") or "").strip()
                if thread_id:
                    guild_id = str(p.get("guild_id") or ctx.interaction.guild_id)
                    url = f"https://discord.com/channels/{guild_id}/{thread_id}"
                    lines.append(f"- [{label_text}]({url})")
                else:
                    lines.append(f"- {label_text}")

                ch_line = character_line(p)
                if ch_line:
                    lines.append(ch_line)
            lines.append("")

        current_date_key = None
        for dt, p in dated:
            date_key = dt.date().isoformat()
            if date_key != current_date_key:
                current_date_key = date_key
                month = dt.month
                day = dt.day
                weekday = WEEKDAYS[dt.weekday()]
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(f"▶ {month}/{day} (**{weekday}**)")

            raid_name = (p.get("raid_name") or "").strip()
            difficulty = (p.get("difficulty") or "").strip()
            msg = (p.get("message") or "").strip()
            time_str = dt.strftime("%H:%M")

            label_text = f"{raid_name} {difficulty}".strip()
            if msg:
                label_text = f"{label_text} {msg}"

            thread_id = (p.get("thread_manage_id") or "").strip()
            if thread_id:
                guild_id = str(p.get("guild_id") or ctx.interaction.guild_id)
                url = f"https://discord.com/channels/{guild_id}/{thread_id}"
                label = f"[{label_text}]({url})"
            else:
                label = label_text

            lines.append(f"[ {time_str} ]  {label}")

            ch_line = character_line(p)
            if ch_line:
                lines.append(ch_line)

        chunks: list[str] = []
        current = ""
        for line in lines:
            addition = line if not current else "\n" + line
            if len(current) + len(addition) > 1900:
                if current:
                    chunks.append(current)
                current = line
            else:
                current += addition
        if current:
            chunks.append(current)

        for chunk in chunks:
            await ctx.followup.send(chunk, ephemeral=True)


def setup(bot):
    bot.add_cog(RaidCog(bot))
