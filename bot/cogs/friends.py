import os, time, hmac, hashlib, io
import discord
from discord.ext import commands
from core.http_client import http_client
from core.config import ADMIN_2FA_SECRET

def build_admin_2fa_headers(admin_discord_id: int, method: str, path: str):
    ts = int(time.time())
    base = f"{admin_discord_id}:{method}:{path}:{ts}"
    sig = hmac.new(ADMIN_2FA_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Admin-Discord-Id": str(admin_discord_id),
        "X-Admin-Timestamp": str(ts),
        "X-Admin-Signature": sig
    }

class FriendsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="친구", description="친구찾기 기능을 사용해요.")
    async def 친구(self, ctx: discord.ApplicationContext):
        # 프로필 및 매칭 상태 확인
        has_profile = False
        is_matched = False
        
        try:
            profile_resp = await http_client.get(f"/friends/profile/{ctx.user.id}")
            if profile_resp.status_code == 200:
                profile_data = profile_resp.json()
                has_profile = profile_data.get("has_profile", False)
                if has_profile:
                    # 프로필이 있으면 current_match에서 매칭 상태 확인
                    current_match = profile_data.get("current_match", {})
                    is_matched = current_match.get("is_matched", False)
            elif profile_resp.status_code == 404:
                # 404면 프로필 없음
                has_profile = False
                is_matched = False
        except Exception as e:
            print(f"프로필 확인 오류: {e}")
            has_profile = False
            is_matched = False
        
        v = discord.ui.View(timeout=180)
        v.add_item(discord.ui.Button(label="프로필 등록", style=discord.ButtonStyle.primary, custom_id="ff_profile"))
        
        # 프로필이 없거나 매칭된 상태라면 친구 매칭 버튼 비활성화
        match_disabled = not has_profile or is_matched
        
        if not has_profile:
            match_button_label = "친구 매칭 (프로필 먼저 등록)"
        elif is_matched:
            match_button_label = "친구 매칭 (이미 매칭됨)"
        else:
            match_button_label = "친구 매칭"
        
        match_button = discord.ui.Button(
            label=match_button_label,
            style=discord.ButtonStyle.success if not match_disabled else discord.ButtonStyle.secondary,
            custom_id="ff_match",
            disabled=match_disabled
        )
        v.add_item(match_button)
        
        v.add_item(discord.ui.Button(label="내 프로필 보기", style=discord.ButtonStyle.secondary, custom_id="ff_myprofile"))
        v.add_item(discord.ui.Button(label="프로필 삭제", style=discord.ButtonStyle.danger, custom_id="ff_profile_delete"))
        
        # embed 설명을 상태에 따라 수정
        base_description = (
            "**로아 친구를 찾아보아요!**\n"
            "\n"
            "1) **[프로필 등록]** 버튼을 눌러 캐릭터 이름으로 프로필을 만들어요.\n"
            "2) **[친구 매칭]** 버튼으로 등록된 유저 중 한 명의 프로필을 확인해요.\n"
            "3) 마음에 들면 **[좋아요]**! 상대에게 익명으로 DM 알림이 전송돼요.\n"
            "4) 매칭이 성사되면, **봇에게 DM**을 보내면 서로에게 자동 전달됩니다.\n"
            "\n"
            "🧷 **매칭 해제**\n"
            "• 매칭을 끝내려면 `/매칭해제` 를 입력하세요.\n"
            "• 매칭 중에는 다른 유저와 새로운 매칭이 불가합니다.\n"
            "• 상대가 이미 다른 사람과 매칭 중이면 매칭되지 않아요.\n"
        )
        
        if not has_profile:
            description = f"**📝 프로필을 먼저 등록해주세요!**\n친구 매칭을 하려면 먼저 **[프로필 등록]**을 완료해야 해요.\n\n{base_description}"
            embed_color = discord.Color.orange()
        elif is_matched:
            description = f"**🎉 현재 매칭 상태입니다!**\n매칭을 해제하려면 `/매칭해제` 명령어를 사용하세요.\n\n{base_description}"
            embed_color = discord.Color.green()
        else:
            description = base_description
            embed_color = discord.Color.blue()
        
        embed = discord.Embed(
            title="로스트아크 친구 찾기!", 
            description=description,
            color=embed_color
        )
        embed.set_footer(text="베타테스트 버전으로 오류가 많을 수 있습니다.")
        await ctx.respond(embed=embed, view=v, ephemeral=True)

    @discord.slash_command(name="매칭해제", description="현재 매칭을 해제합니다.")
    async def 매칭해제(self, ctx: discord.ApplicationContext):
        # 매칭 해제 실행 (API가 partner_id 반환)
        r = await http_client.post("/friends/unmatch", json={"user_id": ctx.user.id})
        
        if r.status_code == 200:
            data = r.json()
            ok = data.get("ok")
            partner_id = data.get("partner_id")
            
            if ok:
                await ctx.respond("✅ 매칭을 해제했습니다.", ephemeral=True)
                
                # 상대방에게 알림 전송 (API에서 partner_id 반환)
                if partner_id:
                    try:
                        partner_user = await self.bot.fetch_user(int(partner_id))
                        if partner_user:
                            embed = discord.Embed(
                                title="💔 매칭이 해제되었습니다",
                                description="상대방이 매칭을 해제했습니다.\n\n새로운 친구를 찾으려면 `/친구` 명령어를 사용해주세요!",
                                color=discord.Color.orange()
                            )
                            await partner_user.send(embed=embed)
                    except Exception as e:
                        print(f"매칭 해제 알림 전송 실패: {e}")
            else:
                message = data.get("message", "")
                if message == "no_active_match":
                    await ctx.respond("활성 매칭이 없습니다.", ephemeral=True)
                else:
                    await ctx.respond("매칭 해제에 실패했습니다.", ephemeral=True)
        else:
            await ctx.respond("매칭 해제 중 오류가 발생했습니다.", ephemeral=True)

    @discord.slash_command(name="친구로그", description="(관리자) 매치 로그 열람", guild_ids=[1227993158596694139])  # 관리자 전용 명령어
    async def 친구로그(self, ctx: discord.ApplicationContext, match_id: int):
        if not ADMIN_2FA_SECRET:
            return await ctx.respond("서버에 2FA 비밀키가 설정되어 있지 않습니다.", ephemeral=True)
        # 2FA 헤더 빌드
        path = f"/friends/logs/{match_id}"
        headers = build_admin_2fa_headers(ctx.user.id, "GET", path)
        r = await http_client.get(path, headers=headers)
        if r.status_code != 200:
            return await ctx.respond(f"열람 실패: {r.status_code} {r.text}", ephemeral=True)
        rows = (r.json() or {}).get("data", [])
        if not rows:
            return await ctx.respond("로그가 없습니다.", ephemeral=True)
        text = "\n".join([f"[{row['created_at']}] {row['sender_id']}: {row['content']}" for row in rows if row.get("content")]) or "(메시지 없음)"
        if len(text) > 1800:
            return await ctx.respond(file=discord.File(io.BytesIO(text.encode()), filename=f"match_{match_id}.txt"), ephemeral=True)
        await ctx.respond(f"```\n{text}\n```", ephemeral=True)

def setup(bot):
    bot.add_cog(FriendsCog(bot))
