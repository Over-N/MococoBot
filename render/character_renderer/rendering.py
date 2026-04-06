from __future__ import annotations
import re
from datetime import datetime
from io import BytesIO
from typing import Iterable, List, Tuple, Optional, Dict
import json
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageSequence
import httpx

from .constants import (
    Canvas,
    Typo,
    Spacing,
    ORDER,
    TYPE_ALIAS,
    GRADE_GRADIENTS,
    BOX_W,
    BOX_H,
    BOX_RADIUS,
    BOX_COLOR,
    BOX_GAP_Y,
    BORDER_INSET,
    ORANGE,
    TITLE_SIZE,
    UPGRADE_SIZE,
    TEXT_RIGHT_GAP_FROM_ICON,
    BADGE_W,
    BADGE_H,
    BADGE_RADIUS,
    ACCESSORY_GAP_Y,
    ACCESS_OPT_SIZE,
    ACCESS_OPT_COLOR,
    ACCESS_OPT_X_GAP,
    ACCESS_OPT_Y_FROM_ICON_TOP,
    ACCESS_OPT_LINE_GAP,
    ACCESS_DIAMOND_HALF,
    SPECIAL_ARK,
    FONTS_DIR,
    ACCESSORY_ORDER,
)
 
_GRADIENT_CACHE: Dict[Tuple[int, int, Tuple[int, int, int], Tuple[int, int, int]], Image.Image] = {}

def _get_gradient_image(w: int, h: int, c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> Image.Image:
    key = (w, h, c1, c2)
    grad = _GRADIENT_CACHE.get(key)
    if grad is None:
        grad = make_linear_gradient(w, h, c1, c2).convert("RGBA")
        _GRADIENT_CACHE[key] = grad
    return grad

_ICON_MASK_CACHE: Dict[Tuple[int, int], Image.Image] = {}

def _get_icon_mask(w: int, h: int, radius: int) -> Image.Image:
    r = max(1, radius - 1)
    key = (w, h)
    mask = _ICON_MASK_CACHE.get(key)
    if mask is None:
        mask = rounded_mask(w, h, r)
        _ICON_MASK_CACHE[key] = mask
    return mask

def _draw_box_background(base_img: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, grade: Optional[str], box_mask: Image.Image) -> None:
    if grade in GRADE_GRADIENTS:
        g1, g2 = GRADE_GRADIENTS[grade]
        grad_img = _get_gradient_image(BOX_W, BOX_H, g1, g2)
        base_img.paste(grad_img, (x, y), box_mask)
    else:
        draw.rounded_rectangle([x, y, x + BOX_W, y + BOX_H], radius=BOX_RADIUS, fill=BOX_COLOR)

def _paste_icon_with_mask(base_img: Image.Image, icon_url: str, x: int, y: int, w: int, h: int) -> None:
    try:
        icon = fetch_icon(icon_url).convert("RGBA")
        if icon.size != (w, h):
            icon = icon.resize((w, h), Image.LANCZOS)
        mask = _get_icon_mask(w, h, BOX_RADIUS)
        a2 = ImageChops.multiply(icon.getchannel("A"), mask)
        icon.putalpha(a2)
        base_img.paste(icon, (x, y), icon)
    except Exception:
        pass

def _draw_quality_badge(draw: ImageDraw.ImageDraw, x: int, y: int, qv: Optional[int]) -> None:
    if qv is None:
        return
    bx = x
    by = y
    draw.rounded_rectangle([bx, by, bx + BADGE_W, by + BADGE_H], radius=BADGE_RADIUS, fill=_quality_color(qv))
    f_badge = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), 11)
    txt = str(qv)
    tb = draw.textbbox((0, 0), txt, font=f_badge)
    dx = (BADGE_W - (tb[2] - tb[0])) // 2
    dy = (BADGE_H - (tb[3] - tb[1])) // 2 - 1
    _safe_text(draw, (bx + dx, by + dy), txt, font=f_badge, fill=Canvas.WHITE)
from .utils import (
    load_font,
    ellipsis,
    strip_tags,
    parse_tooltip_json,
    norm_type,
    rounded_mask,
    make_linear_gradient,
    load_icon_15,
    fetch_icon,
    get_collect_point,
    parse_discord_emoji,
)
from .bracelet import (
    extract_access_refine_options,
    extract_bracelet_extra_options,
    extract_bracelet_fallback_opts,
    extract_ability_stone_options,
    _BRACELET_STAT_RE,
)

__all__ = ["render_character_card"]

def _safe_text(draw: ImageDraw.ImageDraw, position: Tuple[int, int], text: str,
               font: ImageFont.ImageFont, fill: Tuple[int, int, int, int]) -> None:
    try:
        draw.text(position, text, font=font, fill=fill)
    except Exception:
        try:
            fallback = ImageFont.load_default()
            draw.text(position, text, font=fallback, fill=fill)
        except Exception:
            pass

