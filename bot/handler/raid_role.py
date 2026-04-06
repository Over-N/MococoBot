import discord
from commands.raid_role import RaidRoleDropdownView

async def handle_raid_role_manage(interaction: discord.Interaction):
    """레이드 역할 관리 버튼 클릭 시"""
    try:
        user_roles = interaction.user.roles
        view = RaidRoleDropdownView(user_roles)
        
        await interaction.response.send_message(
            view=view,
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"오류가 발생했습니다: {e}", ephemeral=True)