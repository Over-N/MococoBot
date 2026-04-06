import discord
from discord.ext import commands
from commands.verify_config import VerifyConfigView

class VerifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="인증", description="로스트아크 인증 시스템을 관리합니다. (관리자 전용)")
    @commands.has_permissions(administrator=True)
    async def 인증(self, ctx: discord.ApplicationContext):
        try:
            await VerifyConfigView.show_config_menu(ctx)
        except Exception as e:
            print(f"인증 명령어 오류 : {e}")
            try:
                await ctx.respond("처리 중 오류가 발생했습니다.", ephemeral=True)
            except Exception:
                pass

    @인증.error
    async def 인증_error(self, ctx: discord.ApplicationContext, error):
        if isinstance(error, commands.MissingPermissions):
            try:
                await ctx.respond("권한이 없어 해당 명령어 사용이 불가능해요!", ephemeral=True)
            except Exception:
                try:
                    await ctx.followup.send("권한이 없어 해당 명령어 사용이 불가능해요!", ephemeral=True)
                except Exception:
                    pass
            return

        if isinstance(error, commands.BotMissingPermissions):
            miss = getattr(error, "missing_permissions", None) or []
            msg = "봇 권한이 부족합니다." + (f" (필요: {', '.join(miss)})" if miss else "")
            try:
                await ctx.respond(msg, ephemeral=True)
            except Exception:
                try:
                    await ctx.followup.send(msg, ephemeral=True)
                except Exception:
                    pass
            return

        print(f"인증 명령어 예외: {error}")
        try:
            await ctx.respond("처리 중 오류가 발생했습니다.", ephemeral=True)
        except Exception:
            try:
                await ctx.followup.send("처리 중 오류가 발생했습니다.", ephemeral=True)
            except Exception:
                pass

def setup(bot):
    bot.add_cog(VerifyCog(bot))