def _safe_textlength(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    try:
        return draw.textlength(text, font=font)
    except Exception:
        try:
            fallback = ImageFont.load_default()
            return draw.textlength(text, font=fallback)
        except Exception:
            try:
                size = getattr(font, 'size', 10)
            except Exception:
                size = 10
            return float(len(text) * size)

def _quality_color(v: int) -> Tuple[int, int, int, int]:
    if v >= 100:
        return (0xE5, 0x9C, 0x35, 255)
    if 90 <= v <= 99:
        return (0x8F, 0x5C, 0xD7, 255)
    if 70 <= v <= 89:
        return (0x5E, 0x96, 0xD7, 255)
    if 30 <= v <= 69:
        return (0x5B, 0xA7, 0x44, 255)
    if 10 <= v <= 29:
        return (0xD4, 0xA5, 0x59, 255)
    return (0xD9, 0x5C, 0x4B, 255)

def _extract_quality_value(item: dict) -> Optional[int]:
    t = norm_type(item.get("Type", ""))
    if t in ("어빌리티 스톤", "팔찌"):
        return None
    tip = parse_tooltip_json(item)
    try:
        v = (tip.get("Element_001", {}) or {}).get("value", {}) or {}
        qv = v.get("qualityValue")
        if isinstance(qv, int):
            return qv
        if isinstance(qv, str) and qv.isdigit():
            return int(qv)
    except Exception:
        pass
    return None

def _draw_diamond(draw: ImageDraw.ImageDraw, x: int, y: int, half: int, fill: Tuple[int, int, int, int]):
    cx, cy = x + half, y + half
    draw.polygon([(cx - half, cy), (cx, cy - half), (cx + half, cy), (cx, cy + half)], fill=fill)

def _collect_accessories(equipments: Iterable[dict]) -> List[dict]:
    items = equipments or []
    norm = [(norm_type(it.get("Type") or ""), it) for it in items]
    picked: List[dict] = []
    for want, cnt in [(norm_type(t), c) for t, c in ACCESSORY_ORDER]:
        count = 0
        for t_type, item in norm:
            if t_type == want:
                picked.append(item)
                count += 1
                if count >= cnt:
                    break
    return picked

def _draw_equipment_column(base_img: Image.Image, start_x: int, equipments: Iterable[dict]):
    draw = ImageDraw.Draw(base_img)
    box_mask = rounded_mask(BOX_W, BOX_H, BOX_RADIUS)
    inner_w = BOX_W - BORDER_INSET * 2
    inner_h = BOX_H - BORDER_INSET * 2
    by_type: Dict[str, dict] = {}
    for it in equipments or []:
        t = TYPE_ALIAS.get(it.get("Type") or "", it.get("Type") or "")
        by_type.setdefault(t, it)
    f_title_bold = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), TITLE_SIZE)
    f_up_reg = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), UPGRADE_SIZE)
    y = 148
    for t in ORDER:
        item = by_type.get(t)
        grade = item.get("Grade") if item else None
        _draw_box_background(base_img, draw, start_x, y, grade, box_mask)
        inner_x, inner_y = start_x + BORDER_INSET, y + BORDER_INSET
        full_name, upgrade_stage = ("", None)
        enh, rest = "", ""
        is_orb = False
        if item:
            full_name, upgrade_stage = _extract_enhance_and_upgradeline(item)
            enh, rest = "", full_name
            m = re.match(r"(\+\d+)\s*(.*)", full_name or "")
            if m:
                enh, rest = m.group(1), m.group(2)
            is_orb = "보주" in (rest or full_name or "")
        if item and item.get("Icon"):
            _paste_icon_with_mask(base_img, item["Icon"], inner_x, inner_y, inner_w, inner_h)
            if not is_orb:
                qv = _extract_quality_value(item)
                bx = inner_x + inner_w - 1 - BADGE_W
                by = inner_y + inner_h - 1 - BADGE_H
                _draw_quality_badge(draw, bx, by, qv)
        text_x = start_x + BOX_W + TEXT_RIGHT_GAP_FROM_ICON
        if item:
            if is_orb:
                name = rest or full_name or ""
                tb = draw.textbbox((0, 0), name, font=f_title_bold)
                th = tb[3] - tb[1]
                center_y = inner_y + inner_h // 2
                name_y = int(center_y - th // 2)
                _safe_text(draw, (text_x, name_y), name, font=f_title_bold, fill=Canvas.WHITE)
            else:
                text_y = y + 9
                x_cursor = text_x
                _safe_text(draw, (x_cursor, text_y), rest, font=f_title_bold, fill=Canvas.WHITE)
                _safe_text(draw, (x_cursor, text_y + 15), enh + "강", font=f_title_bold, fill=ORANGE)
                x_cursor += int(_safe_textlength(draw, enh + "강 ", font=f_title_bold))
                if upgrade_stage:
                    _safe_text(draw, (x_cursor, text_y + 16), f"(+{upgrade_stage})", font=f_up_reg, fill=ORANGE)
        y += BOX_H + BOX_GAP_Y


def _extract_enhance_and_upgradeline(item: dict) -> Tuple[str, str]:
    name = item.get("Name", "") or ""
    tip = parse_tooltip_json(item)
    stage = ""
    try:
        v = (tip.get("Element_005", {}) or {}).get("value") or ""
        text = strip_tags(v)
        m = re.search(r"\[상급\s*재련\]\s*(\d+)\s*단계", text)
        if m:
            stage = f"{m.group(1)}"
    except Exception:
        pass
    return (name, stage)

def _draw_accessory_column(base_img: Image.Image, start_x: int, start_y: int, equipments: Iterable[dict]):
    draw = ImageDraw.Draw(base_img)
    box_mask = rounded_mask(BOX_W, BOX_H, BOX_RADIUS)
    inner_w = BOX_W - BORDER_INSET * 2
    inner_h = BOX_H - BORDER_INSET * 2
    accs = _collect_accessories(equipments)
    y = start_y
    for item in accs:
        grade = item.get("Grade")
        _draw_box_background(base_img, draw, start_x, y, grade, box_mask)
        inner_x, inner_y = start_x + BORDER_INSET, y + BORDER_INSET
        if item.get("Icon"):
            _paste_icon_with_mask(base_img, item["Icon"], inner_x, inner_y, inner_w, inner_h)
        qv = _extract_quality_value(item)
        bx = inner_x + inner_w - 1 - BADGE_W
        by = inner_y + inner_h - 1 - BADGE_H
        _draw_quality_badge(draw, bx, by, qv)
        acc_type = norm_type(item.get("Type") or "")
        if acc_type in ("목걸이", "귀걸이", "반지"):
            opts = extract_access_refine_options(item)
            if opts:
                f_opt = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), ACCESS_OPT_SIZE)
                text_x = inner_x + inner_w + ACCESS_OPT_X_GAP
                text_y = inner_y + ACCESS_OPT_Y_FROM_ICON_TOP
                for opt_text, color_rgba in opts[:3]:
                    opt_text = re.sub(r"세레나데,\s*신앙,\s*조화\s*게이지\s*획득량", "서포터 아이덴티티 획득량", opt_text)
                    th = getattr(f_opt, 'size', 11)
                    d = ACCESS_DIAMOND_HALF
                    dy = max(0, (th - 2 * d) // 2)
                    if color_rgba is not None:
                        _draw_diamond(draw, text_x, text_y + dy, d, fill=color_rgba)
                        tx = text_x + 2 * d + 4
                    else:
                        tx = text_x
                    _safe_text(draw, (tx, text_y), opt_text, font=f_opt, fill=ACCESS_OPT_COLOR)
                    text_y += th + ACCESS_OPT_LINE_GAP
        if acc_type == "어빌리티 스톤":
            lines = extract_ability_stone_options(item)
            if lines:
                _draw_stone_badge_and_name(base_img, draw, (inner_x, inner_y), lines)
        y += BOX_H + ACCESSORY_GAP_Y

def _draw_stone_badge_and_name(base: Image.Image, draw: ImageDraw.ImageDraw, icon_box_xy: Tuple[int, int], lines: List[Tuple[str, int]]):
    x0, y0 = icon_box_xy
    if not lines:
        return
    STONE_BADGE_W = 15
    STONE_BADGE_H = 15
    STONE_BADGE_R = 3
    STONE_VAL_FONT_SIZE = 9
    STONE_NAME_FONT_SIZE = 11
    STONE_NAME_GAP_X = 9
    STONE_RIGHT_OFFSET = 5
    STONE_TOP_OFFSETS = (-2, 14, 30)
    STONE_BADGE_BG_TOP = (0x5E, 0x96, 0xD7, 255)
    STONE_BADGE_BG_BOT = (0xD9, 0x5C, 0x4B, 255)
    f_val = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), STONE_VAL_FONT_SIZE)
    f_name = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), STONE_NAME_FONT_SIZE)
    base_x = x0 + BOX_W + STONE_RIGHT_OFFSET
    for idx, (name, lv) in enumerate(lines[:3]):
        badge_x = base_x
        badge_y = y0 + STONE_TOP_OFFSETS[idx]
        fill_color = STONE_BADGE_BG_TOP if idx < 2 else STONE_BADGE_BG_BOT
        badge = Image.new("RGBA", (STONE_BADGE_W, STONE_BADGE_H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle([0, 0, STONE_BADGE_W - 1, STONE_BADGE_H - 1], radius=STONE_BADGE_R, fill=fill_color)
        base.paste(badge, (badge_x, badge_y), badge)
        val_text = str(lv)
        tw = draw.textlength(val_text, font=f_val)
        tx = badge_x + (STONE_BADGE_W - tw) / 2
        ty = badge_y + (STONE_BADGE_H - STONE_VAL_FONT_SIZE) / 2 - 1
        _safe_text(draw, (tx, ty), val_text, font=f_val, fill=Canvas.WHITE)
        name_x = badge_x + STONE_BADGE_W + STONE_NAME_GAP_X
        _safe_text(draw, (name_x, badge_y), name, font=f_name, fill=Canvas.WHITE)

def _draw_bracelet_icon_and_text(base_img: Image.Image, draw: ImageDraw.ImageDraw, equipments: Iterable[dict], payload: dict):
    bracelet = None
    for it in equipments or []:
        if norm_type(it.get("Type") or "") == "팔찌":
            bracelet = it
            break
    if not bracelet:
        return
    bx, by = 26, 533
    box_mask = rounded_mask(BOX_W, BOX_H, BOX_RADIUS)
    grade = bracelet.get("Grade")
    _draw_box_background(base_img, draw, bx, by, grade, box_mask)
    inner_x, inner_y = bx + BORDER_INSET, by + BORDER_INSET
    inner_w, inner_h = BOX_W - BORDER_INSET * 2, BOX_H - BORDER_INSET * 2
    if bracelet.get("Icon"):
        _paste_icon_with_mask(base_img, bracelet["Icon"], inner_x, inner_y, inner_w, inner_h)
    raw_lines = extract_access_refine_options(bracelet) or []
    raw_lines += extract_bracelet_fallback_opts(bracelet) or []
    summarized_pairs = extract_bracelet_extra_options(bracelet) or []
    PRIORITY = ["힘", "지능", "민첩", "체력", "치명", "특화", "신속", "제압", "인내", "숙련", "최대 생명력"]
    left_stats: List[Tuple[str, str, Optional[Tuple[int, int, int, int]]]] = []
    seen = set()
    for p in PRIORITY:
        for text, color in raw_lines:
            m = _BRACELET_STAT_RE.match(text)
            if not m:
                continue
            name, val = m.group(1), m.group(2)
            if name == p and (name, val) not in seen:
                if name == "최대 생명력":
                    name = "최생"
                left_stats.append((name, val, color))
                seen.add((name, val))
                if len(left_stats) == 3:
                    break
        if len(left_stats) == 3:
            break
    base_font = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 11)
    def _draw_diamond2(draw_ctx: ImageDraw.ImageDraw, x: int, y: int, size: int = 6, fill: Tuple[int, int, int, int] = (255, 255, 255, 255)) -> int:
        cx = x + size // 2 + 2
        cy = y + 11 // 2
        pts = [(cx, cy - size // 2), (cx + size // 2, cy), (cx, cy + size // 2), (cx - size // 2, cy)]
        draw_ctx.polygon(pts, fill=fill)
        return cx + size // 2 + 4
    left_lx = bx + BOX_W + 9
    left_ly = by + 4
    for _, (name, val, c) in enumerate(left_stats[:3]):
        x_cursor = left_lx
        name_text = f"{name} "
        _safe_text(draw, (x_cursor, left_ly), name_text, font=base_font, fill=Canvas.WHITE)
        x_cursor += int(_safe_textlength(draw, name_text, font=base_font))
        val_text = f"{val}"
        val_color = c if c is not None else Canvas.WHITE
        _safe_text(draw, (x_cursor, left_ly), val_text, font=base_font, fill=val_color)
        left_ly += 11 + 4
    spec_x = 159
    spec_y = 538
    spec_gap = 16
    diamond_size = 9
    y_cursor = spec_y
    for t_text, t_color in summarized_pairs:
        x_cursor = spec_x
        color = t_color if t_color is not None else Canvas.WHITE
        x_cursor = _draw_diamond2(draw, x_cursor, y_cursor, size=diamond_size, fill=color)
        _safe_text(draw, (x_cursor, y_cursor), t_text, font=base_font, fill=Canvas.WHITE)
        y_cursor += spec_gap

def _draw_profile_info(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    ap = payload.get("ArmoryProfile", {}) or {}

    f_label = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), 11)
    f_value = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 11)
    white = Canvas.WHITE

    def value_only(label_text: str, lx: int, ly: int, val: Optional[str],
                   gap: int = 11, below: Optional[str] = None,
                   vcolor: Tuple[int, int, int, int] = white,
                   fixed: bool = True):
        if val is None:
            return
        vx = lx if fixed else lx + _safe_textlength(draw, label_text, font=f_label) + gap
        _safe_text(draw, (vx, ly), str(val), font=f_value, fill=vcolor)
        if below:
            b_gap = max(16, getattr(f_value, 'size', 11) + 5)
            _safe_text(draw, (vx, ly + b_gap), str(below), font=f_value, fill=white)

    def _to_int_safe(v) -> int:
        try:
            if v is None: return 0
            if isinstance(v, (int, float)): return int(v)
            s = str(v).strip().replace(",", "")
            return int(float(s))
        except Exception:
            return 0

    def _honor_icon_name(honor_val: int) -> str:
        if honor_val >= 1000: return "honor_point_5.png"
        if honor_val >= 500:  return "honor_point_4.png"
        if honor_val >= 300:  return "honor_point_3.png"
        if honor_val >= 100:  return "honor_point_2.png"
        return "honor_point_1.png"

    def draw_honor_with_icon(lx: int, ly: int, honor_val_any):
        honor_text = "" if honor_val_any is None else str(honor_val_any)
        _, ty0, _, ty1 = draw.textbbox((0, 0), honor_text, font=f_value)
        text_h = ty1 - ty0
        icon_name = _honor_icon_name(_to_int_safe(honor_val_any))
        icon = load_icon_15(icon_name)
        if icon is not None and icon.size != (15, 15):
            try:
                icon = icon.resize((15, 15), Image.LANCZOS)
            except Exception:
                pass

        if icon:
            iy = int(ly + (ty0 + ty1) / 2 - icon.height / 2)
            try:
                base_img.paste(icon, (lx, iy), icon)
            except Exception:
                pass
            text_x = lx + icon.width + 3
            _safe_text(draw, (text_x, ly), honor_text, font=f_value, fill=white)
        else:
            _safe_text(draw, (lx, ly), honor_text, font=f_value, fill=white)

    def _get_stat_value(ap_dict: dict, stat_name: str) -> Optional[str]:
        try:
            for s in (ap_dict.get("Stats") or []):
                if (s or {}).get("Type") == stat_name:
                    return str((s or {}).get("Value"))
        except Exception:
            pass
        return None

    raw_title = ap.get("Title")
    title_text = strip_tags(str(raw_title) or "")
    guild_name = ap.get("GuildName")
    town_level = ap.get("TownLevel")
    town_name  = ap.get("TownName")
    item_lv    = ap.get("ItemAvgLevel")
    exp_lv     = ap.get("ExpeditionLevel")
    honor_pt   = ap.get("HonorPoint")
    combat_pow = ap.get("CombatPower")
    atk_value  = _get_stat_value(ap, "공격력")

    title_ark = ((payload or {}).get("ArkPassive", {}) or {}).get("Title") or ""
    combat_color = (0x5B, 0xA7, 0x44, 255) if title_ark in SPECIAL_ARK else (0xD9, 0x5C, 0x4B, 255)
    value_only("칭호", 496, 26, title_text or None, vcolor=ORANGE)
    value_only("길드", 496, 43, guild_name)
    value_only("영지", 496, 77, f"Lv.{town_level}" if town_level is not None else None, below=town_name)
    draw_honor_with_icon(496, 60, honor_pt)

    value_only("아이템 Lv", 683, 26, item_lv)
    value_only("원정대 Lv", 683, 43, exp_lv)
    value_only("전투력",   683, 60, combat_pow, vcolor=combat_color)
    value_only("공격력",   683, 77, atk_value)

def _draw_ark_grid(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    NAME_X = 583
    BADGE_X = 549

    NAME_FONT = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 11)
    LV_FONT   = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 9)

    BACK = (0x23, 0x27, 0x30, 255)
    TXT = Canvas.WHITE

    CORE_BOX_POS = {
        ("질서", "해"): (467, 294),
        ("질서", "달"): (467, 319),
        ("질서", "별"): (467, 344),
        ("혼돈", "해"): (467, 369),
        ("혼돈", "달"): (467, 394),
        ("혼돈", "별"): (467, 419),
    }
    CORE_BOX_W = CORE_BOX_H = 21
    CORE_BOX_R = 3

    BADGE_W = 27
    BADGE_H = 15
    BADGE_R = 3

    S = 4
    badge_bg = Image.new("RGBA", (BADGE_W * S, BADGE_H * S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge_bg)
    bd.rounded_rectangle(
        [0, 0, BADGE_W * S - 1, BADGE_H * S - 1],
        radius=BADGE_R * S,
        fill=BACK,
    )
    badge_bg = badge_bg.resize((BADGE_W, BADGE_H), Image.Resampling.LANCZOS)

    rows = [
        ("질서", "해", 298),
        ("질서", "달", 323),
        ("질서", "별", 348),
        ("혼돈", "해", 373),
        ("혼돈", "달", 398),
        ("혼돈", "별", 423),
    ]

    slots = (((payload or {}).get("ArkGrid", {}) or {}).get("Slots")) or []
    core_map = {}

    for s in slots:
        name = s.get("Name")
        if not isinstance(name, str):
            continue

        m = re.match(r"(질서|혼돈)의\s*(해|달|별)\s*코어\s*:\s*(.+)", name)
        if m:
            attr, body, tail = m.group(1), m.group(2), m.group(3).strip()
            disp_name = tail
        else:
            m2 = re.match(r"(질서|혼돈)의\s*(해|달|별)\s*코어\s*$", name)
            if m2:
                attr, body = m2.group(1), m2.group(2)
                disp_name = f"{attr} {body}"
            else:
                continue

        try:
            p = int(s.get("Point") or 0)
        except Exception:
            p = 0

        grade = s.get("Grade")
        core_map[(attr, body)] = (disp_name, p, grade)

    for key, (bx, by) in CORE_BOX_POS.items():
        if key not in core_map:
            continue

        _, _, grade = core_map.get(key, (None, None, None))
        gpair = GRADE_GRADIENTS.get(grade)
        if gpair:
            g1, g2 = gpair
            grad = make_linear_gradient(CORE_BOX_W, CORE_BOX_H, g1, g2).convert("RGBA")
            base_img.paste(grad, (bx, by), rounded_mask(CORE_BOX_W, CORE_BOX_H, CORE_BOX_R))
        else:
            draw.rounded_rectangle(
                [bx, by, bx + CORE_BOX_W, by + CORE_BOX_H],
                radius=CORE_BOX_R, fill=BACK
            )

    for attr, body, y in rows:
        name_pack = core_map.get((attr, body))
        if not name_pack:
            continue

        name_str, point, _grade = name_pack

        _safe_text(draw, (NAME_X, y), name_str, font=NAME_FONT, fill=TXT)

        badge_text = f"{point}P"
        try:
            tw = _safe_textlength(draw, badge_text, font=LV_FONT)
        except Exception:
            tw = draw.textlength(badge_text, font=LV_FONT)

        x0 = BADGE_X
        y0 = y + (NAME_FONT.size - BADGE_H) // 2

        base_img.paste(badge_bg, (x0, y0), badge_bg)

        tx = x0 + int((BADGE_W - tw) / 2)
        ty = y0 + (BADGE_H - LV_FONT.size) // 2 - 1
        _safe_text(draw, (tx, ty), badge_text, font=LV_FONT, fill=TXT)


def _draw_ark_grid_effects(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    EFFECT_POS = {
        "공격력":       (885, 298),
        "보스 피해":     (885, 323),
        "추가 피해":     (885, 348),
        "낙인력":       (885, 374),
        "아군 피해 강화": (885, 399),
        "아군 공격 강화": (885, 424),
    }
    LV_FONT   = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 9)
    effects = (((payload or {}).get("ArkGrid", {}) or {}).get("Effects")) or []
    TXT = Canvas.WHITE
    def _to_int(v):
        try:
            if isinstance(v, bool) or v is None:
                return None
            if isinstance(v, (int, float)):
                return int(v)
            m = re.search(r"(\d+)", str(v))
            return int(m.group(1)) if m else None
        except Exception:
            return None

    level_map = {}
    for e in effects:
        nm = e.get("Name")
        lv = _to_int(e.get("Level"))
        if isinstance(nm, str):
            level_map[nm.strip()] = lv

    GEM_FONT = LV_FONT
    for key, (x, y) in EFFECT_POS.items():
        lv = level_map.get(key)
        text = f"Lv. {lv:02d}" if isinstance(lv, int) and lv > 0 else "-"
        _safe_text(draw, (x, y), text, font=GEM_FONT, fill=TXT)


def _draw_collectibles_right_top(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    white = Canvas.WHITE
    f_val = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 11)
    specs = [
        ("모코코 씨앗", 798, 26),
        ("섬의 마음", 798, 43),
        ("위대한 미술품", 798, 60),
        ("거인의 심장", 798, 77),
        ("이그네아의 징표", 856, 26),
        ("항해 모험물", 856, 43),
        ("세계수의 잎", 856, 60),
        ("오르페우스의 별", 856, 77),
        ("기억의 오르골", 905, 26),
        ("크림스네일의 해도", 905, 43),
        ("누크만의 환영석", 905, 60),
    ]
    for type_name, ax, ay in specs:
        draw.text((ax, ay), get_collect_point(payload, type_name), fill=white, font=f_val)

def _draw_ark_passive(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    Y_VALUE = 485

    ark = ((payload or {}).get("ArkPassive", {}) or {})
    pts = (ark.get("Points") or [])
    by_name = {(p or {}).get("Name"): (p or {}) for p in pts if isinstance(p, dict)}

    order = ["진화", "깨달음", "도약"]
    total_xmap = {"진화": 534, "깨달음": 687, "도약": 845}
    info_xmap = {"진화": 562, "깨달음": 715, "도약": 868}

    f_val = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 11)
    f_badge = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 9)

    def parse_info(desc: str) -> str:
        if not isinstance(desc, str):
            return ""
        nums = re.findall(r"(\d+)", desc)
        if len(nums) >= 2:
            return f"{int(nums[0])}랭크 {int(nums[1])}"
        return desc.replace("레벨", "").strip()

    for name in order:
        data = by_name.get(name, {})
        x = total_xmap[name]
        val_text = str(data.get("Value", ""))
        _safe_text(draw, (x, Y_VALUE), val_text, font=f_val, fill=Canvas.WHITE)
        info_text = parse_info(data.get("Description", ""))
        if info_text:
            ix = info_xmap[name]
            _safe_text(draw, (ix, Y_VALUE), info_text, font=f_val, fill=Canvas.WHITE)

    effects = (ark.get("Effects") or [])

    def strip_tags(s: str) -> str:
        if not isinstance(s, str):
            return ""
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def parse_effect(desc: str):
        s = strip_tags(desc)
        m_tier = re.search(r"(\d+)\s*티어", s)
        if not m_tier:
            return None
        tier = int(m_tier.group(1))
        after = s[m_tier.end():].strip()
        m_lv = re.search(r"(.+?)\s*Lv\.?\s*(\d+)\s*$", after)
        if not m_lv:
            return None
        node_name = m_lv.group(1).strip()
        lv = m_lv.group(2).strip()
        if not node_name:
            return None
        return tier, node_name, lv

    grouped = {k: {} for k in order}
    for e in effects:
        if not isinstance(e, dict):
            continue
        cat = e.get("Name")
        if cat not in grouped:
            continue
        parsed = parse_effect(e.get("Description", ""))
        if not parsed:
            continue
        tier, node_name, lv = parsed
        grouped[cat].setdefault(tier, []).append((node_name, lv))

    BOX_W = 15
    BOX_H = 15
    BOX_R = 3
    BOX_FILL = (0x23, 0x27, 0x30, 255)

    h_bbox = draw.textbbox((0, 0), "Ag", font=f_val)
    text_h = (h_bbox[3] - h_bbox[1])
    text_top = h_bbox[1]
    line_h = max(text_h, BOX_H) + 2

    def _row_text_y(row_top: int) -> int:
        return row_top + (line_h - text_h) // 2 - text_top

    def _row_badge_y(row_top: int) -> int:
        return row_top + (line_h - BOX_H) // 2

    def draw_tier_badge(x0: int, y0: int, tier: int):
        x1 = x0 + BOX_W
        y1 = y0 + BOX_H
        if hasattr(draw, "rounded_rectangle"):
            draw.rounded_rectangle((x0, y0, x1, y1), radius=BOX_R, fill=BOX_FILL)
        else:
            draw.rectangle((x0, y0, x1, y1), fill=BOX_FILL)

        t = str(tier)
        tb = draw.textbbox((0, 0), t, font=f_badge)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
        tx = x0 + (BOX_W - tw) // 2 - tb[0]
        ty = y0 + (BOX_H - th) // 2 - tb[1]
        _safe_text(draw, (tx, ty), t, font=f_badge, fill=Canvas.WHITE)

    LAYOUT_Y = 510
    LAYOUT = {
        "진화": {"box": 466, "name": 488, "lv": 590},
        "깨달음": {"box": 619, "name": 641, "lv": 750},
        "도약": {"box": 772, "name": 794, "lv": 901},
    }

    for cat in order:
        lay = LAYOUT.get(cat)
        if not lay:
            continue

        bx0 = lay["box"]
        nx0 = lay["name"]
        vx0 = lay["lv"]

        y = LAYOUT_Y
        tiers = sorted(grouped[cat].keys())

        vals = [str(lv) for tier in tiers for _, lv in grouped[cat][tier]]
        if vals:
            try:
                cell_w = max(draw.textbbox((0, 0), v, font=f_val)[2] for v in vals)
            except Exception:
                try:
                    cell_w = max(f_val.getbbox(v)[2] for v in vals)
                except Exception:
                    try:
                        cell_w = max(_safe_textlength(draw, v, font=f_val) for v in vals)
                    except Exception:
                        cell_w = max(draw.textlength(v, font=f_val) for v in vals)
            cell_w = int(round(cell_w))
        else:
            cell_w = 0

        for tier in tiers:
            nodes = grouped[cat][tier]

            draw_tier_badge(bx0, _row_badge_y(y), tier)

            for i, (node_name, lv) in enumerate(nodes):
                val = str(lv)
                row_top = y + (i * line_h)
                ty = _row_text_y(row_top)

                _safe_text(draw, (nx0, ty), node_name, font=f_val, fill=Canvas.WHITE)

                try:
                    r = draw.textbbox((0, 0), val, font=f_val)[2]
                except Exception:
                    try:
                        r = f_val.getbbox(val)[2]
                    except Exception:
                        try:
                            r = _safe_textlength(draw, val, font=f_val)
                        except Exception:
                            r = draw.textlength(val, font=f_val)

                x = vx0 + cell_w - int(round(r))
                _safe_text(draw, (x, ty), val, font=f_val, fill=Canvas.WHITE)
            y = y + (len(nodes) * line_h)

