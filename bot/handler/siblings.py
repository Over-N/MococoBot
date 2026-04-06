import discord
from core.http_client import http_client
from commands.siblings import ExpeditionManageView

async def handle_expedition_register_button(interaction: discord.Interaction):
    """원정대 등록 버튼 클릭 처리"""
    try:
        await interaction.response.defer(ephemeral=True)
        
        # 현재 등록된 캐릭터 수 조회
        count_response = await http_client.get(f"/siblings/{interaction.user.id}/count")
        
        registered_count = 0
        if count_response.status_code == 200:
            count_data = count_response.json()
            registered_count = count_data.get('character_count', 0)
        
        embed = discord.Embed(
            title="🏴‍☠️ 원정대 등록기능!",
            description="""**__원정대 등록이란?__** 레이드 일정에 참가신청 할 때
번거롭게 닉네임을 입력하지 않고, 등록해둔 원정대를 리스트로 출력해줘요.
또한 기존과 같이 닉네임 입력도 따로 가능합니다!

────────────────────────────────────────""",
            color=0x2b2d31
        )
        
        embed.add_field(
            name="📊 현재 등록 상태",
            value=f"등록된 캐릭터: **{registered_count}개**",
            inline=True
        )
        
        embed.add_field(
            name="🔧 **【기능 설명】**",
            value="""**➕ 캐릭터 등록**: 개별 캐릭터를 직접 등록
**📋 원정대 일괄 등록**: 한 캐릭터의 전체 원정대 조회 후 선택 등록
**🗑️ 등록된 캐릭터 삭제**: 등록된 캐릭터를 선택하여 삭제""",
            inline=False
        )
        
        view = ExpeditionManageView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        try:
            await interaction.followup.send(
                content=f"❌ 오류가 발생했습니다: {str(e)}", ephemeral=True
            )
        except:
            await interaction.followup.send(
                f"❌ 오류가 발생했습니다: {str(e)}", 
                ephemeral=True
            )