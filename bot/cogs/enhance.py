from __future__ import annotations

import discord
from discord.ext import commands
from core.http_client import http_client
from typing import Optional, Tuple, Dict, List
from datetime import datetime
import time

COLOR_PRIMARY = discord.Color.gold()
COLOR_OK = discord.Color.green()
COLOR_WARN = discord.Color.orange()
COLOR_ERR = discord.Color.red()
COLOR_INFO = discord.Color.blurple()
COLOR_DUEL = discord.Color.purple()

EMO_HAMMER = "⚒️"
EMO_DIAMOND = "💎"
EMO_SPARK = "✨"
EMO_FIRE = "🔥"
EMO_DOWN = "🔻"
EMO_SHIELD = "🛡️"
EMO_HOURGLASS = "⏳"
EMO_TROPHY = "🏆"
EMO_CRYSTAL_BALL = "🔮"
EMO_SCROLL = "📜"
EMO_LOCK = "🔒"
EMO_BADGE_PLUS = "🟣"
EMO_TOKEN = "🧿"
EMO_SWORDS = "⚔️"
EMO_DICE = "🎲"
EMO_WARN = "⚠️"  # 선대 최초 진입 경고 등

def _fmt_secs(secs: int) -> str:
    secs = max(0, int(secs))
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}시간 {m}분 {s}초"
    if m:
        return f"{m}분 {s}초"
    return f"{s}초"

def _unix_after(secs: int) -> int:
    try:
        return int(time.time()) + max(0, int(secs))
    except Exception:
        return int(time.time())

def _fmt_cooldown_ts(secs: int) -> str:
    ts = _unix_after(secs)
    return f"{EMO_HOURGLASS} <t:{ts}:R> (<t:{ts}:T>)"

def _fmt_rates(r: dict | None) -> str | None:
    if not r:
        return None
    try:
        s = r.get("success"); d = r.get("destroy"); dn = r.get("down"); f = r.get("fail")
        parts = []
        if isinstance(s, (int, float)): parts.append(f"성공 **{s:.1f}%**")
        if isinstance(d, (int, float)): parts.append(f"파괴 **{d:.1f}%**")
        if isinstance(dn, (int, float)): parts.append(f"하락 **{dn:.1f}%**")
        if isinstance(f, (int, float)): parts.append(f"실패 **{f:.1f}%**")
        return " · ".join(parts) if parts else None
    except Exception:
        return None

def _level_icon_url(level: int) -> str:
    if level >= 26:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_10_81.png"
    if level >= 25:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_105.png"
    if level >= 23:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_104.png"
    if level >= 20:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_103.png"
    if level >= 17:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_102.png"
    if level >= 14:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_101.png"
    if level >= 11:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_100.png"
    if level >= 9:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_99.png"
    if level >= 5:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_98.png"
    if level >= 3:
        return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_97.png"
    return "https://cdn-lostark.game.onstove.com/efui_iconatlas/use/use_12_96.png"

def _set_author_with_pity(embed: discord.Embed, level_label: str, level: int, pity_pct: float) -> None:
    try:
        embed.set_author(name=f"{level_label} · 장기백 {pity_pct:.0f}%", icon_url=_level_icon_url(int(level)))
    except Exception:
        pass

def _outcome_color(outcome: str) -> discord.Color:
    return {
        "success": COLOR_OK, "pity_forced": COLOR_OK, "fail": COLOR_WARN, "down": COLOR_WARN,
        "destroy": COLOR_ERR, "already_max": COLOR_PRIMARY, "destroy_prevented": COLOR_INFO,
        "down_prevented": COLOR_INFO, "destroy_to_plus1": COLOR_OK, "dice_success": COLOR_OK, "dice_down": COLOR_WARN,
    }.get(outcome, COLOR_PRIMARY)

def _outcome_title(outcome: str) -> str:
    return {
        "success": "✨ 강화 성공!", "pity_forced": "💎 장기백 확정 성공!", "fail": "🛡️ 강화 실패", "down": "🔻 등급 하락",
        "destroy": "🔥 장비 파괴", "already_max": "💎 최종 단계", "destroy_prevented": "🛡️ 파괴 무효!",
        "down_prevented": "🛡️ 하락 무효!", "destroy_to_plus1": "🧿 파괴 뒤집기: +1강!", "dice_success": "🎲 주사위: 성공!",
        "dice_down": "🎲 주사위: 하락",
    }.get(outcome, "결과")

_GAHO_NAME_PREFIXES = ("📜 선조의 가호", f"{EMO_BADGE_PLUS} 강화된 선조의 가호")
_AUTO_GAHO_NAME_PREFIXES = ("⚡ 자동 발동", "⚡ 자동 발동 — 선조의 가호", "⚡ Auto Gaho")

def _replace_or_add_field(embed: discord.Embed, *, name: str, value: str, inline: bool = False,
                          prefixes: tuple[str, ...] = _GAHO_NAME_PREFIXES) -> None:
    try:
        fields = list(embed.fields) if hasattr(embed, "fields") else []
        idx: Optional[int] = None
        for i, f in enumerate(fields):
            if any(str(f.name).startswith(pfx) for pfx in prefixes):
                idx = i; break
        if idx is not None: embed.set_field_at(idx, name=name, value=value, inline=inline)
        else: embed.add_field(name=name, value=value, inline=inline)
    except Exception:
        embed.add_field(name=name, value=value, inline=inline)

def _is_duel(resp: dict) -> bool:
    tokens = (resp or {}).get("tokens") or {}
    return int(tokens.get("three_runs_left") or 0) > 0

def _has_silian(esthers: List[str] | None) -> bool:
    try:
        return any(str(x).strip() == "실리안" for x in (esthers or []))
    except Exception:
        return False

