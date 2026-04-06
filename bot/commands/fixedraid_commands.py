# filename: commands/fixedraid_commands.py
import discord
from typing import List, Dict, Any, Optional
from core.http_client import http_client
from core.raid_data import raid_list, raid_difficulty_map
import httpx

WEEKDAYS_LABEL = ["월","화","수","목","금","토","일"]

def _weekday_opts() -> List[discord.SelectOption]:
    return [discord.SelectOption(label=f"{i}({WEEKDAYS_LABEL[i]})", value=str(i)) for i in range(7)]

def _hour_opts() -> List[discord.SelectOption]:
    return [discord.SelectOption(label=f"{h:02d}", value=f"{h:02d}") for h in range(24)]

def _minute_opts(step: int = 5) -> List[discord.SelectOption]:
    return [discord.SelectOption(label=f"{m:02d}", value=f"{m:02d}") for m in range(0, 60, step)]

def _raid_opts() -> List[discord.SelectOption]:
    return [discord.SelectOption(label=v, value=v) for v in raid_list[:25]]

def _diff_opts() -> List[discord.SelectOption]:
    diffs, seen = [], set()
    for v in raid_difficulty_map.values():
        for d in v:
            if d not in seen:
                seen.add(d); diffs.append(d)
    return [discord.SelectOption(label=v, value=v) for v in diffs[:25]]

