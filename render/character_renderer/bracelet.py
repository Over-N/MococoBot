from __future__ import annotations

import re
import html
from typing import List, Tuple, Optional, Dict, Callable

from .constants import ACCESSORY_TYPE_ALIAS, GRADE_GRADIENTS
from .utils import strip_tags, parse_tooltip_json, iter_tooltip_strings, norm_type

__all__ = ["extract_access_refine_options", "extract_bracelet_fallback_opts", "extract_bracelet_extra_options", "extract_ability_stone_options"]

_BR_RE = re.compile(r"(?i)<br\s*/?>|\r\n|\n")
_IMG_RE = re.compile(r"(?i)<img[^>]*>")
_WS_RE = re.compile(r"\s+")
_COLOR_RE_1 = re.compile(r"(?i)color\s*=\s*['\"]#?([0-9a-f]{6})['\"]")
_COLOR_RE_2 = re.compile(r"(?i)style=\s*['\"][^'\"]*color\s*:\s*#?([0-9a-f]{6})")

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_INT_RE = re.compile(r"([\d,]+)")
_STONE_LINE_RE = re.compile(r"\[\s*<FONT[^>]*>(?P<name>[^<]+)</FONT>\s*\].*?Lv\.(?P<lv>\d+)", re.I | re.S)

_STAT_KEYS = (("신속", "신속"), ("특화", "특화"), ("치명", "치명"), ("제압", "제압"), ("인내", "인내"), ("숙련", "숙련"), ("체력", "체력"), ("최대생명력", "최대 생명력"), ("최대 생명력", "최대 생명력"), ("힘", "힘"), ("민첩", "민첩"), ("지능", "지능"))

NOISE_KEYS = ("한파티당하나만적용", "해당효과는한파티당하나만적용", "해당효과는한파티당1개만적용")

_BRACELET_STAT_RE = re.compile(
    r"^(신속|특화|치명|제압|인내|숙련|체력|지능|힘|민첩|최대\s*생명력)\s*\+?\s*([\d,]+)\s*$"
)

def _hex_to_rgba(h: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = (h or "").strip().lstrip("#")
    if len(h) != 6:
        return (255, 255, 255, alpha)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)

