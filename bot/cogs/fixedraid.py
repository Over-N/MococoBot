# filename: cogs/fixedraid.py
import discord
import httpx
from discord.ext import commands
from typing import List, Dict, Any
from commands.fixedraid_commands import FixedRaidCommands, CreateFixedModal, NicknameModal, FixedSelect, build_embed

WEEK = "월화수목금토일"

def _state_to_options(items: List[Dict[str, Any]]) -> List[discord.SelectOption]:
    opts: List[discord.SelectOption] = []
    for i in items:
        wd = WEEK[int(i.get("weekday", 0)) % 7]
        hh = int(i.get("hour", 0))
        mm = int(i.get("minute", 0))
        boss = str(i.get("boss") or "")
        diff = str(i.get("difficulty") or "")
        cap = int(i.get("capacity") or 0)
        cnt = int(i.get("participants", i.get("member_count", 0)) or 0)
        label = f"[{wd}] {hh:02d}:{mm:02d} {boss} {diff} ({cnt}/{cap})"
        opts.append(discord.SelectOption(label=label[:100], value=str(i["id"])))
    return opts[:25]

class AdminView(discord.ui.View):
    def __init__(self, client: FixedRaidCommands):
        super().__init__(timeout=120)
        self.client = client

    @discord.ui.button(label="일정 생성", style=discord.ButtonStyle.primary)
    async def create_fixed(self, button, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateFixedModal(interaction.guild.id, self.client))

    @discord.ui.button(label="일정 삭제", style=discord.ButtonStyle.danger)
    async def delete_fixed(self, button, interaction: discord.Interaction):
        items = await self.client.fetch_dropdown(interaction.guild.id)
        opts = [discord.SelectOption(label=i.get("label") or str(i.get("id")), value=str(i["id"])) for i in (items or [])][:25]
        if not opts:
            await interaction.response.send_message("삭제할 일정이 없습니다.", ephemeral=True)
            return
        sel = FixedSelect(opts, "삭제할 일정을 선택하세요")

        async def on_select(i2: discord.Interaction):
            fr_id = int(sel.values[0])
            await i2.response.defer(ephemeral=True)
            await self.client.delete_fixed(fr_id)
            await i2.followup.send("삭제 완료", ephemeral=True)

        sel.callback = on_select
        v = discord.ui.View(timeout=60); v.add_item(sel)
        await interaction.response.send_message("삭제 대상 선택", view=v, ephemeral=True)

    @discord.ui.button(label="참가 인원 관리", style=discord.ButtonStyle.secondary)
    async def manage_members(self, button, interaction: discord.Interaction):
        items = await self.client.fetch_state(interaction.guild.id)
        if not items:
            await interaction.response.send_message("관리할 일정이 없습니다.", ephemeral=True)
            return
        opts = []
        for i in items:
            wd = WEEK[int(i.get("weekday", 0)) % 7]
            hh = int(i.get("hour", 0))
            mm = int(i.get("minute", 0))
            boss = str(i.get("boss") or "")
            diff = str(i.get("difficulty") or "")
            cnt = int(i.get("participants", i.get("member_count", 0)) or 0)
            cap = int(i.get("capacity") or 0)
            label = f"[{wd}] {hh:02d}:{mm:02d} {boss} {diff} ({cnt}/{cap})"
            opts.append(discord.SelectOption(label=label[:100], value=str(i["id"])))
        opts = opts[:25]
        sel = FixedSelect(opts, "관리할 일정을 선택하세요")

        async def on_select(i2: discord.Interaction):
            fr_id = int(sel.values[0])
            await i2.response.defer(ephemeral=True)
            members = await self.client.fetch_members(fr_id)
            if not members:
                desc = "현재 참가한 인원이 없습니다."
            else:
                lines = []
                for m in members:
                    role_label = "딜러" if int(m.get("role") or 0) == 0 else "서포터"
                    name = m.get("name") or m.get("nickname") or "무명"
                    class_name = m.get("class_name") or ""
                    class_emoji = m.get("class_emoji") or ""
                    lines.append(f"{name} ({role_label}) {class_emoji} {class_name}")
                desc = "\n".join(lines)
            embed = discord.Embed(title="참가 인원 관리", description=desc or "", color=discord.Color.orange())
            view = discord.ui.View(timeout=120)
            if members:
                for m in members[:25]:
                    name_label = (m.get("name") or m.get("nickname") or str(m.get("user_id")))[:80]
                    uid = int(m.get("user_id"))
                    btn = discord.ui.Button(label=f"❌ {name_label}", style=discord.ButtonStyle.danger)

                    def make_callback(user_id: int, name_label: str):
                        async def cb(inter: discord.Interaction):
                            try:
                                await self.client.leave_member(fr_id, user_id)
                                await inter.response.send_message(
                                    f"{name_label} 참가자가 제거되었습니다.",
                                    ephemeral=True,
                                )
                            except httpx.HTTPStatusError as e:
                                try:
                                    detail = e.response.json().get("detail", "")
                                except Exception:
                                    detail = e.response.text
                                await inter.response.send_message(
                                    f"오류: {detail or e}",
                                    ephemeral=True,
                                )
                        return cb
                    btn.callback = make_callback(uid, name_label)
                    view.add_item(btn)
            await i2.followup.send(embed=embed, view=view, ephemeral=True)

        sel.callback = on_select
        v = discord.ui.View(timeout=60)
        v.add_item(sel)
        await interaction.response.send_message("일정을 선택하세요.", view=v, ephemeral=True)

