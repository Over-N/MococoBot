import discord
from core.http_client import http_client
from typing import List, Optional

QUIZ_EMBED_COLOR = 0xF1C232

def make_quiz_embed(
    question: str,
    *,
    solved_by: Optional[str] = None,
    explanation: Optional[str] = None,
    image_url: Optional[str] = None,
    answer_text: Optional[str] = None,
) -> discord.Embed:
    description = question
    if solved_by:
        description += f"\n\n✅ **{solved_by}** 님이 정답을 맞추었어요!"
        if answer_text:
            description += f"\n\n🧩 정답: {answer_text}"
        if explanation:
            description += f"\n\n📘 해설\n{explanation}"
    embed = discord.Embed(title="오늘의 퀴즈", description=description, color=QUIZ_EMBED_COLOR)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="정답은 버튼으로 제출하세요")
    return embed

_WRONG_FIELD_NAME = "❌ 오답 기록"
_MAX_LINES = 10
_MAX_FIELD_LEN = 1024

def _sanitize_answer_for_log(s: str) -> str:
    s = (s or "").replace("`", "'").replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())  # collapse spaces
    return (s[:57] + "…") if len(s) > 60 else s

def _extract_question_and_image_from_embed(embed: Optional[discord.Embed]) -> (str, Optional[str]):
    if embed is None:
        return ("퀴즈", None)
    desc = embed.description or "퀴즈"
    question = desc.split("\n", 1)[0].strip() or "퀴즈"
    image_url = embed.image.url if embed.image else None
    return (question, image_url)

def _build_embed_with_wrong_log(
    base_embed: Optional[discord.Embed],
    username: str,
    answer_for_log: str
) -> discord.Embed:
    question, image_url = _extract_question_and_image_from_embed(base_embed)

    solved_by = None
    explanation = None
    if base_embed:
        desc = base_embed.description or ""
        solved_flag = "✅ " in desc and "정답을 맞추었어요" in desc
        if solved_flag:
            try:
                start = desc.index("✅ ") + 2
                seg = desc[start:].split("**", 2)
                if len(seg) >= 3:
                    solved_by = seg[1]
            except Exception:
                pass
            if "📘 해설" in desc:
                explanation = desc.split("📘 해설", 1)[-1].strip()

    new_embed = make_quiz_embed(question, solved_by=solved_by, explanation=explanation, image_url=image_url)

    existing_lines: List[str] = []
    if base_embed and base_embed.fields:
        for f in base_embed.fields:
            if f.name == _WRONG_FIELD_NAME:
                existing_lines = [ln for ln in (f.value or "").splitlines() if ln.strip()]
                break

    new_line = f"• **{username}**: `{answer_for_log}`"
    existing_lines.append(new_line)

    while len(existing_lines) > _MAX_LINES:
        existing_lines.pop(0)
    while True:
        val = "\n".join(existing_lines)
        if len(val) <= _MAX_FIELD_LEN:
            break
        if not existing_lines:
            break
        existing_lines.pop(0)

    if existing_lines:
        new_embed.add_field(name=_WRONG_FIELD_NAME, value="\n".join(existing_lines), inline=False)

    return new_embed

class QuizAnswerModal(discord.ui.Modal):
    def __init__(self, guild_id: int, message_id: int):
        super().__init__(title="정답 입력")
        self.guild_id = guild_id
        self.message_id = message_id
        self.answer_input = discord.ui.InputText(label="정답", placeholder="정답을 입력하세요")
        self.add_item(self.answer_input)

    async def callback(self, interaction: discord.Interaction):
        answer = self.answer_input.value or ""
        try:
            resp = await http_client.post(
                "/quiz/attempt",
                json={
                    "guild_id": self.guild_id,
                    "message_id": self.message_id,
                    "user_id": interaction.user.id,
                    "username": interaction.user.display_name,
                    "answer": answer,
                },
            )
            data = resp.json()
        except Exception:
            await interaction.response.send_message("정답 확인 중 오류가 발생했습니다.", ephemeral=True)
            return

        # 메시지/임베드 확보 시도
        channel = interaction.channel
        message = None
        if channel:
            try:
                message = await channel.fetch_message(self.message_id)
            except Exception:
                message = None

        # 정답 처리
        if data.get("is_correct"):
            first_solver = data.get("first_solver")
            explanation = data.get("explanation")
            solved_by = data.get("solved_by", {}).get("username")
            answer_raw = data.get("answer_raw")

            if message and message.embeds:
                question, image_url = _extract_question_and_image_from_embed(message.embeds[0])
            else:
                question, image_url = ("퀴즈", None)

            new_embed = make_quiz_embed(
                question,
                solved_by=solved_by,
                explanation=explanation,
                image_url=image_url,
                answer_text=answer_raw,
            )
            if message:
                await message.edit(embed=new_embed, view=None)

            await interaction.response.send_message("🎉 정답입니다!", ephemeral=True)
            return

        already_solved = data.get("already_solved")
        sanitized = _sanitize_answer_for_log(answer)

        if message:
            base_embed = message.embeds[0] if message.embeds else None
            new_embed = _build_embed_with_wrong_log(base_embed, interaction.user.display_name, sanitized)
            try:
                await message.edit(embed=new_embed)
            except Exception:
                pass

        if already_solved:
            solved_info = data.get("solved_by") or {}
            solver_name = solved_info.get("username") or "알 수 없음"
            await interaction.response.send_message(
                f"이미 **{solver_name}** 님이 정답을 맞췄습니다.", ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ 오답입니다.", ephemeral=True)

# 버튼 → 모달
async def open_quiz_answer_modal(interaction: discord.Interaction):
    message_id = interaction.message.id if interaction.message else 0
    await interaction.response.send_modal(QuizAnswerModal(interaction.guild_id, message_id))