def _fmt_pct(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return s
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s

def _norm_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = _WS_RE.sub(" ", s).strip().lstrip("•◇◆-·").strip()
    s = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", s).replace("% ", "%")
    return s

def _nospace(s: str) -> str:
    return (s or "").replace(" ", "")

def _pcts(s: str) -> List[str]:
    return [_fmt_pct(x) for x in _PCT_RE.findall(s or "")]

def _pct_at(s: str, idx: int) -> str:
    a = _pcts(s)
    return a[idx] if 0 <= idx < len(a) else ""

def _int_after(s: str, key: str) -> str:
    m = re.search(re.escape(key) + r"\s*([\d,]+)", s or "")
    return m.group(1) if m else ""

def _has(ns: str, *subs: str) -> bool:
    return all(_nospace(x) in (ns or "") for x in subs)

def _is_noise(ns: str) -> bool:
    return any(k in (ns or "") for k in NOISE_KEYS)

def _is_stat_line(n: str, ns: str) -> bool:
    for k, _label in _STAT_KEYS:
        kk = _nospace(k)
        if ns.startswith(kk):
            rest = ns[len(kk):].lstrip("+")
            if rest and rest[0].isdigit():
                return True
    return False

def _parse_line_html(line_html: str) -> Tuple[str, Optional[Tuple[int, int, int, int]]]:
    s_html = line_html or ""
    cols = _COLOR_RE_1.findall(s_html)
    cols += _COLOR_RE_2.findall(s_html)
    color = _hex_to_rgba(cols[-1]) if cols else None
    s_html = _IMG_RE.sub(" ", s_html)
    s = strip_tags(s_html)
    s = html.unescape(s)
    s = _norm_text(s)
    s = re.sub(r"(?<!\s)([+\-])(\d)", r" \1\2", s)
    return s, color

def _find_bracelet_effect_block(tip: Dict) -> Optional[Dict]:
    if not isinstance(tip, dict):
        return None
    for node in tip.values():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "ItemPartBox":
            continue
        val = node.get("value")
        if not isinstance(val, dict):
            continue
        title = val.get("Element_000")
        title_s = strip_tags(title) if isinstance(title, str) else ""
        title_s = _norm_text(title_s)
        if "팔찌 효과" in title_s or "팔찌효과" in title_s:
            return val
    return None

def _item_tier_from_tooltip(tip: Dict) -> int:
    if not isinstance(tip, dict):
        return 0
    for node in tip.values():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "ItemTitle":
            continue
        v = node.get("value")
        if not isinstance(v, dict):
            continue
        for kk in ("leftStr2", "leftStr1", "leftStr0", "rightStr0"):
            s = v.get(kk)
            if not isinstance(s, str):
                continue
            t = _norm_text(strip_tags(html.unescape(s)))
            m = re.search(r"티어\s*(\d+)", t)
            if m:
                return int(m.group(1))
    for s in iter_tooltip_strings(tip):
        if not isinstance(s, str):
            continue
        t = _norm_text(strip_tags(html.unescape(s)))
        m = re.search(r"티어\s*(\d+)", t)
        if m:
            return int(m.group(1))
    return 0

def _bracelet_supports_option_view(item: Dict) -> bool:
    tip = parse_tooltip_json(item)
    tier = _item_tier_from_tooltip(tip) if tip else 0
    if tier == 0:
        return True
    return tier == 4

def _iter_bracelet_raw_lines(item: Dict) -> List[Tuple[str, Optional[Tuple[int, int, int, int]]]]:
    tip = parse_tooltip_json(item)
    if not tip:
        return []
    blk = _find_bracelet_effect_block(tip)
    lines: List[Tuple[str, Optional[Tuple[int, int, int, int]]]] = []
    def feed_html(s: str):
        for part in _BR_RE.split(s or ""):
            t, c = _parse_line_html(part)
            if t:
                lines.append((t, c))
    if isinstance(blk, dict):
        for k in sorted(blk.keys()):
            if k == "Element_000":
                continue
            v = blk.get(k)
            if isinstance(v, str):
                feed_html(v)
        if lines:
            return lines
    for s in iter_tooltip_strings(tip):
        if isinstance(s, str):
            feed_html(s)
    return lines

def _weapon_power_base(n: str) -> str:
    v = _int_after(n, "무기 공격력이")
    if v:
        return v
    m = re.search(r"무기\s*공격력\s*\+\s*([\d,]+)", n or "")
    return m.group(1) if m else ""

def _parse_wp_stack(n: str) -> Tuple[str, str, str, str]:
    m = re.search(r"공격\s*적중\s*시\s*([\d,]+)\s*초\s*마다\s*([\d,]+)\s*초\s*동안.*?무기\s*공격력이\s*([\d,]+)\s*증가.*?최대\s*([\d,]+)\s*중첩", n or "")
    if not m:
        return ("", "", "", "")
    return m.group(1), m.group(2), m.group(3), m.group(4)

def _parse_wp_hp(n: str) -> Tuple[str, str, str]:
    m = re.search(r"자신의\s*생명력이\s*([\d.]+)%\s*이상.*?공격\s*적중\s*시\s*([\d,]+)\s*초\s*동안.*?무기\s*공격력이\s*([\d,]+)\s*증가", n or "")
    if not m:
        return ("", "", "")
    return _fmt_pct(m.group(1)), m.group(2), m.group(3)

# fucking lostark;
SINGLE_RULES: Tuple[Tuple[Tuple[str, ...], Callable[[str], Optional[str]]], ...] = (
    (("추가 피해가", "악마 및 대악마 계열 피해량이", "증가한다"), lambda n: f"추피 +{_pct_at(n,0)}%, 악추피 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("치명타 적중률이", "치명타로 적중 시", "적에게 주는 피해가", "증가한다"), lambda n: f"치적 +{_pct_at(n,0)}%, 치명타 적중시 적주피 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("치명타 피해가", "치명타로 적중 시", "적에게 주는 피해가", "증가한다"), lambda n: f"치피증 +{_pct_at(n,0)}%, 치명타 적중시 적주피 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("보호 효과가 적용된 대상", "5초 동안", "적에게 주는 피해가", "증가한다", "아군 공격력 강화 효과가"), lambda n: f"보호막 상태시 피증 +{_pct_at(n,0)}%, 아공강 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("몬스터에게 공격 적중 시", "대상의 방어력을", "감소", "아군 공격력 강화 효과가"), lambda n: f"방깎 +{_pct_at(n,0)}%, 아공강 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("몬스터에게 공격 적중 시", "치명타 피해 저항", "감소", "아군 공격력 강화 효과가"), lambda n: f"치피증 +{_pct_at(n,0)}%, 아공강 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("몬스터에게 공격 적중 시", "치명타 저항", "감소", "아군 공격력 강화 효과가"), lambda n: f"치적 +{_pct_at(n,0)}%, 아공강 +{_pct_at(n,1)}%" if _pct_at(n,0) and _pct_at(n,1) else None),
    (("무기 공격력이","증가한다","공격 적중 시","최대","중첩"),lambda n:(lambda b,cd,dur,inc,mx:f"무공 +{b}, 적중시 무공 +{inc} (최대 {mx}중첩)" if b and inc and mx else None)(_weapon_power_base(n),*_parse_wp_stack(n))),
    (("공격 적중 시","무기 공격력이","증가한다","최대","중첩"),lambda n:(lambda cd,dur,inc,mx:f"적중시 무공 +{inc} (최대 {mx}중첩)" if inc and mx else None)(*_parse_wp_stack(n))),
    (("무기 공격력이", "증가한다", "자신의 생명력이", "공격 적중 시"), lambda n: (lambda b,hp,dur,inc: f"무공 +{b}, HP{hp}%↑ 적중시({dur}초) 무공 +{inc}" if b and hp and dur and inc else None)(_weapon_power_base(n), *_parse_wp_hp(n))),
    (("자신의 생명력이", "이상", "공격 적중 시", "무기 공격력이", "증가"), lambda n: (lambda hp,dur,inc: f"HP{hp}%↑ 적중시({dur}초) 무공 +{inc}" if hp and dur and inc else None)(*_parse_wp_hp(n))),
    (("무기 공격력이", "증가한다"), lambda n: (lambda v: f"무공 +{v}" if v and ("공격 적중 시" not in n) and ("자신의 생명력이" not in n) else None)(_weapon_power_base(n))),
    (("적에게 주는 피해가", "무력화 상태의 적에게 주는 피해가", "증가한다"), lambda n: f"적주피 +{_pct_at(n,0)}%, 무력 적주피 +{_pct_at(n,1)}%" if _pct_at(n,1) else None),
    (("스킬의 재사용 대기 시간이", "증가하지만", "적에게 주는 피해가", "증가한다"), lambda n: f"쿨타임 +{_pct_at(n,0)}%, 적주피 +{_pct_at(n,1)}%" if _pct_at(n,1) else None),
    (("추가 피해가", "증가한다"), lambda n: f"추피 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("악마 및 대악마 계열 피해량이", "증가한다"), lambda n: f"악추피 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("치명타 적중률이", "증가한다"), lambda n: f"치적 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("치명타 피해가", "증가한다"), lambda n: f"치피증 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("치명타로 적중 시", "적에게 주는 피해가", "증가한다"), lambda n: f"치명타 적중시 적주피 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("아군 공격력 강화 효과가", "증가한다"), lambda n: f"아공강 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("전투자원 자연 회복량", "+", "%"), lambda n: f"자원회복 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("백어택 스킬이", "적에게 주는 피해가", "증가한다"), lambda n: f"백어택 적주피 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("헤드어택 스킬이", "적에게 주는 피해가", "증가한다"), lambda n: f"헤드어택 적주피 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("방향성 공격이 아닌 스킬이", "적에게 주는 피해가", "증가한다"), lambda n: f"비방향 적주피 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("이동기 및 기상기 재사용 대기 시간이", "감소한다"), lambda n: f"이동기, 기상기 쿨감 -{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("보호 효과가 적용된 대상", "5초 동안", "적에게 주는 피해가", "증가한다"), lambda n: f"보호막 상태시 피증 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
    (("매 초마다", "10초 동안", "무기 공격력", "공격 및 이동 속도", "증가한다"), lambda n: (lambda w,p: f"10초마다 무공 +{w}, 공이속 +{p}%")(_int_after(n,"무기 공격력이"), _pct_at(n,0)) if _int_after(n,"무기 공격력이") and _pct_at(n,0) else None),
    (("공격 및 이동 속도가", "증가한다"), lambda n: f"공이속 +{_pct_at(n,0)}%" if _pct_at(n,0) else None),
)

PAIR_RULES: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...], int, Callable[[str, str], Optional[str]]], ...] = (
    (("무기 공격력이","증가한다"),("공격 적중 시","무기 공격력이","증가","최대","중첩"),3,lambda h,t:(lambda b,cd,dur,inc,mx:f"무공 +{b}, 적중시 무공 +{inc} (최대 {mx}중첩)" if b and inc and mx else None)(_weapon_power_base(h),*_parse_wp_stack(t))),
    (("무기 공격력이", "증가한다"), ("자신의 생명력이", "이상", "공격 적중 시", "무기 공격력이", "증가"), 3, lambda h,t: (lambda b,hp,dur,inc: f"무공 +{b}, HP{hp}%↑ 적중시({dur}초) 무공 +{inc}" if b and hp and dur and inc else None)(_weapon_power_base(h), *_parse_wp_hp(t))),
    (("치명타 적중률이", "증가한다"), ("치명타로 적중 시", "적에게 주는 피해가", "증가한다"), 3, lambda h,t: f"치적 +{_pct_at(h,0)}%, 치명타 적중시 적주피 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
    (("치명타 피해가", "증가한다"), ("치명타로 적중 시", "적에게 주는 피해가", "증가한다"), 3, lambda h,t: f"치피증 +{_pct_at(h,0)}%, 치명타 적중시 적주피 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
    (("추가 피해가", "증가한다"), ("악마 및 대악마 계열 피해량이", "증가한다"), 3, lambda h,t: f"추피 +{_pct_at(h,0)}%, 악추피 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
    (("보호 효과가 적용된 대상", "5초 동안", "적에게 주는 피해가", "증가한다"), ("아군 공격력 강화 효과가", "증가한다"), 4, lambda h,t: f"보호막 상태시 피증 +{_pct_at(h,0)}%, 아공강 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
    (("몬스터에게 공격 적중 시", "대상의 방어력을", "감소"), ("아군 공격력 강화 효과가", "증가한다"), 4, lambda h,t: f"방깎 +{_pct_at(h,0)}%, 아공강 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
    (("몬스터에게 공격 적중 시", "치명타 피해 저항", "감소"), ("아군 공격력 강화 효과가", "증가한다"), 4, lambda h,t: f"치피증 +{_pct_at(h,0)}%, 아공강 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
    (("몬스터에게 공격 적중 시", "치명타 저항", "감소"), ("아군 공격력 강화 효과가", "증가한다"), 4, lambda h,t: f"치적 +{_pct_at(h,0)}%, 아공강 +{_pct_at(t,0)}%" if _pct_at(h,0) and _pct_at(t,0) else None),
)

def _abbrev_single(text: str) -> str:
    n = _norm_text(text or "")
    ns = _nospace(n)
    for keys, fn in SINGLE_RULES:
        if _has(ns, *keys):
            v = fn(n)
            if v:
                return v
    return n

def _merge_bracelet_effects(lines: List[Tuple[str, Optional[Tuple[int, int, int, int]]]], limit: int) -> List[Tuple[str, Optional[Tuple[int, int, int, int]]]]:
    eff: List[Tuple[str, str, Optional[Tuple[int, int, int, int]]]] = []
    for t, c in lines:
        n = _norm_text(t or "")
        if not n:
            continue
        ns = _nospace(n)
        if _is_noise(ns):
            continue
        if _is_stat_line(n, ns):
            continue
        eff.append((n, ns, c))

    out: List[Tuple[str, Optional[Tuple[int, int, int, int]]]] = []
    seen = set()
    i = 0

    def push(text: str, color):
        k = (text or "").strip()
        if not k:
            return
        if k in seen:
            return
        seen.add(k)
        out.append((k, color))

    while i < len(eff) and len(out) < limit:
        hn, hns, hc = eff[i]
        merged_text = None
        consumed = 1
        for hkeys, tkeys, window, render in PAIR_RULES:
            if not _has(hns, *hkeys):
                continue
            j = i + 1
            end = min(len(eff), i + 1 + window)
            while j < end:
                tn, tns, _tc = eff[j]
                if _is_noise(tns):
                    j += 1
                    continue
                if _has(tns, *tkeys):
                    merged_text = render(hn, tn)
                    if merged_text:
                        consumed = j - i + 1
                    break
                j += 1
            if merged_text:
                break
        if merged_text:
            push(merged_text, hc)
            i += consumed
            continue
        push(_abbrev_single(hn), hc)
        i += 1

    return out

def _take_stat_lines(lines: List[Tuple[str, Optional[Tuple[int, int, int, int]]]], limit: int) -> List[Tuple[str, Optional[Tuple[int, int, int, int]]]]:
    out: List[Tuple[str, Optional[Tuple[int, int, int, int]]]] = []
    for t, c in lines:
        n = _norm_text(t or "")
        ns = _nospace(n)
        if _is_stat_line(n, ns):
            out.append((n, c))
            if len(out) >= limit:
                break
    return out

def _find_refine_block(tip: Dict) -> Optional[Dict]:
    if not isinstance(tip, dict):
        return None
    for node in tip.values():
        if not isinstance(node, dict):
            continue
        v = node.get("value")
        if isinstance(v, dict):
            title = v.get("Element_000")
            if isinstance(title, str) and ("연마 효과" in title or "연마효과" in title):
                return v
    return None

def extract_access_refine_options(item: Dict) -> List[Tuple[str, Optional[Tuple[int, int, int, int]]]]:
    tip = parse_tooltip_json(item)
    if not tip:
        return []
    candidates: List[str] = []
    blk = _find_refine_block(tip)
    if isinstance(blk, dict):
        for v in blk.values():
            if isinstance(v, str) and "emoticon_sign_greenDot" in v:
                candidates += _BR_RE.split(v)
    if not candidates:
        for s in iter_tooltip_strings(tip):
            if isinstance(s, str) and "emoticon_sign_greenDot" in s:
                candidates += _BR_RE.split(s)
    out: List[Tuple[str, Optional[Tuple[int, int, int, int]]]] = []
    for ln in candidates:
        if not ln or "emoticon_sign_greenDot" not in ln:
            continue
        text, color = _parse_line_html(ln)
        if text:
            out.append((text, color))
        if len(out) >= 3:
            break
    return out

def extract_ability_stone_options(item: Dict) -> List[Tuple[str, int]]:
    if norm_type(item.get("Type", "")) != "어빌리티 스톤":
        return []
    tip = parse_tooltip_json(item)
    if not tip:
        return []
    out: List[Tuple[str, int]] = []
    for s in iter_tooltip_strings(tip):
        if not isinstance(s, str):
            continue
        m = _STONE_LINE_RE.search(s)
        if m:
            name = html.unescape(m.group("name")).strip()
            lv = int(m.group("lv"))
            out.append((name, lv))
            if len(out) == 3:
                break
    return out

def extract_bracelet_fallback_opts(item: Dict) -> List[Tuple[str, Optional[Tuple[int, int, int, int]]]]:
    if norm_type(item.get("Type", "")) != "팔찌":
        return []
    lines = _iter_bracelet_raw_lines(item)
    return _take_stat_lines(lines, limit=3)

def extract_bracelet_extra_options(item: Dict) -> List[Tuple[str, Optional[Tuple[int, int, int, int]]]]:
    if norm_type(item.get("Type", "")) != "팔찌":
        return []
    if not _bracelet_supports_option_view(item):
        return [("옵션 보기가 지원되지 않는 팔찌에요", None)]
    lines = _iter_bracelet_raw_lines(item)
    return _merge_bracelet_effects(lines, limit=6)
