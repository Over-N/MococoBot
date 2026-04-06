from __future__ import annotations

import discord
from discord import option
from discord.ext import commands
from typing import Awaitable, Callable, List, Optional
from handler.quiz import make_quiz_embed, QUIZ_EMBED_COLOR
from core.http_client import http_client

def make_admin_embed(cfg: dict, schedules: Optional[List[dict]] = None) -> discord.Embed:
    enabled = bool(cfg.get("enabled"))
    channel_id = cfg.get("channel_id")

    def fmt_hhmm(v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            return None
        digits = digits[-4:].zfill(4)
        return f"{digits[:2]}:{digits[2:]}"

    times = {fmt_hhmm(s.get("hhmm")) for s in (schedules or []) if s.get("hhmm")}
    times.discard(None)
    schedule_values = sorted(times)
    cfg_time = fmt_hhmm(cfg.get("schedule_hhmm")) if cfg.get("schedule_hhmm") else None
    schedule_text = "\n".join(schedule_values) if schedule_values else (cfg_time or "미지정")

    status = "✅ 지금은 **활성화**되어 있어요." if enabled else "⛔ 지금은 **비활성화** 상태예요."
    description = (
        f"{status}\n\n"
        "**퀴즈 시스템 안내**\n"
        "• 설정하신 **시간:분**마다 로스트아크 관련 퀴즈를 랜덤으로 보내드려요.\n"
        "• 메시지 하단의 **정답 버튼**으로 편하게 제출하실 수 있어요.\n"
        "• 정답을 맞추면 서버 **랭킹**에 기록돼요. `/랭킹`에서 확인해 보세요!\n"
        "• 가끔 **이미지 문제**도 출제돼요.\n"
        "• 관리자는 원하는 만큼 **여러 시간**을 등록할 수 있어요.\n"
        "• 바로 확인하려면 **퀴즈 보내기** 버튼으로 테스트해 보세요.\n"
        "• 알림이 잦다면 전용 채널 사용을 권장드려요!\n"
    )

    embed = discord.Embed(
        title="🧩 퀴즈 설정",
        description=description,
        color=discord.Color(QUIZ_EMBED_COLOR),
    )
    embed.add_field(name="채널", value=(f"<#{channel_id}>" if channel_id else "미지정"), inline=True)
    embed.add_field(name="시간", value=schedule_text, inline=False)
    embed.set_footer(text="MococoBot • Quiz")
    return embed


async def fetch_config_and_schedules(guild_id: int) -> tuple[dict, List[dict]]:
    cfg_resp = await http_client.get(f"/quiz/config/{guild_id}")
    if cfg_resp is None or cfg_resp.status_code != 200:
        raise RuntimeError("?? ??? ???? ?????.")
    cfg = cfg_resp.json() or {}

    sc_resp = await http_client.get(f"/quiz/schedules/{guild_id}")
    schedules: List[dict] = []
    if sc_resp is not None and sc_resp.status_code == 200:
        payload = sc_resp.json() or []
        if isinstance(payload, list):
            schedules = payload

    return cfg, schedules


class ChannelSelectView(discord.ui.View):
    def __init__(self, options: List[discord.SelectOption], callback: Callable):
        super().__init__(timeout=180)
        self._callback = callback
        select = discord.ui.Select(placeholder="채널 선택", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        channel_id = int(self.children[0].values[0])
        await self._callback(interaction, channel_id)
        self.stop()


class ChannelSearchModal(discord.ui.Modal):
    def __init__(self, callback: Callable[[discord.Interaction, int], None]):
        super().__init__(title="채널 검색", custom_id="quiz_channel_search")
        self.callback_func = callback
        self.search = discord.ui.InputText(label="채널 이름 (일부)", placeholder="검색할 채널 이름")
        self.add_item(self.search)

    async def callback(self, interaction: discord.Interaction):
        term = self.search.value.strip().lower()
        channels = [c for c in interaction.guild.text_channels if term in c.name.lower()]
        if not channels:
            await interaction.response.send_message("채널을 찾지 못했습니다.", ephemeral=True)
            return
        options = [discord.SelectOption(label=f"#{c.name}", value=str(c.id)) for c in channels[:25]]
        view = ChannelSelectView(options, self.callback_func)
        await interaction.response.send_message("채널을 선택하세요.", view=view, ephemeral=True)


class TimeAddView(discord.ui.View):
    def __init__(self, guild_id: int, preset: Optional[List[str]] = None):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.sel_h: Optional[str] = None
        self.sel_m: Optional[str] = None
        # "HH:MM" 문자열을 누적 관리
        self.added: set[str] = set(preset or [])

    def _embed(self) -> discord.Embed:
        top = f"현재 선택: **{self.sel_h or '--'}시 {self.sel_m or '--'}분**"
        bottom = ", ".join(sorted(self.added)) if self.added else "없음"
        em = discord.Embed(title="⏰ 퀴즈 시간 추가", description=top, color=discord.Color.gold())
        em.add_field(name="등록된 시간(누적)", value=bottom, inline=False)
        em.set_footer(text="시/분 선택 → [추가]를 누르세요")
        return em

    @discord.ui.select(
        placeholder="시(HH)",
        min_values=1, max_values=1,
        options=[discord.SelectOption(label=f"{i:02d}", value=f"{i:02d}") for i in range(24)]
    )
    async def select_hour(self, select: discord.ui.Select, interaction: discord.Interaction):
        self.sel_h = select.values[0]
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.select(
        placeholder="분(MM)",
        min_values=1, max_values=1,
        options=[discord.SelectOption(label=f"{i:02d}", value=f"{i:02d}") for i in range(0, 60, 5)]
    )
    async def select_minute(self, select: discord.ui.Select, interaction: discord.Interaction):
        self.sel_m = select.values[0]
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="추가", style=discord.ButtonStyle.primary)
    async def add_time(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.sel_h or not self.sel_m:
            return await interaction.response.edit_message(
                embed=self._embed().set_footer(text="시와 분을 모두 선택하세요."),
                view=self
            )

        try:
            resp = await http_client.post(
                f"/quiz/schedules/{interaction.guild_id}/add",
                json={"hh": self.sel_h, "mm": self.sel_m}
            )
            if resp is None or resp.status_code != 200:
                raise RuntimeError("add failed")

            sc = await http_client.get(f"/quiz/schedules/{interaction.guild_id}")
            items = (sc.json() if sc is not None and sc.status_code == 200 else []) or []
            self.added = {str(s.get("hhmm")) for s in items if s.get("hhmm")}

            await interaction.response.edit_message(embed=self._embed(), view=self)

        except Exception:
            em = self._embed()
            em.set_footer(text="추가 실패. 잠시 후 다시 시도하세요.")
            await interaction.response.edit_message(embed=em, view=self)


class TimeDeleteView(discord.ui.View):
    def __init__(self, guild_id: int, schedules: List[dict]):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.selected: Optional[str] = None
        self.items: set[str] = {str(s.get("hhmm")) for s in (schedules or []) if s.get("hhmm")}

    def _embed(self) -> discord.Embed:
        top = f"삭제 대상: **{self.selected or '--'}**"
        bottom = ", ".join(sorted(self.items)) if self.items else "없음"
        em = discord.Embed(title="🗑️ 퀴즈 시간 삭제", description=top, color=discord.Color.red())
        em.add_field(name="현재 등록된 시간", value=bottom, inline=False)
        em.set_footer(text="삭제할 시간을 선택 후 [삭제]를 누르세요")
        return em

    @discord.ui.select(
        placeholder="삭제할 시간 선택",
        min_values=1, max_values=1,
        options=[]
    )
    async def select_time(self, select: discord.ui.Select, interaction: discord.Interaction):
        if not select.options:
            select.options = [discord.SelectOption(label=t, value=t) for t in sorted(self.items)][:25]
        self.selected = select.values[0]
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger)
    async def delete_time(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.selected:
            return await interaction.response.edit_message(
                embed=self._embed().set_footer(text="먼저 삭제할 시간을 선택하세요."),
                view=self
            )
        try:
            resp = await http_client.delete(
                f"/quiz/schedules/{interaction.guild_id}/remove",
                params={"hhmm": self.selected}
            )
            if resp is None or resp.status_code != 200:
                raise RuntimeError("delete failed")

            sc = await http_client.get(f"/quiz/schedules/{interaction.guild_id}")
            items = (sc.json() if sc is not None and sc.status_code == 200 else []) or []
            self.items = {str(s.get("hhmm")) for s in items if s.get("hhmm")}
            self.selected = None

            for child in self.children:
                if isinstance(child, discord.ui.Select):
                    child.options = [discord.SelectOption(label=t, value=t) for t in sorted(self.items)][:25]

            await interaction.response.edit_message(embed=self._embed(), view=self)

        except Exception:
            em = self._embed()
            em.set_footer(text="삭제 실패. 잠시 후 다시 시도하세요.")
            await interaction.response.edit_message(embed=em, view=self)

class RawAnswerButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label="정답 입력",
                custom_id="quiz_answer",
            )
        )
        
