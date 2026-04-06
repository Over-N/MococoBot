import discord
from core.http_client import http_client
import datetime
from typing import Any, Dict, Optional

async def permission_check(interaction: discord.Interaction, party_id: int) -> Optional[Dict[str, Any]]:
    try:
        party_resp = await http_client.get(f"/party/{party_id}")
        if getattr(party_resp, "status_code", None) != 200:
            await interaction.response.send_message("파티 정보를 불러올 수 없습니다.", ephemeral=True)
            return None

        data = (party_resp.json() or {}).get("data") or {}
        owner_id = str(data.get("owner") or "")
        user_id = str(interaction.user.id)

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        guild = interaction.guild

        if member and getattr(member.guild_permissions, "administrator", False):
            return data

        if user_id == owner_id:
            return data

        if guild:
            server_resp = await http_client.get(f"/discord/server/{guild.id}")
            if getattr(server_resp, "status_code", None) == 200:
                server_data = (server_resp.json() or {}).get("data") or {}
                admin_role_id = server_data.get("admin_roles")
                if admin_role_id and member:
                    role = guild.get_role(int(admin_role_id))
                    if role and role in member.roles:
                        return data

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ 이 일정을 관리할 권한이 없습니다.\n"
                "권한이 필요한 조건:\n"
                "• 서버 관리자 권한\n"
                "• 일정 생성자\n"
                "• 서버에서 지정한 관리자 역할 보유",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ 이 일정을 관리할 권한이 없습니다.\n"
                "권한이 필요한 조건:\n"
                "• 서버 관리자 권한\n"
                "• 일정 생성자\n"
                "• 서버에서 지정한 관리자 역할 보유",
                ephemeral=True,
            )
        return None

    except Exception:
        if not interaction.response.is_done():
            await interaction.response.send_message("권한 확인 중 오류가 발생했습니다.", ephemeral=True)
        else:
            await interaction.followup.send("권한 확인 중 오류가 발생했습니다.", ephemeral=True)
        return None
        
class MessageInputModal(discord.ui.Modal):
    def __init__(self, callback, title="멘션 메시지 입력"):
        super().__init__(title=title)
        self.callback_func = callback
        self.add_item(discord.ui.InputText(label="보낼 메시지", placeholder="메시지를 입력하세요...", max_length=200))

    async def callback(self, interaction: discord.Interaction):
        sender = interaction.user.display_name
        await self.callback_func(interaction, self.children[0].value, sender)

class MentionTypeView(discord.ui.View):
    def __init__(self, party_id, chat_channel_id, participants):
        super().__init__(timeout=60)
        self.party_id = party_id
        self.chat_channel_id = chat_channel_id
        self.participants = participants

    @discord.ui.button(label="채팅방 멘션", style=discord.ButtonStyle.primary)
    async def mention_in_channel(self, button, interaction: discord.Interaction):
        async def send_message(interaction: discord.Interaction, message, sender):
            await interaction.response.defer(ephemeral=True)
            mentions = [f"<@{p['user_id']}>" for p in self.participants]
            embed = discord.Embed(
                description=message,
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now()
            )
            embed.set_footer(text=f"발송자: {sender}")
            thread = interaction.guild.get_thread(int(self.chat_channel_id))
            if thread:
                await thread.send(" ".join(mentions), embed=embed)
            else:
                await interaction.response.send_message("스레드를 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_modal(MessageInputModal(send_message, title="멘션 메시지 입력"))

    @discord.ui.button(label="DM 발송", style=discord.ButtonStyle.secondary)
    async def mention_dm(self, button, interaction: discord.Interaction):
        async def send_dm(interaction: discord.Interaction, message, sender):
            await interaction.response.defer(ephemeral=True)
            embed = discord.Embed(
                description=message,
                color=discord.Color.green(),
                timestamp=datetime.datetime.now()
            )
            embed.set_footer(text=f"발송자: {sender}")
            sent = 0
            for p in self.participants:
                user = interaction.guild.get_member(int(p['user_id']))
                if user:
                    try:
                        await user.send(embed=embed)
                        sent += 1
                    except Exception:
                        pass
            await sender.send(f"DM 발송 완료 하였어요! ({sent}명)")
        await interaction.response.send_modal(MessageInputModal(send_dm, title="DM 메시지 입력"))

class MemberDropdown(discord.ui.Select):
    def __init__(self, participants, party_id):
        options = [
            discord.SelectOption(
                label=f"{p['name']} ({'딜러' if p['role']==0 else '서포터'})",
                value=str(p['user_id']),
                description=f"클래스: {p.get('class_name','')}, 아이템레벨: {p.get('item_level','')}"
            )
            for p in participants
        ]
        super().__init__(placeholder="강제 참가 취소할 인원을 선택하세요", min_values=1, max_values=1, options=options)
        self.party_id = party_id

    async def callback(self, interaction: discord.Interaction):
        user_id = self.values[0]
        resp = await http_client.delete(f"/party/{self.party_id}/participants/{user_id}")
        msg = "강제 참가 취소 완료!" if resp.status_code == 200 else "참가 취소 실패!"
        await interaction.response.send_message(msg, ephemeral=True)

class ManagePartyView(discord.ui.View):
    def __init__(self, party_id, participants, chat_channel_id):
        super().__init__(timeout=180)
        self.party_id = party_id
        self.chat_channel_id = chat_channel_id
        self.participants = participants
        if participants:
            self.add_item(MemberDropdown(participants, party_id))

    @discord.ui.button(label="인원 멘션", style=discord.ButtonStyle.primary)
    async def mention_members(self, button, interaction: discord.Interaction):
        await interaction.response.send_message(
            "멘션 방식을 선택하세요.",
            view=MentionTypeView(self.party_id, self.chat_channel_id, self.participants),
            ephemeral=True
        )

    @discord.ui.button(label="일정 삭제", style=discord.ButtonStyle.danger)
    async def delete_party(self, button, interaction: discord.Interaction):
        resp = await http_client.delete(f"/party/{self.party_id}/delete")
        msg = "일정이 삭제되었습니다." if resp.status_code == 200 else "일정 삭제 실패!"
        await interaction.response.send_message(msg, ephemeral=True)