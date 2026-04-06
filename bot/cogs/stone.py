from __future__ import annotations
import discord
from discord.ext import commands
from typing import Optional, Literal, Tuple, Dict, List
from functools import lru_cache
from datetime import datetime

COLOR_PRIMARY = discord.Color.blurple()
COLOR_INFO = discord.Color.teal()
COLOR_DONE = discord.Color.gold()

EMO_BLUE = "🟦"
EMO_RED = "🟥"
EMO_FAIL = "⬛"
EMO_EMPTY = "⬜"

EMO_WARN = "⚠️"
EMO_CHECK = "✅"
EMO_STOP = "🛑"
EMO_ROBOT = "🤖"
EMO_NEXT = "➡️"
EMO_SUCCESS = "🆗"
EMO_FAIL_BTN = "❌"
EMO_HAMMER = "⚒️"

MAX_SLOTS = 10
P_MIN, P_MAX, P_STEP = 25, 75, 10

DEC_LIMITS: Dict[Tuple[int, int], int] = {(7, 7): 4, (9, 7): 2, (10, 6): 2}

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))

def _counts(seq: List[bool]) -> Tuple[int, int]:
    s = sum(1 for x in seq if x)
    f = len(seq) - s
    return s, f

def fmt_line_seq(seq: List[bool], color: Literal["blue", "red"]) -> str:
    bar: List[str] = []
    for ok in seq:
        if ok:
            bar.append(EMO_BLUE if color == "blue" else EMO_RED)
        else:
            bar.append(EMO_FAIL)
    if len(bar) < MAX_SLOTS:
        bar += [EMO_EMPTY] * (MAX_SLOTS - len(bar))
    return "".join(bar)

class StoneState:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.p = P_MAX
        self.inc_top_seq: List[bool] = []
        self.inc_bot_seq: List[bool] = []
        self.dec_seq: List[bool] = []
        self.finished = False

    def line_full(self, line: str) -> bool:
        seq = getattr(self, f"{line}_seq")
        return len(seq) >= MAX_SLOTS

    def all_full(self) -> bool:
        return self.line_full("inc_top") and self.line_full("inc_bot") and self.line_full("dec")

    def try_cut(self, line: str, force_success: Optional[bool] = None) -> bool:
        if self.finished or self.line_full(line):
            return False
        import random
        success = (random.randint(1, 100) <= self.p) if force_success is None else bool(force_success)
        seq = getattr(self, f"{line}_seq")
        seq.append(success)
        if success:
            self.p = clamp(self.p - P_STEP, P_MIN, P_MAX)
        else:
            self.p = clamp(self.p + P_STEP, P_MIN, P_MAX)
        if self.all_full():
            self.finished = True
        return success

    def snapshot(self) -> Dict[str, object]:
        t_s, t_f = _counts(self.inc_top_seq)
        b_s, b_f = _counts(self.inc_bot_seq)
        d_s, d_f = _counts(self.dec_seq)
        return {"p": self.p, "inc_top": (t_s, t_f), "inc_bot": (b_s, b_f), "dec": (d_s, d_f), "finished": self.finished}

@lru_cache(maxsize=None)
def _dp_with_limit(p: int, t_s: int, t_f: int, b_s: int, b_f: int, d_s: int, d_f: int, a: int, b: int, d: int) -> float:
    if t_s + t_f >= MAX_SLOTS and b_s + b_f >= MAX_SLOTS and d_s + d_f >= MAX_SLOTS:
        return 1.0 if (t_s >= a and b_s >= b and d_s <= d) else 0.0
    if t_s + (MAX_SLOTS - (t_s + t_f)) < a or b_s + (MAX_SLOTS - (b_s + b_f)) < b or d_s > d:
        return 0.0
    ps = p / 100.0
    pf = 1.0 - ps
    best = 0.0
    if t_s + t_f < MAX_SLOTS:
        val = ps * _dp_with_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s + 1, t_f, b_s, b_f, d_s, d_f, a, b, d) + \
              pf * _dp_with_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f + 1, b_s, b_f, d_s, d_f, a, b, d)
        best = max(best, val)
    if b_s + b_f < MAX_SLOTS:
        val = ps * _dp_with_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s + 1, b_f, d_s, d_f, a, b, d) + \
              pf * _dp_with_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f + 1, d_s, d_f, a, b, d)
        best = max(best, val)
    if d_s + d_f < MAX_SLOTS:
        val = ps * _dp_with_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s + 1, d_f, a, b, d) + \
              pf * _dp_with_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s, d_f + 1, a, b, d)
        best = max(best, val)
    return best