class MemberView(discord.ui.View):
    def __init__(self, client: FixedRaidCommands):
        super().__init__(timeout=120)
        self.client = client

    @discord.ui.button(label="일정 참가", style=discord.ButtonStyle.success)
    async def join(self, button, interaction: discord.Interaction):
        items = await self.client.fetch_state(interaction.guild.id)
        if not items:
            await interaction.response.send_message("참가 가능한 일정이 없습니다.", ephemeral=True)
            return
        opts = _state_to_options(items)
        sel = FixedSelect(opts, "참가할 일정을 선택하세요")

        async def on_select(i2: discord.Interaction):
            fr_id = int(sel.values[0])
            await i2.response.send_modal(NicknameModal(fr_id, i2.user.id, self.client))

        sel.callback = on_select
        v = discord.ui.View(timeout=60); v.add_item(sel)
        await interaction.response.send_message("일정 선택", view=v, ephemeral=True)

    @discord.ui.button(label="일정 참가 취소", style=discord.ButtonStyle.secondary)
    async def leave(self, button, interaction: discord.Interaction):
        items = await self.client.fetch_state(interaction.guild.id)
        if not items:
            await interaction.response.send_message("취소할 일정이 없습니다.", ephemeral=True)
            return
        opts: List[discord.SelectOption] = []
        for i in items:
            wd = WEEK[int(i.get("weekday", 0)) % 7]
            hh = int(i.get("hour", 0))
            mm = int(i.get("minute", 0))
            boss = str(i.get("boss") or "")
            diff = str(i.get("difficulty") or "")
            opts.append(discord.SelectOption(label=f"[{wd}] {hh:02d}:{mm:02d} {boss} {diff}"[:100], value=str(i["id"])))
        opts = opts[:25]

        sel = FixedSelect(opts, "취소할 일정을 선택하세요")

        async def on_select(i2: discord.Interaction):
            fr_id = int(sel.values[0])
            await i2.response.defer(ephemeral=True)
            await self.client.leave_member(fr_id, i2.user.id)
            await i2.followup.send("참가 취소 완료", ephemeral=True)

        sel.callback = on_select
        v = discord.ui.View(timeout=60); v.add_item(sel)
        await interaction.response.send_message("일정 선택", view=v, ephemeral=True)

class FixedRaidCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = FixedRaidCommands()

    @discord.slash_command(name="고정공격대", description="고정공격대")
    async def 고정공격대(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        data = await self.client.fetch_state(ctx.guild.id)
        embed = build_embed(data)
        view = discord.ui.View(timeout=120)
        if ctx.author.guild_permissions.administrator:
            av = AdminView(self.client)
            for c in av.children: view.add_item(c)
        mv = MemberView(self.client)
        for c in mv.children: view.add_item(c)
        await ctx.followup.send(embed=embed, view=view, ephemeral=True)

def setup(bot):
    bot.add_cog(FixedRaidCog(bot))
