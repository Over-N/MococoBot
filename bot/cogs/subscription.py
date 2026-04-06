import discord
from discord.ext import commands

from core.http_client import http_client

TYPE_MAP = {"일일": "daily", "공지": "notice", "유튜브": "youtube"}


class SubscriptionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.slash_command(
        name="구독",
        description="알림을 구독/해제해요. (없으면 활성화, 있으면 비활성화)",
    )
    async def subscribe(
        self,
        ctx: discord.ApplicationContext,
        종류: str = discord.Option(
            str,
            required=True,
            choices=list(TYPE_MAP.keys()),
            description="알림 종류를 선택하세요.",
        ),
        채널: discord.TextChannel | None = discord.Option(
            discord.TextChannel,
            required=False,
            description="지정하면 서버 채널 알림, 미지정 시 개인 DM 알림",
        ),
    ):
        await ctx.defer(ephemeral=True)
        typ = TYPE_MAP[종류]

        try:
            if 채널 is not None:
                if ctx.guild_id is None:
                    await ctx.respond("서버에서만 사용할 수 있어요.", ephemeral=True)
                    return

                perms = getattr(ctx.author, "guild_permissions", None)
                if not getattr(perms, "administrator", False):
                    await ctx.respond("채널 옵션은 서버 관리자만 사용할 수 있어요.", ephemeral=True)
                    return

                if getattr(채널, "guild", None) and int(채널.guild.id) != int(ctx.guild_id):
                    await ctx.respond("현재 서버의 채널만 선택할 수 있어요.", ephemeral=True)
                    return

                payload = {"guild_id": int(ctx.guild_id), "channel_id": int(채널.id), "type": typ}
                resp = await http_client.post("/subscription/channel/toggle", json=payload)

                if not (200 <= resp.status_code < 300):
                    await ctx.respond(f"서버 채널 구독 처리 오류 (HTTP {resp.status_code})", ephemeral=True)
                    return

                data = resp.json()
                action = data.get("action")
                enabled = data.get("enabled", 0)

                if action == "enabled" and enabled == 1:
                    await ctx.respond(f"`{종류}` 서버 채널 구독이 활성화되었어요. 채널: <#{채널.id}>", ephemeral=True)
                elif action == "disabled" and enabled == 0:
                    await ctx.respond(f"`{종류}` 서버 채널 구독이 비활성화되었어요.", ephemeral=True)
                else:
                    await ctx.respond("구독 처리 결과를 해석할 수 없어요.", ephemeral=True)
                return

            payload = {"user_id": int(ctx.author.id), "type": typ}
            resp = await http_client.post("/subscription/user/toggle", json=payload)

            if not (200 <= resp.status_code < 300):
                await ctx.respond(f"개인(DM) 구독 처리 오류 (HTTP {resp.status_code})", ephemeral=True)
                return

            data = resp.json()
            action = data.get("action")
            enabled = data.get("enabled", 0)

            if action == "enabled" and enabled == 1:
                await ctx.respond(f"`{종류}` 개인(DM) 구독이 활성화되었어요.", ephemeral=True)
            elif action == "disabled" and enabled == 0:
                await ctx.respond(f"`{종류}` 개인(DM) 구독이 비활성화되었어요.", ephemeral=True)
            else:
                await ctx.respond("구독 처리 결과를 해석할 수 없어요.", ephemeral=True)
        except Exception:
            await ctx.respond("구독 처리 중 네트워크 오류가 발생했어요.", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(SubscriptionCog(bot))