def _draw_ark_passive_effects(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    effects = (((payload or {}).get("ArmoryEngraving", {}) or {}).get("ArkPassiveEffects")) or []
    if not isinstance(effects, list):
        return

    X_BOX = 776
    Y_START = 167
    w = h = 15
    r = 3
    f_level = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), 11)
    f_name  = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 11)
    f_pill  = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 9)
    def grade_color(g: str) -> Tuple[int, int, int, int]:
        g = (g or "").strip()
        if g == "유물":
            return (0xD9, 0x5C, 0x4B, 255)
        if g == "전설":
            return (0xE5, 0x9C, 0x35, 255)
        return (0x23, 0x27, 0x30, 255)
    for i, eff in enumerate(effects):
        if not isinstance(eff, dict):
            continue
        y = Y_START + i * (h + 2)
        draw.rounded_rectangle([X_BOX, y, X_BOX + w, y + h], radius=r, fill=grade_color(eff.get("Grade")))
        lvl = str(eff.get("Level", ""))
        tw = draw.textlength(lvl, font=f_level)
        level_height = getattr(f_level, "size", 11)
        _safe_text(draw, (X_BOX + (w - tw) / 2, y - 1 + (h - level_height) / 2), lvl, font=f_level, fill=Canvas.WHITE)
        name = str(eff.get("Name", "") or "")
        _safe_text(draw, (X_BOX + w + 5, y - 1), name, font=f_name, fill=Canvas.WHITE)
        asl = eff.get("AbilityStoneLevel", None)
        if asl is not None:
            pill_text = f"Lv. {int(asl)}"
            ptw = draw.textlength(pill_text, font=f_pill)
            pad_lr, pad_tb = 5, 2
            pw = int(ptw + pad_lr * 2)
            ph = int(getattr(f_pill, 'size', 9) + pad_tb * 2)
            PX = X_BOX + w + 96
            PY = y + (h - ph) // 2
            draw.rounded_rectangle([PX, PY, PX + pw, PY + ph], radius=3, fill=(0x23, 0x27, 0x30, 255))
            _safe_text(draw, (PX + pad_lr, PY + pad_tb - 1), pill_text, font=f_pill, fill=Canvas.WHITE)