class EnableButton(discord.ui.View):
    def __init__(self, guild_id: int, command_channel_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.command_channel_id = command_channel_id

    @discord.ui.button(label="활성화", style=discord.ButtonStyle.success)
    async def enable(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("관리자만 가능합니다.", ephemeral=True)

        await http_client.patch(
            f"/quiz/config/{self.guild_id}",
            json={"enabled": True, "channel_id": self.command_channel_id},
        )

        cfg = (await http_client.get(f"/quiz/config/{self.guild_id}")).json()
        ch_id = cfg.get("channel_id") or interaction.channel_id
        await interaction.response.edit_message(
            embed=make_admin_embed(cfg),
            view=AdminView(self.guild_id, ch_id),
        )


class AdminView(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.channel_id = channel_id

    @discord.ui.button(label="비활성화", style=discord.ButtonStyle.danger)
    async def disable(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("관리자만 가능합니다.", ephemeral=True)
            return
        try:
            await http_client.patch(f"/quiz/config/{self.guild_id}", json={"enabled": False})
            cfg_resp = await http_client.get(f"/quiz/config/{self.guild_id}")
            cfg = cfg_resp.json()
            sc_resp = await http_client.get(f"/quiz/schedules/{self.guild_id}")
            schedules = sc_resp.json() if sc_resp.status_code == 200 else []
            embed = make_admin_embed(cfg, schedules)
            view = EnableButton(self.guild_id, self.channel_id)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            await interaction.response.send_message("비활성화 중 오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="퀴즈 보내기", style=discord.ButtonStyle.primary)
    async def send_now(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("관리자만 가능합니다.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await http_client.post(
                "/quiz/send_now",
                params={"guild_id": self.guild_id, "channel_id": self.channel_id}
            )
        except Exception:
            await interaction.followup.send("퀴즈 전송 중 오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="채널 지정", style=discord.ButtonStyle.secondary)
    async def set_channel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("관리자만 가능합니다.")
            return

        async def finish_select(inter: discord.Interaction, channel_id: int):
            try:
                await http_client.patch(f"/quiz/config/{self.guild_id}", json={"channel_id": channel_id})
                self.channel_id = channel_id
                cfg_resp = await http_client.get(f"/quiz/config/{self.guild_id}")
                cfg = cfg_resp.json()
                sc_resp = await http_client.get(f"/quiz/schedules/{self.guild_id}")
                schedules = sc_resp.json() if sc_resp.status_code == 200 else []
                embed = make_admin_embed(cfg, schedules)
                await inter.response.edit_message(content="채널이 설정되었습니다.", embed=embed, view=self)
            except Exception:
                await inter.response.send_message("채널 설정 중 오류가 발생했습니다.", ephemeral=True)

        modal = ChannelSearchModal(finish_select)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="시간 추가", style=discord.ButtonStyle.secondary)
    async def time_add(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("관리자만 가능합니다.", ephemeral=True)

        sc = await http_client.get(f"/quiz/schedules/{self.guild_id}")
        preset = [str(s.get("hhmm")) for s in (sc.json() if sc is not None and sc.status_code == 200 else []) if s.get("hhmm")]
        view = TimeAddView(self.guild_id, preset=preset)
        await interaction.response.send_message(embed=view._embed(), view=view, ephemeral=True)

    @discord.ui.button(label="시간 삭제", style=discord.ButtonStyle.secondary)
    async def time_del(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("관리자만 가능합니다.", ephemeral=True)

        sc = await http_client.get(f"/quiz/schedules/{self.guild_id}")
        schedules = sc.json() if sc is not None and sc.status_code == 200 else []
        if not schedules:
            return await interaction.response.send_message("등록된 시간이 없습니다.", ephemeral=True)

        view = TimeDeleteView(self.guild_id, schedules)
        for child in view.children:
            if isinstance(child, discord.ui.Select) and not child.options:
                child.options = [discord.SelectOption(label=t, value=t) for t in sorted(view.items)][:25]
        await interaction.response.send_message(embed=view._embed(), view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Cog implementation
# ---------------------------------------------------------------------------
class QuizCog(commands.Cog):
    def __init__(self, bot: discord.AutoShardedBot):
        self.bot = bot

    @commands.slash_command(name="퀴즈", description="퀴즈 시스템을 관리합니다.")
    async def quiz_cmd(self, ctx: discord.ApplicationContext):
        guild_id = ctx.guild_id
        try:
            cfg_resp = await http_client.get(f"/quiz/config/{guild_id}")
            if cfg_resp.status_code != 200:
                return await ctx.respond("퀴즈 설정을 불러올 수 없습니다.", ephemeral=True)
            cfg = cfg_resp.json()

            if not cfg.get("enabled"):
                embed = discord.Embed(
                    title="🧩 퀴즈",
                    description="퀴즈 시스템이 활성화 되어있지 않아요.\n활성화 하시겠어요?",
                    color=discord.Color(QUIZ_EMBED_COLOR),
                )
                view = EnableButton(guild_id, ctx.channel_id)
                return await ctx.respond(embed=embed, view=view, ephemeral=True)

            # 활성 상태 패널
            sc_resp = await http_client.get(f"/quiz/schedules/{guild_id}")
            schedules = sc_resp.json() if sc_resp is not None and sc_resp.status_code == 200 else []
            channel_id = cfg.get("channel_id") or ctx.channel_id
            embed = make_admin_embed(cfg, schedules)
            view = AdminView(guild_id, channel_id)
            return await ctx.respond(embed=embed, view=view, ephemeral=True)

        except Exception:
            return await ctx.respond("설정 불러오기 중 오류가 발생했습니다.", ephemeral=True)

    @commands.slash_command(name="랭킹", description="퀴즈 랭킹을 확인합니다.")
    @option("기간", type=str, description="조회할 기간", choices=["주", "월", "전체"], default="주")
    async def ranking_cmd(self, ctx: discord.ApplicationContext, 기간: str):
        period_map = {"주": "week", "월": "month", "전체": "all"}
        period_val = period_map.get(기간, "week")

        try:
            resp = await http_client.get(
                "/quiz/ranking",
                params={"guild_id": ctx.guild_id, "period": period_val}
            )
            if resp.status_code != 200:
                return await ctx.respond("랭킹을 불러오지 못했습니다.", ephemeral=True)

            ranking = resp.json() or []
            if not ranking:
                return await ctx.respond("순위 정보가 없습니다.", ephemeral=True)

            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, row in enumerate(ranking[:20], start=1):
                prefix = medals[i-1] if i <= 3 else f"{i}."
                name = row.get("username") or str(row.get("user_id"))
                lines.append(f"{prefix} **{name}** ‒ {row.get('score', 0)}점")

            title = {"week": "이번 주 랭킹", "month": "이번 달 랭킹", "all": "전체 랭킹"}[period_val]
            embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
            await ctx.respond(embed=embed, ephemeral=True)

        except Exception:
            await ctx.respond("랭킹 조회 중 오류가 발생했습니다.", ephemeral=True)


def setup(bot: discord.AutoShardedBot):
    bot.add_cog(QuizCog(bot))