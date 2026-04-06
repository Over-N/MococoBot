import discord
from discord.ext import commands
from discord import option
from commands.siblings import ExpeditionRegisterChannelModal
from handler.siblings import handle_expedition_register_button

class SiblingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="원정대", description="원정대 등록 및 관리 기능입니다.")
    async def expedition_manage(self, ctx: discord.ApplicationContext):
        try:
            await handle_expedition_register_button(ctx.interaction)
        except Exception as e:
            print(f"오류: {e}")

    @discord.slash_command(name="원정대등록채널", description="원정대 등록 버튼을 특정 채널에 보냅니다.")
    @option("채널", description="원정대 등록 버튼을 보낼 채널을 선택하세요.", type=discord.TextChannel)
    @commands.has_permissions(administrator=True)
    async def expedition_register_channel(self, ctx: discord.ApplicationContext, 채널: discord.TextChannel):
        try:
            # 채널 권한 확인
            bot_permissions = 채널.permissions_for(ctx.guild.me)
            if not bot_permissions.send_messages:
                await ctx.respond(
                    f"❌ {채널.mention} 채널에 메시지를 보낼 권한이 없습니다.", 
                    ephemeral=True
                )
                return
            
            if not bot_permissions.embed_links:
                await ctx.respond(
                    f"❌ {채널.mention} 채널에 임베드를 보낼 권한이 없습니다.", 
                    ephemeral=True
                )
                return
            
            # 모달 표시
            modal = ExpeditionRegisterChannelModal(채널)
            await ctx.response.send_modal(modal)
            
        except Exception as e:
            await ctx.respond(f"❌ 오류가 발생했습니다: {str(e)}", ephemeral=True)

    @expedition_register_channel.error
    async def expedition_register_channel_error(self, ctx: discord.ApplicationContext, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.respond(
                "❌ 관리자 권한이 없어 해당 명령어를 사용할 수 없습니다!", 
                ephemeral=True
            )

def setup(bot):
    bot.add_cog(SiblingsCog(bot))