class FixedRaidCommands:
    def __init__(self):
        self.client = None
    async def setup(self):
        self.client = http_client
    async def api_get(self, endpoint: str):
        if not self.client:
            await self.setup()
        r = await self.client.get(endpoint)
        r.raise_for_status()
        return r.json()
    async def api_post(self, endpoint: str, data: dict):
        if not self.client:
            await self.setup()
        r = await self.client.post(endpoint, json=data)
        r.raise_for_status()
        return r.json()
    async def api_delete(self, endpoint: str, data: dict):
        if not self.client:
            await self.setup()
        r = await self.client.delete(endpoint, json=data)
        r.raise_for_status()
        return r.json() if r.content else {"ok": True}
    async def fetch_state(self, guild_id: int) -> List[Dict[str, Any]]:
        res = await self.api_get(f"/fixedraid/state?guild_id={guild_id}")
        return res.get("data", res if isinstance(res, list) else [])
    async def fetch_dropdown(self, guild_id: int) -> List[Dict[str, Any]]:
        res = await self.api_get(f"/fixedraid/dropdown?guild_id={guild_id}")
        return res.get("data", res if isinstance(res, list) else [])
    async def create_fixed(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api_post("/fixedraid/create", payload)
    async def delete_fixed(self, fixed_raid_id: int) -> Dict[str, Any]:
        return await self.api_delete("/fixedraid/delete", {"fixed_raid_id": fixed_raid_id})
    async def join_member(self, fixed_raid_id: int, user_id: int, nickname: Optional[str] = None, character_id: Optional[int] = None, role: int = 0) -> Dict[str, Any]:
        return await self.api_post("/fixedraid/join", {"fixed_raid_id": fixed_raid_id, "user_id": user_id, "character_id": character_id, "role": role, "nickname": nickname})
    async def leave_member(self, fixed_raid_id: int, user_id: int) -> Dict[str, Any]:
        return await self.api_post("/fixedraid/leave", {"fixed_raid_id": fixed_raid_id, "user_id": user_id})

    async def fetch_members(self, fixed_raid_id: int) -> List[Dict[str, Any]]:
        """
        Retrieve the list of members for a given fixed raid.  Returns an empty list if none exist.
        """
        res = await self.api_get(f"/fixedraid/members?fixed_raid_id={fixed_raid_id}")
        if not isinstance(res, dict):
            return res if isinstance(res, list) else []
        return res.get("items", res.get("data", []) if isinstance(res.get("data"), list) else [])

class CreateFixedModal(discord.ui.DesignerModal):
    def __init__(self, guild_id: int, client: FixedRaidCommands):
        super().__init__(title="고정공격대 일정 등록", custom_id="fixedraid_modal_v2")
        self.guild_id = guild_id
        self.client = client

        self.weekday = discord.ui.Select(placeholder="요일 선택", custom_id="weekday", options=_weekday_opts(), min_values=1, max_values=1)
        self.hour = discord.ui.Select(placeholder="시 선택 (00~23)", custom_id="hour", options=_hour_opts(), min_values=1, max_values=1)
        self.minute = discord.ui.Select(placeholder="분 선택 (00~59, 5분 단위)", custom_id="minute", options=_minute_opts(5), min_values=1, max_values=1)
        self.raid = discord.ui.Select(placeholder="레이드 선택", custom_id="raid", options=_raid_opts(), min_values=1, max_values=1)
        self.difficulty = discord.ui.Select(placeholder="난이도 선택", custom_id="difficulty", options=_diff_opts(), min_values=1, max_values=1)

        self.add_item(discord.ui.Label(label="요일", item=self.weekday))
        self.add_item(discord.ui.Label(label="시", item=self.hour))
        self.add_item(discord.ui.Label(label="분", item=self.minute))
        self.add_item(discord.ui.Label(label="레이드", item=self.raid))
        self.add_item(discord.ui.Label(label="난이도", item=self.difficulty))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        wd = int(self.weekday.values[0])
        hh = int(self.hour.values[0])
        mm = int(self.minute.values[0])
        boss = self.raid.values[0]
        diff = self.difficulty.values[0]

        payload = {
            "guild_id": interaction.guild.id,
            "weekday": wd,
            "hour": hh,
            "minute": mm,
            "boss": boss,
            "difficulty": diff,
            "created_by_user_id": interaction.user.id,
        }
        line = f"[{WEEKDAYS_LABEL[wd]}] {hh:02d}:{mm:02d} {boss} {diff}\n정말로 등록하시겠어요?"
        await interaction.followup.send(line, view=ConfirmFixedView(self.client, payload), ephemeral=True)

class MessageModal(discord.ui.Modal):
    def __init__(self, parent_view: "ConfirmFixedView"):
        super().__init__(title="메시지 입력", custom_id="fixedraid_msg_v1")
        self.parent_view = parent_view
        self.msg = discord.ui.InputText(label="메시지", style=discord.InputTextStyle.long, required=False, max_length=200)
        self.add_item(self.msg)
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.msg_text = (self.msg.value or "").strip() or None
        p = self.parent_view.payload
        line = f"[{WEEKDAYS_LABEL[p['weekday']]}] {int(p['hour']):02d}:{int(p['minute']):02d} {p['boss']} {p['difficulty']}"
        if self.parent_view.msg_text:
            line += f"\n메시지: {self.parent_view.msg_text}"
        await interaction.response.edit_message(content=line + "\n정말로 등록하시겠어요?", view=self.parent_view)

class ConfirmFixedView(discord.ui.View):
    def __init__(self, client: FixedRaidCommands, payload: Dict[str, Any], msg_text: Optional[str] = None):
        super().__init__(timeout=120)
        self.client = client
        self.payload = payload
        self.msg_text = msg_text
    @discord.ui.button(label="등록", style=discord.ButtonStyle.primary, custom_id="fixedraid_confirm")
    async def confirm(self, _: discord.ui.Button, interaction: discord.Interaction):
        data = dict(self.payload)
        if self.msg_text:
            data["message"] = self.msg_text
        try:
            res = await self.client.create_fixed(data)
            if not res or not res.get("ok"):
                await interaction.response.edit_message(content="생성 실패.", view=None)
                return
            await interaction.response.edit_message(content="고정공격대 일정이 생성되었습니다.", view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"생성 실패: {e}", view=None)
    @discord.ui.button(label="메시지 입력", style=discord.ButtonStyle.secondary, custom_id="fixedraid_addmsg")
    async def add_message(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(MessageModal(self))
    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, custom_id="fixedraid_cancel")
    async def cancel(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="생성이 취소되었습니다.", view=None)

class NicknameModal(discord.ui.Modal):
    def __init__(self, fixed_raid_id: int, user_id: int, client: FixedRaidCommands):
        super().__init__(title="닉네임 입력", custom_id="fixedraid_nick_v1")
        self.fixed_raid_id = fixed_raid_id
        self.user_id = user_id
        self.client = client
        self.nick = discord.ui.InputText(label="닉네임")
        self.add_item(self.nick)
    async def callback(self, interaction: discord.Interaction):
        try:
            await self.client.join_member(self.fixed_raid_id, self.user_id, nickname=(self.nick.value or "").strip() or None)
            await interaction.response.send_message("참가 완료", ephemeral=True)
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", "")
            except Exception:
                detail = e.response.text
            text = str(detail).lower()
            if ("duplicate" in text) or ("already" in text) or ("이미" in str(detail)):
                await interaction.response.send_message("이미 참가한 일정입니다.", ephemeral=True)
            else:
                await interaction.response.send_message(f"오류: {detail or e}", ephemeral=True)

class FixedSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

def build_embed(payload) -> discord.Embed:
    items = payload.get("data") if isinstance(payload, dict) else payload
    lines: List[str] = []
    for i in (items or []):
        wd = WEEKDAYS_LABEL[int(i.get("weekday", 0)) % 7]
        hh = f"{int(i.get('hour', 0)):02d}"
        mm = f"{int(i.get('minute', 0)):02d}"
        boss = str(i.get("boss") or "")
        diff = str(i.get("difficulty") or "")
        msg = str(i.get("message") or "")
        cap = int(i.get("capacity") or 0)
        cnt = int(i.get("participants", i.get("member_count", 0)) or 0)
        line = f"[{wd}] {hh}:{mm} {boss} {diff}"
        if msg:
            line += f" : {msg}"
        line += f" ({cnt}/{cap})"
        lines.append(line)
    desc = "고정공격대 기능이란?\n수요일마다 매주 설정한 요일 기준으로 일정을 자동으로 생성해요.\n`/레이드`와 동일하게 생성되며, 고정공격대 멤버로 등록된 인원은 자동 참가됩니다.\n\n"
    desc += "\n".join(lines) if lines else "등록된 고정공격대가 없습니다."
    return discord.Embed(title="고정공격대", description=desc, color=discord.Color.blurple())