def _parse_gems(payload: dict) -> Tuple[List[dict], List[dict]]:
    gems = (((payload or {}).get("ArmoryGem", {}) or {}).get("Gems")) or []
    if not isinstance(gems, list):
        return [], []
    cd_list, dmg_list = [], []
    for g in gems:
        if not isinstance(g, dict):
            continue
        tip = g.get("Tooltip") or ""
        try:
            t = strip_tags(tip) if isinstance(tip, str) else strip_tags(json.dumps(tip, ensure_ascii=False))
        except Exception:
            t = str(tip)
        t = t.replace(" ", "")
        if "재사용대기시간" in t and "감소" in t:
            cd_list.append(g)
        elif ("피해" in t and "증가" in t) or ("지원효과" in t and "증가" in t):
            dmg_list.append(g)
    return cd_list[:9], dmg_list[:9]

def _draw_rounded_outline(draw: ImageDraw.ImageDraw, xy: Tuple[int, int, int, int], radius: int, outline: Tuple[int, int, int, int], width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, outline=outline, width=width)

def _draw_gem_box(base_img: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, gem: Optional[dict]):
    w = h = 29
    r = 3
    border = (0x26, 0x29, 0x33, 255)
    rect = [x, y, x + w, y + h]
    if gem:
        grad = None
        try:
            gname = (gem.get("Grade") or "").strip()
            c1c2 = GRADE_GRADIENTS.get(gname)
            if c1c2:
                grad = make_linear_gradient(w, h, c1c2[0], c1c2[1]).convert("RGBA")
        except Exception:
            grad = None
        if grad is not None:
            base_img.paste(grad, (x, y), rounded_mask(w, h, r))
        else:
            _draw_rounded_outline(draw, rect, r, outline=border, width=1)
        try:
            icon_url = gem.get("Icon")
            if icon_url:
                ico = fetch_icon(icon_url).convert("RGBA")
                if ico.size != (w, h):
                    ico = ico.resize((w, h), Image.LANCZOS)
                mask = rounded_mask(w, h, r)
                a2 = ImageChops.multiply(ico.getchannel("A"), mask)
                ico.putalpha(a2)
                base_img.paste(ico, (x, y), ico)
        except Exception:
            pass
        try:
            lvl = gem.get("Level", None)
            lvl_text = f"{int(lvl)}" if str(lvl).isdigit() else (str(lvl) if lvl is not None else "")
            if lvl_text:
                pill_font = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), 11)
                text = lvl_text
                tw = draw.textlength(text, font=pill_font)
                pad = 2
                pw = int(tw + pad * 2)
                ph = int(pill_font.size + pad * 2)
                px = x + w - 3 - pw
                py = y + h - 3 - ph
                draw.rounded_rectangle([px, py, px + pw, py + ph], radius=3, fill=(0x23, 0x27, 0x30, 255))
                draw.text((px + pad, py + pad - 1), text, font=pill_font, fill=Canvas.WHITE)
        except Exception:
            pass
    else:
        _draw_rounded_outline(draw, rect, r, outline=border, width=1)

