import discord
from discord.ext import commands
from discord import option
import random
import datetime
import aiohttp

LUCKY_ITEMS = [
    "운명의 파편 주머니 (대)", "아비도스 융화 재료", "운명의 파괴석", "운명의 돌파석",
    "8레벨 겁화의 보석", "8레벨 작열의 보석", "전설 카드 팩 IV", 
    "유물 각인서 선택 상자", "초월 복원권", "97돌", "품질 100 무기"
]
LUCKY_LOCATIONS = [
    "아브렐슈드(2막)", "에기르", "아르모체", "에키드나", "카제로스(종막)", "쿠르잔 전선 (카던)", "에브니 큐브",
    "모코코 마을", "영지 농장", "카오스 게이트"
]

LUCKY_COLORS = ["찬란한 황금색", "타오르는 붉은색", "차분한 파란색", "신비로운 보라색", "싱그러운 초록색", "깊은 심연의 검은색", "순수한 흰색", "에스더의 민트색"]

TEXTS_EXCELLENT = [
    "강화 확률이 뚫려있습니다! 누르면 붙는 날입니다. 무기 강화를 시도해보세요.",
    "레이드에서 산책 딜을 해도 클리어되는 기적의 날입니다. 공대원 운이 최상입니다.",
    "길가다 주운 돌이 97돌이 될 운세입니다. 어빌리티 스톤을 깎아보세요.",
    "경매장에서 원하던 악세서리가 헐값에 올라올 징조가 보입니다.",
    "카던에서 편린이 쏟아질 운세입니다. 오늘은 꼭 숙제를 다 하세요!",
    "품질작 대성공! 한 번의 클릭으로 보라색 품질이 하늘색이 될 날입니다."
]

# 70~89점 (길)
TEXTS_GOOD = [
    "전반적으로 운이 좋습니다. 숙제가 빠르게 끝나는 날입니다.",
    "파티 찾기 창에서 쾌적한 파티를 만날 수 있습니다. 서폿이 금방 구해집니다.",
    "적은 비용으로 엘릭서 깎기에 성공할 수 있는 날입니다. 할머니/할아버지가 돕네요.",
    "초월이 생각보다 잘 붙습니다. 두려워하지 말고 눌러보세요.",
    "오늘따라 패턴이 눈에 잘 들어옵니다. 잔혈(MVP)을 노려볼 만합니다.",
    "큐브에서 금방 입장권이 뜰 것 같은 좋은 예감입니다."
]

# 40~69점 (평)
TEXTS_NORMAL = [
    "무난한 하루입니다. 장기백은 보지 않겠지만 원트도 힘듭니다.",
    "너무 큰 욕심은 화를 부릅니다. 적당히 타협하면 평온한 로생이 됩니다.",
    "레이드 리트가 몇 번 나겠지만, 결국은 클리어할 운세입니다.",
    "강화보다는 내실을 다지기에 좋은 날입니다. 모험의 서를 채워보세요.",
    "골드 벌이는 평범하지만, 지출을 줄이면 이득인 날입니다.",
    "오늘은 채팅을 조심하세요. 사소한 오해가 생길 수 있습니다."
]

# 40점 미만 (흉)
TEXTS_BAD = [
    "장기백의 기운이 감돕니다... 오늘은 재련 NPC 근처에도 가지 마세요.",
    "공팟 빌런을 만날 확률이 높습니다. 고정 파티나 지인과 함께하세요.",
    "돌을 깎다가 25%, 35% 확률에 연속으로 성공하고 75%에 실패할 운입니다.",
    "접속하자마자 서버 불안정이나 튕김 현상을 겪을 수 있습니다.",
    "숙제만 하려다가 3시간 동안 감금될 수 있으니 마음을 비우세요.",
    "충동적인 과금은 금물입니다. 지갑을 닫고 산책이나 다녀오세요."
]

class LostArkFortuneCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="운세", description="로스트아크 캐릭터의 오늘 운세를 확인합니다.")
    @option("닉네임", description="운세를 확인할 캐릭터 닉네임을 입력하세요.")
    async def fortune(self, ctx: discord.ApplicationContext, 닉네임: str):
        await ctx.defer()

        # 시드 설정
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        seed_value = f"{today}_{닉네임}"
        random.seed(seed_value)

        # 운세 점수 생성 및 텍스트 선정
        total_luck = random.randint(1, 100) # 총운
        
        # 총운에 따른 멘트 및 색상 결정
        if total_luck >= 90:
            fortune_msg = random.choice(TEXTS_EXCELLENT)
            title_text = "✨ 오늘은 뭘 해도 되는 날!"
            embed_color = 0xFFD700 # 골드
        elif total_luck >= 70:
            fortune_msg = random.choice(TEXTS_GOOD)
            title_text = "🍀 기분 좋은 바람이 붑니다"
            embed_color = 0x00FF00 # 그린
        elif total_luck >= 40:
            fortune_msg = random.choice(TEXTS_NORMAL)
            title_text = "☕ 평온한 아크라시아의 하루"
            embed_color = 0x3498DB # 블루
        else:
            fortune_msg = random.choice(TEXTS_BAD)
            title_text = "⚡ 이불 밖은 위험해요..."
            embed_color = 0xFF0000 # 레드

        # 세부 운세 수치 (0~100%)
        # 총운이 높으면 세부 운세도 높게 나올 확률 보정 (약간의 연관성 부여)
        base_bonus = (total_luck - 50) // 2
        honing_luck = max(0, min(100, random.randint(1, 100) + base_bonus)) # 재련
        raid_luck = max(0, min(100, random.randint(1, 100) + base_bonus))   # 레이드
        stone_luck = max(0, min(100, random.randint(1, 100) + base_bonus))  # 세공/초월/엘릭서
        
        # 랜덤 아이템 및 추천
        lucky_item = random.choice(LUCKY_ITEMS)
        lucky_loc = random.choice(LUCKY_LOCATIONS)
        lucky_color = random.choice(LUCKY_COLORS)

        # 임베드 구성
        embed = discord.Embed(
            title=f"🔮 {닉네임}님의 운세 ({today})",
            description=f"**{title_text}**",
            color=embed_color
        )

        # 게이지 바 생성 함수
        def make_bar(val):
            filled = val // 10
            # 색상 다르게 표현 (높으면 초록, 낮으면 빨강 느낌)
            icon = "🟩" if val >= 50 else "🟧" if val >= 20 else "🟥"
            return f"{icon * filled}{'⬜' * (10 - filled)} ({val}%)"

        # 메인 내용
        embed.add_field(name="📜 오늘의 조언", value=f"```fix\n{fortune_msg}\n```", inline=False)
        
        embed.add_field(name="🔨 재련/강화 운", value=make_bar(honing_luck), inline=True)
        embed.add_field(name="⚔️ 레이드/파티 운", value=make_bar(raid_luck), inline=True)
        embed.add_field(name="💎 어빌리티 스톤 세공", value=make_bar(stone_luck), inline=True)
        
        embed.add_field(name="", value="━━━━━━━━━━━━━━━━━━━━━━", inline=False) # 구분선 역할

        embed.add_field(name="🎁 행운의 아이템", value=lucky_item, inline=True)
        embed.add_field(name="📍 추천 장소/레이드", value=lucky_loc, inline=True)
        embed.add_field(name="🎨 행운의 색상", value=lucky_color, inline=True)

        embed.add_field(name="📊 종합 운세 점수", value=f"**{total_luck}점**", inline=False)
        
        embed.set_footer(text="재미로 보는 운세!")

        await ctx.followup.send(embed=embed)

def setup(bot):
    bot.add_cog(LostArkFortuneCog(bot))