@lru_cache(maxsize=None)
def _dp_no_limit(p: int, t_s: int, t_f: int, b_s: int, b_f: int, d_s: int, d_f: int, a: int, b: int) -> float:
    if t_s + t_f >= MAX_SLOTS and b_s + b_f >= MAX_SLOTS and d_s + d_f >= MAX_SLOTS:
        return 1.0 if (t_s >= a and b_s >= b) else 0.0
    ps = p / 100.0
    pf = 1.0 - ps
    best = 0.0
    if t_s + t_f < MAX_SLOTS:
        val = ps * _dp_no_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s + 1, t_f, b_s, b_f, d_s, d_f, a, b) + \
              pf * _dp_no_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f + 1, b_s, b_f, d_s, d_f, a, b)
        best = max(best, val)
    if b_s + b_f < MAX_SLOTS:
        val = ps * _dp_no_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s + 1, b_f, d_s, d_f, a, b) + \
              pf * _dp_no_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f + 1, d_s, d_f, a, b)
        best = max(best, val)
    if d_s + d_f < MAX_SLOTS:
        val = ps * _dp_no_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s + 1, d_f, a, b) + \
              pf * _dp_no_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s, d_f + 1, a, b)
        best = max(best, val)
    return best

def choose_best_action(state: StoneState, target: Tuple[int, int]) -> Tuple[str, float]:
    a, b = target
    dec_limit = DEC_LIMITS.get((a, b))
    p = state.p
    t_s, t_f = _counts(state.inc_top_seq)
    b_s, b_f = _counts(state.inc_bot_seq)
    d_s, d_f = _counts(state.dec_seq)
    best_act: Optional[str] = None
    best_val: float = -1.0
    for cand in ("inc_top", "inc_bot", "dec"):
        if state.line_full(cand):
            continue
        if dec_limit is not None:
            if cand == "inc_top":
                val = (p/100) * _dp_with_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s + 1, t_f, b_s, b_f, d_s, d_f, a, b, dec_limit) + \
                      (1 - p/100) * _dp_with_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f + 1, b_s, b_f, d_s, d_f, a, b, dec_limit)
            elif cand == "inc_bot":
                val = (p/100) * _dp_with_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s + 1, b_f, d_s, d_f, a, b, dec_limit) + \
                      (1 - p/100) * _dp_with_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f + 1, d_s, d_f, a, b, dec_limit)
            else:
                val = (p/100) * _dp_with_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s + 1, d_f, a, b, dec_limit) + \
                      (1 - p/100) * _dp_with_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s, d_f + 1, a, b, dec_limit)
        else:
            if cand == "inc_top":
                val = (p/100) * _dp_no_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s + 1, t_f, b_s, b_f, d_s, d_f, a, b) + \
                      (1 - p/100) * _dp_no_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f + 1, b_s, b_f, d_s, d_f, a, b)
            elif cand == "inc_bot":
                val = (p/100) * _dp_no_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s + 1, b_f, d_s, d_f, a, b) + \
                      (1 - p/100) * _dp_no_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f + 1, d_s, d_f, a, b)
            else:
                val = (p/100) * _dp_no_limit(clamp(p - P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s + 1, d_f, a, b) + \
                      (1 - p/100) * _dp_no_limit(clamp(p + P_STEP, P_MIN, P_MAX), t_s, t_f, b_s, b_f, d_s, d_f + 1, a, b)
        if val > best_val:
            best_val = val
            best_act = cand
    return (best_act or "inc_top", best_val)

def build_embed(state: StoneState, author: discord.Member, *, mode: Literal["play", "adv"] = "play", last_action: Optional[str] = None, target: Optional[Tuple[int, int]] = None) -> discord.Embed:
    snap = state.snapshot()
    t_s, t_f = snap["inc_top"]; b_s, b_f = snap["inc_bot"]; d_s, d_f = snap["dec"]
    p = snap["p"]
    title = "어빌리티 스톤 세공" if mode == "play" else f"{EMO_ROBOT} 어빌리티 스톤 세공 추천"
    color = COLOR_DONE if snap["finished"] else (COLOR_INFO if mode == "adv" else COLOR_PRIMARY)
    embed = discord.Embed(title=title, color=color, timestamp=datetime.utcnow())
    desc = f"진행자: {author.mention}\n현재 성공 확률 **{p}%**"
    if mode == "adv" and target:
        desc += f" · 목표 **{target[0]}/{target[1]}**"
    if last_action:
        desc += f"\n최근 결과: {last_action}"
    embed.description = desc
    embed.add_field(name=f"증가(위) {t_s}/{MAX_SLOTS}", value=fmt_line_seq(state.inc_top_seq, "blue"), inline=False)
    embed.add_field(name=f"증가(아래) {b_s}/{MAX_SLOTS}", value=fmt_line_seq(state.inc_bot_seq, "blue"), inline=False)
    embed.add_field(name=f"감소 {d_s}/{MAX_SLOTS}", value=fmt_line_seq(state.dec_seq, "red"), inline=False)
    footer = "버튼을 눌러 세공을 진행해주세요." if not snap["finished"] else "세공이 완료되었습니다."
    embed.set_footer(text=footer)
    return embed

def build_advice_embed(state: StoneState, author: discord.Member, target: Tuple[int, int], last_action: Optional[str] = None, hint: Optional[str] = None) -> discord.Embed:
    action, prob = choose_best_action(state, target)
    embed = build_embed(state, author, mode="adv", last_action=last_action, target=target)
    pretty_map = {"inc_top": "증가(위)", "inc_bot": "증가(아래)", "dec": "감소"}
    rec = pretty_map[action]; prob_pct = f"{prob * 100:.2f}%"
    body = f"다음 추천: **{rec}** {EMO_NEXT} 성공 확률 **{prob_pct}**"
    if hint:
        body += f"\n{hint}"
    embed.add_field(name="추천", value=body, inline=False)
    return embed

class StoneView(discord.ui.View):
    def __init__(self, author: discord.Member, state: StoneState) -> None:
        super().__init__(timeout=15 * 60)
        self.author = author
        self.state = state

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.state.user_id:
            await itx.response.send_message(f"{EMO_WARN} 이 버튼은 {self.author.mention}님만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    def _refresh(self) -> None:
        self.btn_inc_top.disabled = self.state.line_full("inc_top") or self.state.finished
        self.btn_inc_bot.disabled = self.state.line_full("inc_bot") or self.state.finished
        self.btn_dec.disabled = self.state.line_full("dec") or self.state.finished
        self.btn_finish.disabled = self.state.finished

    @discord.ui.button(label="증가(위) 깎기", style=discord.ButtonStyle.primary, emoji=EMO_HAMMER, row=0)
    async def btn_inc_top(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        ok = self.state.try_cut("inc_top")
        self._refresh()
        txt = "증가(위) 성공" if ok else "증가(위) 실패"
        await itx.response.edit_message(embed=build_embed(self.state, self.author, mode="play", last_action=txt), view=self)

    @discord.ui.button(label="증가(아래) 깎기", style=discord.ButtonStyle.primary, emoji=EMO_HAMMER, row=0)
    async def btn_inc_bot(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        ok = self.state.try_cut("inc_bot")
        self._refresh()
        txt = "증가(아래) 성공" if ok else "증가(아래) 실패"
        await itx.response.edit_message(embed=build_embed(self.state, self.author, mode="play", last_action=txt), view=self)

    @discord.ui.button(label="감소 깎기", style=discord.ButtonStyle.danger, emoji=EMO_HAMMER, row=1)
    async def btn_dec(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        ok = self.state.try_cut("dec")
        self._refresh()
        txt = "감소 성공" if ok else "감소 실패"
        await itx.response.edit_message(embed=build_embed(self.state, self.author, mode="play", last_action=txt), view=self)

    @discord.ui.button(label="종료", style=discord.ButtonStyle.secondary, emoji=EMO_STOP, row=1)
    async def btn_finish(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self.state.finished = True
        self._refresh()
        await itx.response.edit_message(embed=build_embed(self.state, self.author, mode="play", last_action="세공 종료"), view=self)

# ======= 선택 단계 ↔ 결과 단계 분리 =======
class AdvisorPickView(discord.ui.View):
    def __init__(self, author: discord.Member, state: StoneState, target: Tuple[int, int]) -> None:
        super().__init__(timeout=20 * 60)
        self.author = author
        self.state = state
        self.target = target

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.state.user_id:
            await itx.response.send_message(f"{EMO_WARN} 이 버튼은 {self.author.mention}님만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    def _refresh(self) -> None:
        self.btn_pick_top.disabled = self.state.line_full("inc_top") or self.state.finished
        self.btn_pick_bot.disabled = self.state.line_full("inc_bot") or self.state.finished
        self.btn_pick_dec.disabled = self.state.line_full("dec") or self.state.finished
        self.btn_done.disabled = self.state.finished

    async def _to_result(self, itx: discord.Interaction, chosen: str, hint: str) -> None:
        view = AdvisorResultView(self.author, self.state, self.target, chosen)
        view._refresh()
        pretty = {"inc_top": "증가(위)", "inc_bot": "증가(아래)", "dec": "감소"}[chosen]
        embed = build_advice_embed(self.state, self.author, self.target, last_action=f"‘{pretty}’ 선택", hint=hint)
        await itx.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="증가(위) 선택", style=discord.ButtonStyle.secondary, emoji=EMO_HAMMER, row=0)
    async def btn_pick_top(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self._refresh()
        await self._to_result(itx, "inc_top", "이제 결과(성공/실패)를 눌러 반영하세요.")

    @discord.ui.button(label="증가(아래) 선택", style=discord.ButtonStyle.secondary, emoji=EMO_HAMMER, row=0)
    async def btn_pick_bot(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self._refresh()
        await self._to_result(itx, "inc_bot", "이제 결과(성공/실패)를 눌러 반영하세요.")

    @discord.ui.button(label="감소 선택", style=discord.ButtonStyle.secondary, emoji=EMO_HAMMER, row=0)
    async def btn_pick_dec(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self._refresh()
        await self._to_result(itx, "dec", "이제 결과(성공/실패)를 눌러 반영하세요.")

    @discord.ui.button(label="종료", style=discord.ButtonStyle.secondary, emoji=EMO_STOP, row=1)
    async def btn_done(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self.state.finished = True
        self._refresh()
        await itx.response.edit_message(embed=build_advice_embed(self.state, self.author, self.target, last_action="세공 종료"), view=self)

class AdvisorResultView(discord.ui.View):
    def __init__(self, author: discord.Member, state: StoneState, target: Tuple[int, int], chosen: str) -> None:
        super().__init__(timeout=20 * 60)
        self.author = author
        self.state = state
        self.target = target
        self.chosen = chosen  # 이번에 반영할 라인

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.state.user_id:
            await itx.response.send_message(f"{EMO_WARN} 이 버튼은 {self.author.mention}님만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    def _refresh(self) -> None:
        done = self.state.finished
        self.btn_success.disabled = done
        self.btn_fail.disabled = done
        self.btn_back.disabled = done
        self.btn_done.disabled = done

    async def _back_to_pick(self, itx: discord.Interaction, last_action: Optional[str] = None) -> None:
        view = AdvisorPickView(self.author, self.state, self.target)
        view._refresh()
        embed = build_advice_embed(self.state, self.author, self.target, last_action=last_action, hint="다음에 깎을 라인을 선택하세요.")
        await itx.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="성공 반영", style=discord.ButtonStyle.success, emoji=EMO_SUCCESS, row=0)
    async def btn_success(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self.state.try_cut(self.chosen, force_success=True)
        pretty = {"inc_top": "증가(위)", "inc_bot": "증가(아래)", "dec": "감소"}[self.chosen]
        self._refresh()
        await self._back_to_pick(itx, last_action=f"{pretty} 성공")

    @discord.ui.button(label="실패 반영", style=discord.ButtonStyle.danger, emoji=EMO_FAIL_BTN, row=0)
    async def btn_fail(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self.state.try_cut(self.chosen, force_success=False)
        pretty = {"inc_top": "증가(위)", "inc_bot": "증가(아래)", "dec": "감소"}[self.chosen]
        self._refresh()
        await self._back_to_pick(itx, last_action=f"{pretty} 실패")

    @discord.ui.button(label="라인 다시 선택", style=discord.ButtonStyle.secondary, emoji=EMO_NEXT, row=0)
    async def btn_back(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self._refresh()
        await self._back_to_pick(itx, last_action="라인 재선택")

    @discord.ui.button(label="종료", style=discord.ButtonStyle.secondary, emoji=EMO_STOP, row=1)
    async def btn_done(self, _b: discord.ui.Button, itx: discord.Interaction) -> None:
        self.state.finished = True
        self._refresh()
        await itx.response.edit_message(embed=build_advice_embed(self.state, self.author, self.target, last_action="세공 종료"), view=self)

class StoneCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.slash_command(name="세공", description="로스트아크 어빌리티 스톤 세공 — 직접 진행")
    async def stone(self, ctx: discord.ApplicationContext) -> None:
        author = getattr(ctx, "user", ctx.author)
        state = StoneState(user_id=author.id)
        view = StoneView(author=author, state=state)
        view._refresh()
        await ctx.respond(embed=build_embed(state, author, mode="play"), view=view)

    @commands.slash_command(name="세공추천", description="목표(7/7, 9/7, 10/6) 최적 추천 → 결과만 반영")
    @discord.option("옵션", description="목표(순서 무시)", input_type=str, choices=["7/7", "9/7", "10/6"])
    async def stone_advice(self, ctx: discord.ApplicationContext, 옵션: str) -> None:
        author = getattr(ctx, "user", ctx.author)
        a, b = map(int, 옵션.split("/"))
        target: Tuple[int, int] = (a, b)
        state = StoneState(user_id=author.id)
        view = AdvisorPickView(author=author, state=state, target=target)  # ← 분리된 1단계 뷰
        view._refresh()
        embed = build_advice_embed(state, author, target, hint="깎을 라인을 먼저 선택하세요.")
        await ctx.respond(embed=embed, view=view)

def setup(bot: commands.Bot) -> None:
    bot.add_cog(StoneCog(bot))