def _draw_gem_grids(base_img: Image.Image, draw: ImageDraw.ImageDraw, payload: dict):
    ROW_X = 466
    ROW1_Y = 167
    ROW2_Y = 225
    BOX_W  = 29
    GAP    = 3
    STEP_X = BOX_W + GAP
    cd_gems, dmg_gems = _parse_gems(payload)
    for i in range(9):
        gx = ROW_X + i * STEP_X
        gem = dmg_gems[i] if i < len(dmg_gems) else None
        _draw_gem_box(base_img, draw, gx, ROW1_Y, gem)
    for i in range(9):
        gx = ROW_X + i * STEP_X
        gem = cd_gems[i] if i < len(cd_gems) else None
        _draw_gem_box(base_img, draw, gx, ROW2_Y, gem)

def _draw_footer_logo(base_img: Image.Image, draw: ImageDraw.ImageDraw):
    try:
        lx, ly = 26, 664
        f_small = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), 9)

        text = f"Image created by Mococo | {datetime.now().strftime('%Y.%m.%d %H:%M')}"
        draw.text((lx, ly), text, font=f_small, fill=Canvas.WHITE)
        
    except Exception:
        pass

def _draw_background_layer(base_img: Image.Image, char_img: Optional[Image.Image], img_opacity: float = 0.3):
    if not char_img:
        return
    W, H = base_img.size
    fg = char_img.convert("RGBA")
    cw, ch = fg.size
    paste_x = W - cw
    paste_y = 0
    if 0.0 < img_opacity < 1.0:
        a = fg.getchannel("A").point(lambda p: int(p * img_opacity))
        fg.putalpha(a)
    base_img.alpha_composite(fg, (paste_x, paste_y))
    grad_w = 345
    if grad_w > 0:
        grad = Image.new("RGBA", (grad_w, H))
        px = grad.load()
        half = max(1, grad_w // 2)
        for x in range(grad_w):
            alpha = 255 if x < half else int(255 * (1 - (x - half) / max(1, grad_w - half)))
            for y in range(H):
                px[x, y] = (0x0D, 0x0E, 0x12, alpha)
        base_img.alpha_composite(grad, (0, 0))

def _overlay_static_background(base_img: Image.Image, payload: dict):
    def _parse_item_level(p: dict) -> float:
        try:
            raw = ((p or {}).get("ArmoryProfile") or {}).get("ItemAvgLevel")
            if raw is None:
                return 0.0
            if isinstance(raw, (int, float)):
                return float(raw)
            s = str(raw).strip().replace(",", "")
            return float(s)
        except Exception:
            return 0.0

    try:
        ilvl = _parse_item_level(payload)
        fname = "background_on.png" if ilvl >= 1700.0 else "background_off.png"
        bg_dir = FONTS_DIR.parent / "background"
        bg_path = bg_dir / fname

        bg = Image.open(bg_path).convert("RGBA")
        if bg.size != base_img.size:
            bg = bg.resize(base_img.size, Image.LANCZOS)
        base_img.alpha_composite(bg, (0, 0))
    except Exception:
        pass

def render_character_card(server: str,
                         nickname: str,
                         class_name: str,
                         equipments: Iterable[dict] = None,
                         payload: Optional[dict] = None,
                         nickname_emoji: Optional[str] = None) -> BytesIO:
    img = Image.new("RGBA", (Canvas.W, Canvas.H), Canvas.BG)
    draw = ImageDraw.Draw(img)
    char_img = None
    client = None
    try:
        client = httpx.Client(timeout=10.0)
    except Exception:
        client = None
    def _parse_item_level(p: Optional[dict]) -> float:
        try:
            raw = ((p or {}).get("ArmoryProfile") or {}).get("ItemAvgLevel")
            if raw is None: return 0.0
            if isinstance(raw, (int, float)): return float(raw)
            return float(str(raw).strip().replace(",", ""))
        except Exception:
            return 0.0
    try:
        url = (payload or {}).get("ArmoryProfile", {}).get("CharacterImage")
        if url:
            r = client.get(url) if client else httpx.get(url, timeout=10.0)
            r.raise_for_status()
            char_img = Image.open(BytesIO(r.content)).convert("RGBA")
    except Exception:
        char_img = None
    ilvl = _parse_item_level(payload)
    _draw_background_layer(img, char_img)
    if ilvl >= 1700.0:
        _draw_ark_grid(img, draw, payload or {})
    _overlay_static_background(img, payload or {})
    _draw_profile_info(img, draw, payload or {})
    _draw_collectibles_right_top(img, draw, payload or {})
    draw.line([(0, 128), (Canvas.W, 128)], fill=(0x26, 0x29, 0x33, 255), width=1)
    f_server = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), Typo.SERVER_SIZE)
    f_nick = load_font(str(FONTS_DIR / "Pretendard-Bold.otf"), Typo.NICK_SIZE)
    f_class = load_font(str(FONTS_DIR / "Pretendard-Regular.otf"), Typo.CLASS_SIZE)
    server = ellipsis(draw, server or "—", f_server, Canvas.W - Spacing.LEFT * 2)
    raw_nick = nickname or "—"
    display_nickname = ellipsis(draw, raw_nick, f_nick, Canvas.W - Spacing.LEFT * 2)
    class_name = ellipsis(draw, class_name or "—", f_class, Canvas.W - Spacing.LEFT * 2)
    x = Spacing.LEFT
    y_server = Spacing.TOP
    _safe_text(draw, (x, y_server), server, font=f_server, fill=Canvas.WHITE)
    _, _, _, h_server = draw.textbbox((0, 0), server, font=f_server)
    y_nick = y_server + h_server + Spacing.GAP_SERVER_NICK
    _safe_text(draw, (x, y_nick), display_nickname, font=f_nick, fill=Canvas.WHITE)
    _, _, _, h_nick = draw.textbbox((0, 0), display_nickname, font=f_nick)
    
    m1 = (payload or {}).get("emojis") or {}
    m2 = (payload or {}).get("Emojis") or {}

    uid = str(((payload or {}).get("user_id")) or "")
    token = nickname_emoji

    if not token and uid:
        token = m1.get(uid) or m2.get(uid)

    eid = None
    animated = False
    if token:
        try:
            eid, animated = parse_discord_emoji(token)
            if eid:
                ext = "gif" if animated else "png"
                url = f"https://cdn.discordapp.com/emojis/{eid}.{ext}"
                resp = client.get(url) if client else httpx.get(url, timeout=10.0)
                if resp.status_code == 200:
                    em = Image.open(BytesIO(resp.content))
                    try:
                        if getattr(em, "is_animated", False):
                            em.seek(0)
                    except Exception:
                        pass
                    em = em.convert("RGBA")
                    target_h = h_nick
                    scale = target_h / float(em.height) if em.height > 0 else 1.0
                    ew = max(1, int(em.width * scale))
                    eh = max(1, int(em.height * scale))
                    nick_width = _safe_textlength(draw, display_nickname, f_nick)
                    ex = int(x + nick_width + 6)
                    ey = int(y_nick + (h_nick - eh) / 2)
                    em = em.resize((ew, eh), Image.LANCZOS)
                    img.paste(em, (ex, ey), em)
        except Exception:
            pass
    y_class = y_nick + h_nick + Spacing.GAP_NICK_CLASS
    def _compose_class_with_ark_title(cname: str, pay: dict) -> str:
        try:
            title = ((pay or {}).get("ArkPassive", {}) or {}).get("Title") or ""
            title = str(title).strip()
            if title:
                return f"{cname} | {title}"
        except Exception:
            pass
        return cname
    _safe_text(draw, (x, y_class), _compose_class_with_ark_title(class_name, payload or {}), font=f_class, fill=Canvas.WHITE)
    equip_start_x = Spacing.LEFT
    _draw_equipment_column(img, equip_start_x, equipments or [])
    _draw_accessory_column(img, 213, 148, equipments or [])
    _draw_bracelet_icon_and_text(img, draw, equipments or [], payload or {})
    _draw_ark_passive(img, draw, payload or {})
    _draw_ark_passive_effects(img, draw, payload or {})
    if ilvl >= 1700.0:
        _draw_ark_grid_effects(img, draw, payload or {})
    _draw_gem_grids(img, draw, payload or {})
    _draw_footer_logo(img, draw)
    buf = BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    if client:
        try:
            client.close()
        except Exception:
            pass
    return buf