def _apply_duel_skin(embed: discord.Embed, resp: dict) -> discord.Embed:
    try:
        if not _is_duel(resp): return embed
        tokens = (resp or {}).get("tokens") or {}; state = (resp or {}).get("state") or {}
        duel_left = int(tokens.get("three_runs_left") or 0)
        esthers_bound = (resp or {}).get("esthers") or []
        silian_on = _has_silian(esthers_bound)
        embed.title = f"{EMO_SWORDS} 강화 일기토 — {duel_left}회 남음"
        embed.color = COLOR_DUEL
        lvl_label = state.get("level_label") or f"{state.get('level','?')}강"
        pity = float(state.get("pity") or 0)
        _set_author_with_pity(embed, lvl_label, int(state.get("level") or 1), pity)
        rule_line = (
            f"{EMO_CRYSTAL_BALL} **일기토 규칙:** 성공 80% / 하락 20% · 실패/파괴 0% · 쿨타임 무시"
            if silian_on else
            f"{EMO_CRYSTAL_BALL} **일기토 규칙:** 성공 50% / 하락 50% · 실패/파괴 0% · 쿨타임 무시"
        )
        cta_line = "`/강화` 명령어를 연속 사용하여, 일기토에 도전해보세요!"
        note_line = (
            "⚔️ **실리안의 힘으로 확률이 `성공 80% / 하락 20%` 으로 변경되었어요.** "
            "**3연속 모두 성공 시 추가로 `+1강`을 획득합니다.**"
            if silian_on else ""
        )
        desc = (embed.description or "").strip()
        parts: List[str] = [rule_line, cta_line]
        if note_line: parts.append(note_line)
        if desc: parts.append(desc)
        embed.description = "\n\n".join(parts)
        embed.set_footer(text=f"장기백 {pity:.1f}% • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return embed
    except Exception:
        return embed

def _resolve_gaho_cap(gaho: dict | None, state: dict | None, data: dict | None, upgrade_flag: bool | None = None) -> int:
    g = gaho or {}
    if isinstance(g.get("cap"), int): return max(1, int(g["cap"]))
    if isinstance(g.get("stack_cap"), int): return max(1, int(g["stack_cap"]))
    st = state or {}
    if isinstance(st.get("gaho_cap"), int): return max(1, int(st["gaho_cap"]))
    d = data or {}
    if isinstance(d.get("gaho_cap"), int): return max(1, int(d["gaho_cap"]))
    if upgrade_flag is None:
        upgrade_flag = bool(g.get("upgrade_pending") or g.get("upgrade_mode"))
    return 5 if upgrade_flag else 6

def _gaho_stack_text(gaho: dict | None, state: dict | None = None, data: dict | None = None) -> str:
    g = gaho or {}
    upgrade = bool(g.get("upgrade_pending") or g.get("upgrade_mode"))
    cap = _resolve_gaho_cap(g, state, data, upgrade_flag=upgrade)
    ready = bool(g.get("ready"))
    cnt = int(g.get("count") or 0)
    cur = cap if ready else max(0, min(cap, cnt))
    return f"{cur}/{cap}"

def _gaho_stack_text_from_count(cnt: int, cap: int) -> str:
    try: c = int(cnt)
    except Exception: c = 0
    return f"{max(0, min(cap, c))}/{cap}"

def _apply_or_clear_cooldown_field(embed: discord.Embed, cooldown_sec: int, tokens: dict | None):
    duel_now = int((tokens or {}).get("three_runs_left") or 0) > 0
    if cooldown_sec > 0 and not duel_now:
        _replace_or_add_field(embed, name="쿨타임", value=_fmt_cooldown_ts(cooldown_sec), inline=False, prefixes=("쿨타임",))
    else:
        try:
            fields = list(embed.fields); embed.clear_fields()
            for f in fields:
                if str(f.name) == "쿨타임": continue
                embed.add_field(name=f.name, value=f.value, inline=f.inline)
        except Exception:
            pass

def _fmt_esther_badges(lst: List[str] | None) -> Optional[str]:
    if not lst: return None
    pills = [f"`{str(x)}`" for x in lst if str(x).strip()]
    return " · ".join(pills) if pills else None

def _add_esthers_field(embed: discord.Embed, lst: List[str] | None):
    """에스더 결속 필드를 항상 교체 방식으로 갱신(중복 방지)."""
    txt = _fmt_esther_badges(lst)
    if txt:
        _replace_or_add_field(
            embed,
            name="🔗 에스더 결속",
            value=txt,
            inline=False,
            prefixes=("🔗 에스더 결속",)
        )
    else:
        # 결속이 없으면 기존 '에스더 결속' 필드를 제거
        try:
            fields = list(embed.fields)
            embed.clear_fields()
            for f in fields:
                if str(f.name).startswith("🔗 에스더 결속"):  # 해당 필드만 건너뛰고 나머지는 유지
                    continue
                embed.add_field(name=f.name, value=f.value, inline=f.inline)
        except Exception:
            pass

# ---------- NEW: 선대 에스더 필드/뷰 ----------
def _add_ancestral_field(embed: discord.Embed, ancestral_owned: List[str] | None, warning: str | None = None):
    pills = " · ".join(f"`{x}`" for x in (ancestral_owned or []))
    if not pills and not warning:
        return
    value = pills or "—"
    if warning:
        value += f"\n**{EMO_WARN} {warning}**"
    _replace_or_add_field(embed, name="🔮 선대 에스더의 가호", value=value, inline=False,
                          prefixes=("🔮 선대 에스더의 가호",))

class AncestralSelectView(discord.ui.View):
    """선대 에스더의 가호 선택 버튼 묶음."""
    def __init__(self, guild_id: int, user_id: int, invoker_id: int, entries: List[Tuple[str, str]]):
        super().__init__(timeout=None)
        self.guild_id = guild_id; self.user_id = user_id; self.invoker_id = invoker_id
        for label, key in entries:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, emoji=EMO_CRYSTAL_BALL)
            async def on_click(interaction: discord.Interaction, _key=key, _label=label):
                if interaction.user.id != self.invoker_id:
                    await interaction.response.send_message(f"{EMO_LOCK} 이 버튼은 명령어 실행자만 사용 가능해요.", ephemeral=True)
                    return
                try:
                    resp = await http_client.post(
                        "/enhance/ancestral/select",
                        json={"guild_id": self.guild_id, "user_id": self.user_id, "key": _key},
                        timeout=10.0,
                    )
                except Exception as e:
                    await interaction.response.send_message(f"❌ 선대 선택 실패\n```{e}```", ephemeral=True); return
                if not resp or resp.status_code != 200:
                    await interaction.response.send_message("❌ 선대 선택 실패", ephemeral=True); return
                data = resp.json() or {}
                # 상태 재조회
                try:
                    st_resp = await http_client.get("/enhance/state", params={"guild_id": self.guild_id, "user_id": self.user_id}, timeout=10.0)
                    st = st_resp.json() if st_resp and st_resp.status_code == 200 else {}
                except Exception:
                    st = {}
                embed = discord.Embed(title=f"🔮 선대 에스더의 가호 활성화 — {data.get('selected', _label)}", color=COLOR_OK)
                level_label = st.get("level_label","?강"); pity = float(st.get("pity") or 0.0)
                _set_author_with_pity(embed, level_label, int(st.get("level") or 1), pity)
                anc = (st.get("ancestral") or {})
                _add_ancestral_field(embed, anc.get("owned"), data.get("warning"))
                _add_esthers_field(embed, st.get("esthers"))
                try: embed.set_thumbnail(url=interaction.user.display_avatar.url)
                except Exception: pass
                embed.set_footer(text=f"{interaction.user.display_name} • MococoBot")
                await interaction.response.edit_message(embed=embed, view=None)
            btn.callback = on_click  # type: ignore
            self.add_item(btn)
# ---------- /NEW ----------

def _format_gaho_field(upgrade_mode: bool, eff: str, level_label: str, stack_txt: str, shield: int, down_shield: int, eff_type: str | None = None, is_dice: bool = False) -> Tuple[str, str]:
    title = f"{EMO_BADGE_PLUS} 강화된 선조의 가호 — 결과" if upgrade_mode else f"{EMO_SCROLL} 선조의 가호 — 결과"
    header = f"**{EMO_BADGE_PLUS} 강화된 가호 효과 적용!**\n" if upgrade_mode else ""
    body = f"- 효과: **{eff}**\n- 현재: **{level_label}**\n- 가호 스택: `{stack_txt}`\n- 파괴 방지권: `x{shield}` · 하락 방지권: `x{down_shield}`"
    value = header + body
    if is_dice:
        value += "\n\n- 아래 **주사위 굴리기** 버튼을 눌러 결과를 확정하세요. (성공/하락 50%)"
    return title, value

def _format_auto_gaho_field(upgrade_mode: bool, eff: str, level_label: str, stack_txt: str, shield: int, down_shield: int, is_dice: bool = False) -> Tuple[str, str]:
    """바훈투르 자동 가호가 발동했을 때 전용 표시."""
    if upgrade_mode:
        name = f"⚡ 자동 발동 — {EMO_BADGE_PLUS} 강화된 선조의 가호"
        head = f"**바훈투르 효과!** 강화된 가호가 자동으로 발동했습니다.\n"
    else:
        name = "⚡ 자동 발동 — 선조의 가호"
        head = f"**바훈투르 효과!** 선조의 가호가 자동으로 발동했습니다.\n"
    body = (
        f"- 효과: **{eff}**\n"
        f"- 현재: **{level_label}**\n"
        f"- 가호 스택: `{stack_txt}` (자동 발동은 스택/강화모드 **소비 없음**)\n"
        f"- 파괴 방지권: `x{shield}` · 하락 방지권: `x{down_shield}`"
    )
    if is_dice:
        body += "\n\n- 아래 **주사위 굴리기** 버튼으로 결과를 확정하세요. (성공/하락 50%)"
    return name, head + body

class GahoDecisionView(discord.ui.View):
    def __init__(self, cog: "EnhanceCog", guild_id: int, user_id: int, invoker_id: int):
        super().__init__(timeout=None)
        self.cog = cog; self.guild_id = guild_id; self.user_id = user_id; self.invoker_id = invoker_id
    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(f"{EMO_LOCK} 이 버튼은 명령어 실행자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True
    async def _clear_pending(self, message: Optional[discord.Message]):
        self.cog._pending_gaho.pop((self.guild_id, self.user_id), None)
        if message:
            try: await message.edit(view=None)
            except Exception: pass
    @discord.ui.button(label="뽑기", style=discord.ButtonStyle.success, emoji=EMO_DICE)
    async def draw(self, button: discord.ui.Button, interaction: discord.Interaction):  # type: ignore[override]
        if not await self._check_user(interaction): return
        try:
            resp = await http_client.post(
                "/enhance/gaho/draw",
                json={"guild_id": self.guild_id, "user_id": self.user_id, "username": interaction.user.display_name},
                timeout=10.0,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ 가호 뽑기 실패\n```{e}```", ephemeral=True); return
        if not resp or resp.status_code != 200:
            await interaction.response.send_message("❌ 가호 뽑기 실패", ephemeral=True)
            await self._clear_pending(interaction.message); return
        data = resp.json() or {}
        eff_obj = data.get("effect") or {}
        eff_type = eff_obj.get("type"); eff = eff_obj.get("desc", "효과 적용")
        upgrade_mode = bool(eff_obj.get("upgrade_mode") or False)
        st = data.get("state") or {}
        level_label = st.get("level_label", "?강")
        cur_level = int(st.get("level") or 1)
        pity_pct = float(st.get("pity") or 0.0)
        cd = int(st.get("cooldown_remain_sec") or 0)
        shield = int(st.get("gaho_shield") or st.get("shield") or 0)
        down_shield = int(st.get("gaho_down_shield") or st.get("down_shield") or 0)
        gaho_count = int(st.get("gaho_count") or 0)
        tokens = data.get("tokens") or {}
        duel_now = int(tokens.get("three_runs_left") or 0) > 0
        esthers_bound = data.get("esthers") or []
        cap = _resolve_gaho_cap(data.get("gaho") or {}, st, data, upgrade_flag=upgrade_mode)
        stack_txt = _gaho_stack_text_from_count(gaho_count, cap=cap)
        try: base_embed = interaction.message.embeds[0]
        except Exception: base_embed = discord.Embed(title="📜 선조의 가호", color=COLOR_PRIMARY)
        if upgrade_mode: base_embed.color = COLOR_INFO
        _set_author_with_pity(base_embed, level_label, cur_level, pity_pct)
        title, gaho_value = _format_gaho_field(upgrade_mode, eff, level_label, stack_txt, shield, down_shield)
        _replace_or_add_field(base_embed, name=title, value=gaho_value, inline=False,
                              prefixes=("📜 선조의 가호", f"{EMO_BADGE_PLUS} 강화된 선조의 가호"))
        _add_esthers_field(base_embed, esthers_bound)
        _apply_or_clear_cooldown_field(base_embed, cd, tokens)
        if duel_now:
            try: base_embed.clear_fields()
            except Exception: base_embed.fields = []
            base_embed.title = f"{EMO_SWORDS} 강화 일기토 — {int(tokens.get('three_runs_left') or 0)}회 남음"
            base_embed.color = COLOR_DUEL
            _set_author_with_pity(base_embed, level_label, cur_level, pity_pct)
            silian_on = _has_silian(esthers_bound)
            rule_line = (
                f"{EMO_CRYSTAL_BALL} **일기토 규칙:** 성공 80% / 하락 20% · 실패/파괴 0% · 쿨타임 무시"
                if silian_on else
                f"{EMO_CRYSTAL_BALL} **일기토 규칙:** 성공 50% / 하락 50% · 실패/파괴 0% · 쿨타임 무시"
            )
            cta_line = "`/강화` 명령어를 연속 사용하여, 일기토에 도전해보세요!"
            note_line = (
                "⚔️ **실리안의 힘으로 확률이 `성공 80% / 하락 20%` 으로 변경되었어요.** "
                "**3연속 모두 성공 시 추가로 `+1강`을 획득합니다.**"
                if silian_on else ""
            )
            parts = [rule_line, cta_line]
            if note_line: parts.append(note_line)
            base_embed.description = "\n\n".join(parts)
            base_embed.set_footer(text=f"장기백 {pity_pct:.1f}% • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            await interaction.response.edit_message(embed=base_embed, view=None)
            await self._clear_pending(None); return
        if eff_type == "dice":
            title, gaho_value = _format_gaho_field(upgrade_mode, eff, level_label, stack_txt, shield, down_shield, eff_type, True)
            _replace_or_add_field(base_embed, name=title, value=gaho_value, inline=False,
                                  prefixes=("📜 선조의 가호", f"{EMO_BADGE_PLUS} 강화된 선조의 가호"))
            view = DiceRollView(self.cog, self.guild_id, self.user_id, self.invoker_id)
            await interaction.response.edit_message(embed=base_embed, view=view)
        else:
            await interaction.response.edit_message(embed=base_embed, view=None)
            await self._clear_pending(None)
    @discord.ui.button(label="넘기기", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, button: discord.ui.Button, interaction: discord.Interaction):  # type: ignore[override]
        if not await self._check_user(interaction): return
        try:
            resp = await http_client.post(
                "/enhance/gaho/skip",
                json={"guild_id": self.guild_id, "user_id": self.user_id, "username": interaction.user.display_name},
                timeout=10.0,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ 가호 넘기기 실패\n```{e}```", ephemeral=True); return
        if not resp or resp.status_code != 200:
            await interaction.response.send_message("❌ 가호 넘기기 실패", ephemeral=True)
            await self._clear_pending(interaction.message); return
        esther_list: List[str] = []
        cap = 6
        try:
            st_resp = await http_client.get("/enhance/state", params={"guild_id": self.guild_id, "user_id": self.user_id}, timeout=10.0)
            if st_resp and st_resp.status_code == 200:
                st = st_resp.json() or {}
                gaho = st.get("gaho") or {}
                cap = _resolve_gaho_cap(gaho, st, st)
                esther_list = st.get("esthers") or []
        except Exception:
            pass
        try: base_embed = interaction.message.embeds[0]
        except Exception: base_embed = discord.Embed(title="📜 선조의 가호", color=COLOR_PRIMARY)
        _replace_or_add_field(base_embed, name="📜 선조의 가호", value=f"**넘김 처리** 되었습니다.\n- 가호 스택: `0/{cap}`", inline=False, prefixes=("📜 선조의 가호", f"{EMO_BADGE_PLUS} 강화된 선조의 가호"))
        _add_esthers_field(base_embed, esther_list)
        await interaction.response.edit_message(embed=base_embed, view=None)
        await self._clear_pending(None)

class DiceRollView(discord.ui.View):
    def __init__(self, cog: "EnhanceCog", guild_id: int, user_id: int, invoker_id: int):
        super().__init__(timeout=None)
        self.cog = cog; self.guild_id = guild_id; self.user_id = user_id; self.invoker_id = invoker_id
    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(f"{EMO_LOCK} 이 버튼은 명령어 실행자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="주사위 굴리기", style=discord.ButtonStyle.primary, emoji=EMO_DICE)
    async def roll(self, button: discord.ui.Button, interaction: discord.Interaction):  # type: ignore[override]
        if not await self._check_user(interaction): return
        try:
            resp = await http_client.post(
                "/enhance/gaho/dice",
                json={"guild_id": self.guild_id, "user_id": self.user_id, "username": interaction.user.display_name},
                timeout=10.0,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ 주사위 실패\n```{e}```", ephemeral=True); return
        if not resp or resp.status_code != 200:
            await interaction.response.send_message("❌ 주사위 실패", ephemeral=True); return
        data = resp.json() or {}
        outcome = data.get("outcome")
        is_success = outcome in ("success", "dice_success")
        try:
            st_resp = await http_client.get("/enhance/state", params={"guild_id": self.guild_id, "user_id": self.user_id}, timeout=10.0)
            st = st_resp.json() if st_resp and st_resp.status_code == 200 else {}
        except Exception:
            st = {}
        level_label = (st.get("level_label") if st else (data.get("state") or {}).get("level_label")) or "?강"
        cur_level = int((st.get("level") if st else (data.get("state") or {}).get("level") or 1))
        pity_pct = float((st.get("pity") if st else (data.get("state") or {}).get("pity") or 0.0))
        gaho = st.get("gaho") or {}; tokens = st.get("tokens") or {}
        stack_txt = _gaho_stack_text(gaho, st, data)
        shield = int((gaho or {}).get("shield") or 0)
        down_shield = int((gaho or {}).get("down_shield") or 0)
        upgrade_mode = bool((gaho or {}).get("upgrade_pending") or False)
        esther_list = st.get("esthers") or []
        try: base_embed = interaction.message.embeds[0]
        except Exception: base_embed = discord.Embed(color=COLOR_PRIMARY)
        _set_author_with_pity(base_embed, level_label, cur_level, pity_pct)
        title = f"{EMO_BADGE_PLUS} 강화된 선조의 가호 — 결과" if upgrade_mode else f"{EMO_SCROLL} 선조의 가호 — 결과"
        result_line = f"- 주사위 결과: **{'성공' if is_success else '하락'}**"
        gaho_value = (
            (f"**{EMO_BADGE_PLUS} 강화된 가호 효과 적용!**\n" if upgrade_mode else "")
            + result_line + "\n"
            + f"- 현재: **{level_label}**\n"
            + f"- 가호 스택: `{stack_txt}`\n"
            + f"- 파괴 방지권: `x{shield}` · 하락 방지권: `x{down_shield}`"
        )
        _replace_or_add_field(base_embed, name=title, value=gaho_value, inline=False,
                              prefixes=("📜 선조의 가호", f"{EMO_BADGE_PLUS} 강화된 선조의 가호"))
        _add_esthers_field(base_embed, esther_list)
        await interaction.response.edit_message(embed=base_embed, view=None)
        self.cog._pending_gaho.pop((self.guild_id, self.user_id), None)

class EstherBindView(discord.ui.View):
    def __init__(self, cog: "EnhanceCog", guild_id: int, user_id: int, invoker_id: int,
                 entries: List[Tuple[str, str]], tooltips: Dict[str, str] | None = None):
        super().__init__(timeout=None)
        self.cog = cog; self.guild_id = guild_id; self.user_id = user_id; self.invoker_id = invoker_id
        self.tooltips = tooltips or {}
        for label, key in entries:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            async def on_click(interaction: discord.Interaction, _key: str = key, _label: str = label):
                if interaction.user.id != self.invoker_id:
                    await interaction.response.send_message(f"{EMO_LOCK} 이 버튼은 명령어 실행자만 사용할 수 있어요.", ephemeral=True)
                    return
                try:
                    bind_resp = await http_client.post(
                        "/enhance/esther/bind",
                        json={"guild_id": self.guild_id, "user_id": self.user_id, "esther": _key},
                        timeout=10.0,
                    )
                except Exception as e:
                    await interaction.response.send_message(f"❌ 에스더 결속 실패\n```{e}```", ephemeral=True); return
                if not bind_resp or bind_resp.status_code != 200:
                    await interaction.response.send_message("❌ 에스더 결속 실패", ephemeral=True); return
                bind_data = bind_resp.json() or {}
                try:
                    st_resp = await http_client.get("/enhance/state", params={"guild_id": self.guild_id, "user_id": self.user_id}, timeout=10.0)
                    st = st_resp.json() if st_resp and st_resp.status_code == 200 else {}
                except Exception:
                    st = {}
                new_level = int((st.get("level") or 1))
                level_label = st.get("level_label") or f"{new_level}강"
                pity_pct = float(st.get("pity") or 0.0)
                esther_list = st.get("esthers") or bind_data.get("esthers") or []
                available = st.get("available_esthers") or bind_data.get("available_esthers") or []
                gaho = st.get("gaho") or {}; tokens = st.get("tokens") or {}
                embed = discord.Embed(
                    title=f"🔗 에스더 결속 완료 — {bind_data.get('bound_esther', _label)}",
                    description=f"{interaction.user.mention}님이 **{_label}**과 결속했습니다!",
                    color=COLOR_OK,
                )
                _set_author_with_pity(embed, level_label, new_level, pity_pct)
                _add_esthers_field(embed, esther_list)
                if available:
                    embed.add_field(name="결속 가능", value=", ".join(available), inline=False)
                stack_txt = _gaho_stack_text(gaho, st, bind_data)
                shield = int((gaho or {}).get("shield") or 0)
                down_shield = int((gaho or {}).get("down_shield") or 0)
                embed.add_field(name="📜 선조의 가호",
                                value=f"`{stack_txt}` · 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`",
                                inline=False)
                try: embed.set_thumbnail(url=interaction.user.display_avatar.url)
                except Exception: pass
                embed.set_footer(text=f"{interaction.user.display_name} • MococoBot")
                await interaction.response.edit_message(embed=embed, view=None)
                self.cog._pending_gaho.pop((self.guild_id, self.user_id), None)
            button.callback = on_click  # type: ignore
            self.add_item(button)

class EnhanceCog(commands.Cog):
    def __init__(self, bot: commands.AutoShardedBot):
        self.bot = bot
        self._pending_gaho: Dict[Tuple[int, int], int] = {}
        self._duel_before: Dict[Tuple[int, int], int] = {}
    @discord.slash_command(name="강화", description="내 무기 강화를 시도하거나 상태를 확인합니다.")
    @discord.option("내상태확인", description="강화 시도 대신 현재 상태만 확인해요.", type=bool, required=False, default=False)
    async def enhance_main(self, ctx: discord.ApplicationContext, 내상태확인: bool = False):
        guild_id = ctx.guild_id
        user = ctx.user or ctx.author; user_id = user.id; username = user.display_name
        if 내상태확인:
            if not guild_id: return await ctx.respond("❌ 서버 내에서만 사용할 수 있어요.", ephemeral=True)
            try:
                resp = await http_client.get("/enhance/state", params={"guild_id": guild_id, "user_id": user_id}, timeout=10.0)
                if not resp or resp.status_code != 200:
                    return await ctx.respond(f"❌ 상태 조회 실패 (코드 {getattr(resp, 'status_code', None)})", ephemeral=True)
                data = resp.json() or {}
                level_label = data.get("level_label", "?강"); pity_pct = float(data.get("pity") or 0.0)
                cooldown = int(data.get("cooldown_remain_sec") or 0)
                current_rates = data.get("current_rates"); gaho = data.get("gaho") or {}; tokens = data.get("tokens") or {}
                cur_level = int(data.get("level") or 1)
                shield = int((gaho or {}).get("shield") or 0); down_shield = int((gaho or {}).get("down_shield") or 0)
                upgrade_pending = bool((gaho or {}).get("upgrade_pending") or False)
                esthers = data.get("esthers") or []; available = data.get("available_esthers") or []
                embed = discord.Embed(title=f"{EMO_HAMMER} 내 강화 상태", color=COLOR_PRIMARY)
                _set_author_with_pity(embed, level_label, cur_level, pity_pct)
                rt = _fmt_rates(current_rates)
                if rt: embed.add_field(name=f"{EMO_CRYSTAL_BALL} 현재 강화 확률", value=rt, inline=False)
                t3 = int((tokens or {}).get("three_runs_left") or 0)
                if t3 > 0:
                    embed.add_field(name=f"{EMO_TOKEN} 3연속 보호", value=f"**{t3}회 남음** — 시도 시 **쿨타임 없음**, **실패/파괴 0%**", inline=False)
                elif cooldown > 0:
                    embed.add_field(name="쿨타임", value=_fmt_cooldown_ts(cooldown), inline=False)
                stack_txt = _gaho_stack_text(gaho, data, data)
                gaho_title = f"{EMO_BADGE_PLUS} 강화된 선조의 가호" if upgrade_pending else "📜 선조의 가호"
                if gaho.get("ready"):
                    embed.add_field(
                        name=gaho_title,
                        value=(("**강화 모드 활성화!**\n" if upgrade_pending else "")
                               + f"**지금 사용 가능** — `{stack_txt}`\n"
                               + f"- 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`\n"
                               + "`/강화`로 결과 임베드에서 **뽑기/넘기기** 선택"),
                        inline=False,
                    )
                else:
                    embed.add_field(name=gaho_title,
                                    value=f"🧿 `{stack_txt}` · 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`",
                                    inline=False)
                _add_esthers_field(embed, esthers)

                # ---- NEW: 선대 에스더 가호 UI ----
                ancestral = data.get("ancestral") or {}
                _add_ancestral_field(embed, ancestral.get("owned"), ancestral.get("warning"))
                view: Optional[discord.ui.View] = None
                ak = ancestral.get("available_keys") or []
                al = ancestral.get("available") or []
                if ak and al:
                    # 상태조회에서도 노골 멘트 추가
                    embed.add_field(
                        name="선대 에스더 가호 부여",
                        value="**37강 + 모든 에스더 결속 조건 충족!**\n**선대 에스더의 가호 부여가 가능합니다. 아래 버튼으로 선택하세요.**\n- 루테란 : 강화 성공시 50% 확률로 1강이 추가되며, 100% 확률로 선조의 가호가 발동됩니다.\n- 갈라투르 : 일반 선조의 가호가 삭제되고, 강화된 선조의 가호로 변경됩니다.\n- 시엔 : 파괴/하락 발생시 50%확률로 방어하며 장기백 증가량 2배가 됩니다.",
                        inline=False
                    )
                    entries = list(zip(al, ak))
                    view = AncestralSelectView(guild_id, user_id, user_id, entries)
                # ---- /NEW ----

                if available and cur_level >= 37:
                    embed.add_field(name="결속 가능", value=f"{', '.join(available)}\n`/강화` 명령어로 결속하세요.", inline=False)
                try: embed.set_thumbnail(url=user.display_avatar.url)
                except Exception: pass
                embed.set_footer(text="MococoBot")
                return await ctx.respond(embed=embed, view=view, ephemeral=True)
            except Exception as e:
                return await ctx.respond(f"❌ 오류가 발생했어요.\n```{e}```", ephemeral=True)
        if not guild_id: return await ctx.respond("❌ 서버 내에서만 사용할 수 있어요.", ephemeral=True)
        try: await ctx.defer(ephemeral=False)
        except Exception: pass
        pending_key = (guild_id, user_id)
        if pending_key in self._pending_gaho:
            try:
                await http_client.post("/enhance/gaho/skip", json={"guild_id": guild_id, "user_id": user_id, "username": username}, timeout=10.0)
            except Exception:
                pass
        self._pending_gaho.pop(pending_key, None)
        prev_duel_left = self._duel_before.get(pending_key, 0)
        try:
            resp = await http_client.post("/enhance/try", json={"guild_id": guild_id, "user_id": user_id, "username": username}, timeout=10.0)
            if resp is not None and resp.status_code == 429:
                detail: Dict[str, any] = {}
                try: detail = resp.json().get("detail") or {}
                except Exception: pass
                remain = int(detail.get("cooldown_remain_sec") or 0)
                msg = detail.get("message") or "강화 대기 중입니다."
                try: await ctx.delete()
                except Exception:
                    try: await ctx.interaction.delete_original_response()
                    except Exception: pass
                return await ctx.followup.send(embed=discord.Embed(title=f"⏳ {msg}", description=f"다음 시도까지 {_fmt_cooldown_ts(remain)}", color=COLOR_WARN), ephemeral=True)
            if not resp or resp.status_code != 200:
                try: await ctx.delete()
                except Exception:
                    try: await ctx.interaction.delete_original_response()
                    except Exception: pass
                return await ctx.followup.send(f"❌ 강화 요청 실패 (코드 {getattr(resp, 'status_code', None)})", ephemeral=True)
            data = resp.json() or {}
        except Exception as e:
            try: await ctx.delete()
            except Exception:
                try: await ctx.interaction.delete_original_response()
                except Exception: pass
            return await ctx.followup.send(f"❌ 오류가 발생했어요.\n```{e}```", ephemeral=True)
        outcome = data.get("outcome", "fail")
        destroy_prevented = bool(data.get("destroy_prevented"))
        down_prevented = bool(data.get("down_prevented"))
        state = data.get("state") or {}
        rates_before = data.get("rates_before") or data.get("rates") or {}
        rates_after = data.get("rates_after") or {}
        gaho = data.get("gaho") or {}
        tokens = data.get("tokens") or {}
        esther_list = data.get("esthers") or []
        available_labels = data.get("available_esthers") or []
        available_keys = data.get("available_esther_keys") or []
        esther_entries: List[Tuple[str, str]] = [(lbl, key) for lbl, key in zip(available_labels, available_keys)]
        tooltips: Dict[str, str] = data.get("esther_tooltips") or {}
        auto_gaho = data.get("auto_gaho") or {}
        ancestral = data.get("ancestral") or {}

        if outcome == "already_max":
            level_label = state.get("level_label", "?강")
            pity_pct = float(state.get("pity") or 0.0)
            cur_level = int(state.get("level") or 1)
            embed = discord.Embed(title=f"💎 최종 단계 — 에스더 결속 가능", description="37강에 도달했습니다! 에스더와 결속하여 새로운 힘을 얻을 수 있습니다.", color=COLOR_PRIMARY)
            _set_author_with_pity(embed, level_label, cur_level, pity_pct)
            _add_esthers_field(embed, esther_list)
            view: Optional[discord.ui.View] = None
            if esther_entries:
                embed.add_field(name="결속 가능", value=", ".join([e[0] for e in esther_entries]) + "\n아래 버튼을 눌러 결속할 에스더를 선택하세요.", inline=False)
                lines = []
                for lbl, key in esther_entries:
                    tip = (tooltips.get(key) or "").strip()
                    lines.append(f"- **{lbl}**: {tip}" if tip else f"- **{lbl}**")
                if lines:
                    embed.add_field(name="에스더 능력", value="\n".join(lines), inline=False)
                view = EstherBindView(self, guild_id=guild_id, user_id=user_id, invoker_id=user_id, entries=esther_entries, tooltips=tooltips)
            else:
                embed.add_field(name="결속 가능", value="모든 에스더를 이미 결속했습니다.", inline=False)
                view = None

            # ---- NEW: 선대 정보/선택 + 노골 멘트 ----
            _add_ancestral_field(embed, ancestral.get("owned"), ancestral.get("warning"))
            ak = ancestral.get("available_keys") or []; al = ancestral.get("available") or []
            if ak and al:
                embed.add_field(
                    name="선대 에스더 가호 부여",
                    value="**37강 + 모든 에스더 결속 조건 충족!**\n**선대 에스더의 가호 부여가 가능합니다. 아래 버튼으로 선택하세요.**\n- 루테란 : 강화 성공시 50% 확률로 1강이 추가되며, 100% 확률로 선조의 가호가 발동됩니다.\n- 갈라투르 : 일반 선조의 가호가 삭제되고, 강화된 선조의 가호로 변경됩니다.\n- 시엔 : 파괴/하락 발생시 50%확률로 방어하며 장기백 증가량 2배가 됩니다.",
                    inline=False
                )
                entries = list(zip(al, ak))
                view = AncestralSelectView(guild_id, user_id, user_id, entries)
            # ---- /NEW ----

            stack_txt = _gaho_stack_text(gaho, state, data)
            shield = int((gaho or {}).get("shield") or 0); down_shield = int((gaho or {}).get("down_shield") or 0)
            upgrade_pending = bool((gaho or {}).get("upgrade_pending") or False)
            gaho_title = f"{EMO_BADGE_PLUS} 강화된 선조의 가호" if upgrade_pending else "📜 선조의 가호"
            embed.add_field(name=gaho_title, value=f"`{stack_txt}` · 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`", inline=False)
            cd_try = int((state or {}).get("cooldown_remain_sec") or 0)
            _apply_or_clear_cooldown_field(embed, cd_try, tokens)
            try: embed.set_thumbnail(url=user.display_avatar.url)
            except Exception: pass
            embed.set_footer(text=f"{ctx.user.display_name} • MococoBot")
            await ctx.edit(embed=embed, view=view)
            return

        now_duel_left = int((tokens or {}).get("three_runs_left") or 0)
        prev_duel_left = self._duel_before.get((guild_id, user_id), 0)
        duel_applied = (prev_duel_left > 0) or (now_duel_left > 0)
        duel_end = (prev_duel_left > 0 and now_duel_left == 0)
        self._duel_before[(guild_id, user_id)] = now_duel_left
        level_label = state.get("level_label", "?강"); pity_pct = float(state.get("pity") or 0.0)
        cur_level = int(state.get("level") or 1)
        effective_outcome = "destroy_prevented" if destroy_prevented else ("down_prevented" if down_prevented else outcome)
        pub_embed = discord.Embed(title=_outcome_title(effective_outcome), color=_outcome_color(effective_outcome))
        _set_author_with_pity(pub_embed, level_label, cur_level, pity_pct)
        prob_map = {"success": rates_before.get("success"), "destroy": rates_before.get("destroy"), "down": rates_before.get("down"), "fail": rates_before.get("fail")}
        prob = prob_map.get(outcome)
        pure_fail = (effective_outcome == "fail")

        # 공통 가호/스택
        stack_txt = _gaho_stack_text(gaho, state, data)
        shield = int((gaho or {}).get("shield") or 0); down_shield = int((gaho or {}).get("down_shield") or 0)
        upgrade_pending = bool((gaho or {}).get("upgrade_pending") or False)

        view: Optional[discord.ui.View] = None

        if pure_fail:
            if duel_applied:
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SWORDS} **일기토** — 실패/파괴가 없습니다.", inline=False)
            else:
                if isinstance(prob, (int, float)):
                    pub_embed.add_field(name="이번 시도", value=f"**{prob:.1f}%** 확률로 강화에 **실패**했어요.", inline=False)
                before_txt = _fmt_rates(rates_before)
                if before_txt: pub_embed.add_field(name=f"{EMO_CRYSTAL_BALL} 이번 시도 확률", value=before_txt, inline=False)

            # 바훈투르 자동 가호 (실패 분기)
            auto_gaho = data.get("auto_gaho") or {}
            if auto_gaho.get("triggered"):
                aeff_obj = (auto_gaho.get("effect") or {})
                aeff = aeff_obj.get("desc") or "자동 가호 발동"
                aup = bool(aeff_obj.get("upgrade_mode") or auto_gaho.get("upgrade_mode") or False)
                a_is_dice = (aeff_obj.get("type") == "dice")
                aname, aval = _format_auto_gaho_field(aup, aeff, level_label, stack_txt, shield, down_shield, is_dice=a_is_dice)
                _replace_or_add_field(pub_embed, name=aname, value=aval, inline=False, prefixes=_AUTO_GAHO_NAME_PREFIXES)
                if a_is_dice:
                    view = DiceRollView(self, guild_id, user_id, user_id)

            gaho_title = f"{EMO_BADGE_PLUS} 강화된 선조의 가호" if upgrade_pending else "📜 선조의 가호"
            if gaho.get("ready"):
                gaho_value = (("**강화 모드 활성화!**\n" if upgrade_pending else "") + f"**사용 가능!** — `{stack_txt}`\n" + f"- 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`\n" + "아래 버튼으로 **뽑기** 또는 **넘기기**를 선택하세요.")
                if view is None:
                    view = GahoDecisionView(self, guild_id=guild_id, user_id=user_id, invoker_id=user_id)
            else:
                gaho_value = (("**강화 모드 활성화!**\n" if upgrade_pending else "") + f"🧿 `{stack_txt}` · 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`")
            pub_embed.add_field(name=gaho_title, value=gaho_value, inline=False)

            buf = data.get("server_buff") or {}
            if buf.get("applied"):
                from_user = buf.get("from_username") or f"<@{buf.get('from_user_id')}>"
                amt = float(buf.get("amount") or 0.0)
                pub_embed.add_field(name="사회 환원", value=f"**{from_user}**님의 가호로 성공확률이 **+{amt:.1f}%** 증가하였어요.", inline=False)
            _add_esthers_field(pub_embed, esther_list)

            # ---- NEW: 선대 정보/선택 ----
            _add_ancestral_field(pub_embed, ancestral.get("owned"), ancestral.get("warning"))
            ak = ancestral.get("available_keys") or []; al = ancestral.get("available") or []
            if ak and al:
                pub_embed.add_field(
                    name="선대 에스더 가호 부여",
                    value="**37강 + 모든 에스더 결속 조건 충족!**\n**선대 에스더의 가호 부여가 가능합니다. 아래 버튼으로 선택하세요.**\n- 루테란 : 강화 성공시 50% 확률로 1강이 추가되며, 100% 확률로 선조의 가호가 발동됩니다.\n- 갈라투르 : 일반 선조의 가호가 삭제되고, 강화된 선조의 가호로 변경됩니다.\n- 시엔 : 파괴/하락 발생시 50%확률로 방어하며 장기백 증가량 2배가 됩니다.",
                    inline=False
                )
                entries = list(zip(al, ak))
                if view is None:
                    view = AncestralSelectView(guild_id, user_id, user_id, entries)
            # ---- /NEW ----

            try: pub_embed.set_thumbnail(url=user.display_avatar.url)
            except Exception: pass
            pub_embed.set_footer(text=f"{ctx.user.display_name} • MococoBot")
            cd_try = int((state or {}).get("cooldown_remain_sec") or 0)
            _apply_or_clear_cooldown_field(pub_embed, cd_try, tokens)
            if now_duel_left > 0: pub_embed = _apply_duel_skin(pub_embed, data)
            elif duel_end:
                pub_embed.title = f"{EMO_SWORDS} 강화 일기토 — 종료"; pub_embed.color = COLOR_DUEL
            await ctx.edit(embed=pub_embed, view=view)
            if view is not None:
                try:
                    msg = await ctx.interaction.original_response()
                    self._pending_gaho[pending_key] = msg.id
                except Exception:
                    self._pending_gaho[pending_key] = 1
            else:
                self._pending_gaho.pop(pending_key, None)
            return

        # 성공/하락/그 외 분기
        if duel_applied:
            if outcome == "success":
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SWORDS} **일기토** — **성공**!", inline=False)
            elif outcome == "down":
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SWORDS} **일기토** — **하락**!", inline=False)
            elif outcome == "pity_forced":
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SWORDS} **일기토** — 장기백 **확정 성공**!", inline=False)
            elif destroy_prevented:
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SWORDS} **일기토** — 파괴는 발생하지 않습니다.", inline=False)
            elif down_prevented:
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SWORDS} **일기토** — 하락이 무효 처리되었습니다.", inline=False)
        else:
            if destroy_prevented:
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SHIELD} **파괴 방지권**으로 파괴를 무효했습니다.", inline=False)
            elif down_prevented:
                pub_embed.add_field(name="이번 시도", value=f"{EMO_SHIELD} **하락 방지권**으로 하락을 무효했습니다.", inline=False)
            elif outcome == "pity_forced":
                pub_embed.add_field(name="이번 시도", value=f"{EMO_DIAMOND} **장기백 100% 확정 성공!**", inline=False)
            else:
                if isinstance(prob, (int, float)):
                    ptxt = f"{prob:.1f}%"
                    msg_map = {
                        "success": f"**{ptxt}** 확률로 강화에 **성공**했어요!",
                        "destroy": f"**{ptxt}** 확률로 장비가 **파괴**되어 1강으로 초기화됐어요.",
                        "down": f"**{ptxt}** 확률로 등급이 **하락**했어요.",
                        "fail": f"**{ptxt}** 확률로 강화에 **실패**했어요.",
                        "destroy_to_plus1": "파괴가 발생했으나 **파괴→+1 스택**으로 **+1강**이 적용되었습니다!",
                    }
                    msg = msg_map.get(effective_outcome)
                    if msg: pub_embed.add_field(name="이번 시도", value=msg, inline=False)
                before_txt = _fmt_rates(rates_before); after_txt = _fmt_rates(rates_after)
                if not duel_applied:
                    if before_txt: pub_embed.add_field(name=f"{EMO_CRYSTAL_BALL} 이번 시도 확률", value=before_txt, inline=False)
                    if after_txt: pub_embed.add_field(name=f"{EMO_CRYSTAL_BALL} 다음 시도 확률", value=after_txt, inline=False)

        # 바훈투르 자동 가호 (성공/기타 분기)
        auto_gaho = data.get("auto_gaho") or {}
        if auto_gaho.get("triggered"):
            aeff_obj = (auto_gaho.get("effect") or {})
            aeff = aeff_obj.get("desc") or "자동 가호 발동"
            aup = bool(aeff_obj.get("upgrade_mode") or auto_gaho.get("upgrade_mode") or False)
            a_is_dice = (aeff_obj.get("type") == "dice")
            aname, aval = _format_auto_gaho_field(aup, aeff, level_label, stack_txt, shield, down_shield, is_dice=a_is_dice)
            _replace_or_add_field(pub_embed, name=aname, value=aval, inline=False, prefixes=_AUTO_GAHO_NAME_PREFIXES)
            if a_is_dice:
                view = DiceRollView(self, guild_id, user_id, user_id)

        gaho_title = f"{EMO_BADGE_PLUS} 강화된 선조의 가호" if upgrade_pending else "📜 선조의 가호"
        if gaho.get("ready"):
            pub_embed.add_field(
                name=gaho_title,
                value=(("**강화 모드 활성화!**\n" if upgrade_pending else "")
                       + f"**사용 가능!** — `{stack_txt}`\n"
                       + f"- 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`\n"
                       + "아래 버튼으로 **뽑기** 또는 **넘기기**를 선택하세요."),
                inline=False,
            )
            if view is None:
                view = GahoDecisionView(self, guild_id=guild_id, user_id=user_id, invoker_id=user_id)
        else:
            pub_embed.add_field(name=gaho_title, value=f"`{stack_txt}` · 파괴 방지권 `x{shield}` · 하락 방지권 `x{down_shield}`", inline=False)

        buf = data.get("server_buff") or {}
        if buf.get("applied"):
            from_user = buf.get("from_username") or f"<@{buf.get('from_user_id')}>"
            amt = float(buf.get("amount") or 0.0)
            pub_embed.add_field(name="사회 환원", value=f"**{from_user}**님의 가호로 성공확률이 **+{amt:.1f}%** 증가하였어요.", inline=False)
        _add_esthers_field(pub_embed, esther_list)

        # ---- NEW: 선대 정보/선택 (일반 결과 흐름에도 노출) ----
        _add_ancestral_field(pub_embed, ancestral.get("owned"), ancestral.get("warning"))
        ak = ancestral.get("available_keys") or []; al = ancestral.get("available") or []
        if ak and al:
            pub_embed.add_field(
                name="선대 에스더 가호 부여",
                value="**37강 + 모든 에스더 결속 조건 충족!**\n**선대 에스더의 가호 부여가 가능합니다. 아래 버튼으로 선택하세요.**\n- 루테란 : 강화 성공시 50% 확률로 1강이 추가되며, 100% 확률로 선조의 가호가 발동됩니다.\n- 갈라투르 : 일반 선조의 가호가 삭제되고, 강화된 선조의 가호로 변경됩니다.\n- 시엔 : 파괴/하락 발생시 50%확률로 방어하며 장기백 증가량 2배가 됩니다.",
                inline=False
            )
            entries = list(zip(al, ak))
            if view is None:
                view = AncestralSelectView(guild_id, user_id, user_id, entries)
        # ---- /NEW ----

        try: pub_embed.set_thumbnail(url=user.display_avatar.url)
        except Exception: pass
        pub_embed.set_footer(text=f"{ctx.user.display_name} • MococoBot")
        cd_try = int((state or {}).get("cooldown_remain_sec") or 0)
        _apply_or_clear_cooldown_field(pub_embed, cd_try, tokens)
        if now_duel_left > 0: pub_embed = _apply_duel_skin(pub_embed, data)
        elif duel_end:
            pub_embed.title = f"{EMO_SWORDS} 강화 일기토 — 종료"; pub_embed.color = COLOR_DUEL
        await ctx.edit(embed=pub_embed, view=view)
        if view is not None:
            try:
                msg = await ctx.interaction.original_response()
                self._pending_gaho[pending_key] = msg.id
            except Exception:
                self._pending_gaho[pending_key] = 1
        else:
            self._pending_gaho.pop(pending_key, None)
        return
    @discord.slash_command(name="강화초기화", description="강화 상태 초기화합니다. (관리자 전용)")
    @discord.default_permissions(administrator=True)
    @discord.option("대상", description="지정하면 해당 유저만 초기화. 비워두면 길드 전체 초기화.", type=discord.Member, required=False, default=None)
    async def enhance_reset(self, ctx: discord.ApplicationContext, 대상: discord.Member | None = None):  # type: ignore[override]
        if not ctx.guild_id: return await ctx.respond("❌ 서버 내에서만 사용할 수 있어요.", ephemeral=True)
        perms = getattr(ctx.user, "guild_permissions", None)
        if not perms or not (perms.administrator or perms.manage_guild):
            return await ctx.respond("❌ 이 명령어는 관리자만 사용할 수 있어요.", ephemeral=True)
        guild_id = ctx.guild_id; target_user_id = 대상.id if 대상 else None
        try:
            payload: Dict[str, any] = {"guild_id": guild_id}
            if target_user_id is not None: payload["user_id"] = int(target_user_id)
            resp = await http_client.post("/enhance/reset", json=payload, timeout=10.0)
            if not resp or resp.status_code != 200:
                return await ctx.respond(f"❌ 초기화 실패 (코드 {getattr(resp, 'status_code', None)})", ephemeral=True)
            data = resp.json() or {}
            user_id_reset = data.get("user_id")
            title = "⚒️ 강화 초기화 완료"
            desc = f"- 대상: **<@{user_id_reset}>**\n" if user_id_reset else "- 대상: **서버 전체**\n"
            embed = discord.Embed(title=title, description=desc, color=COLOR_PRIMARY)
            embed.set_footer(text="MococoBot")
            if user_id_reset:
                self._pending_gaho.pop((guild_id, int(user_id_reset)), None)
                self._duel_before.pop((guild_id, int(user_id_reset)), None)
            else:
                self._pending_gaho.clear(); self._duel_before.clear()
            return await ctx.respond(embed=embed, ephemeral=True)
        except Exception as e:
            return await ctx.respond(f"❌ 오류가 발생했어요.\n```{e}```", ephemeral=True)
    @discord.slash_command(name="강화랭킹", description="강화 랭킹을 확인합니다.")
    @discord.option("종류", description="랭킹 기준을 선택하세요.", type=str, required=False, default="레벨",
                    choices=["레벨", "성공수"])
    async def enhance_rank(self, ctx: discord.ApplicationContext, 종류: str = "레벨"):  # type: ignore[override]
        if not ctx.guild_id: return await ctx.respond("❌ 서버 내에서만 사용할 수 있어요.", ephemeral=True)
        mode = "level" if 종류 == "레벨" else "success"
        try:
            resp = await http_client.get("/enhance/leaderboard", params={"guild_id": ctx.guild_id, "mode": mode, "limit": 20}, timeout=10.0)
            if not resp or resp.status_code != 200:
                return await ctx.respond(f"❌ 랭킹 조회 실패 (코드 {getattr(resp, 'status_code', None)})", ephemeral=True)
            rows = resp.json() or []
            if not rows: return await ctx.respond("📭 랭킹 데이터가 아직 없어요.", ephemeral=False)
            medals = ["🥇", "🥈", "🥉"]; lines: List[str] = []
            if mode == "level":
                for i, r in enumerate(rows, start=1):
                    prefix = medals[i - 1] if i <= 3 else f"`{i:2d}`"
                    name = r.get("username") or str(r.get("user_id"))
                    lvl = int(r.get("level") or 1); pity_pct = float(r.get("pity") or 0.0)
                    lines.append(f"{prefix} **{name}** — **{lvl}강** · 장기백 **{pity_pct:.0f}%**")
                title = f"{EMO_TROPHY} 강화 랭킹 — 현재 최고 레벨"
            else:
                for i, r in enumerate(rows, start=1):
                    prefix = medals[i - 1] if i <= 3 else f"`{i:2d}`"
                    name = r.get("username") or str(r.get("user_id")); cnt = int(r.get("success_count") or 0)
                    lines.append(f"{prefix} **{name}** — 성공 **{cnt}회**")
                title = f"{EMO_TROPHY} 강화 랭킹 — 누적 성공 수"
            embed = discord.Embed(title=title, description="\n".join(lines), color=COLOR_PRIMARY)
            embed.set_footer(text="상위 20명까지 표시됩니다.")
            return await ctx.respond(embed=embed, ephemeral=False)
        except Exception as e:
            return await ctx.respond(f"❌ 오류가 발생했어요。\n```{e}```", ephemeral=True)

def setup(bot: commands.AutoShardedBot):
    bot.add_cog(EnhanceCog(bot))
