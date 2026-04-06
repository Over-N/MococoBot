from pathlib import Path
from io import BytesIO
from typing import Optional, Tuple, Dict
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont
import re, html

BASE_DIR = Path(__file__).resolve().parent
FONTS_DIR = BASE_DIR / "fonts"
RESIZEING = 2

@lru_cache(maxsize=32)
def _get_frame_image(path: str) -> Image.Image:
    try:
        frame = Image.open(path).convert("RGBA").resize((CARD_W, CARD_H))
        return frame
    except Exception:
        return Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))


@lru_cache(maxsize=128)
def _get_raw_image(path: str) -> Image.Image:
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

# 카드 크기
CARD_W, CARD_H = 156*RESIZEING, 241*RESIZEING
CARD_BG = (0x0D, 0x0E, 0x12, 255)
CARD_RADIUS = 5*RESIZEING

# 폰트
FONT_REGULAR = str(FONTS_DIR / "Pretendard-Regular.otf")
FONT_BOLD    = str(FONTS_DIR / "Pretendard-Bold.otf")

_CLASS_BG_PLACEMENTS: Dict[str, Tuple[Tuple[int, int], Tuple[int, int]]] = {
    "워로드":       ((255, 295), (-35,   0)),
    "디스트로이어": ((255, 295), (-35,   0)),
    "홀리나이트":   ((255, 295), (-35,   0)),
    "버서커":       ((255, 295), (-35,   0)),
    "슬레이어":     ((255, 295), (-35,   0)),
    "발키리":       ((255, 295), (-35,   0)),

    "도화가":       ((268, 310), (-42, -50)),
    "기상술사":     ((268, 310), (-42, -50)),
    "환수사":       ((268, 310), (-42, -50)),

    # 나머지 디폴트
    "_default":     ((255, 295), (-34,  -4)),
}

SPECIAL_GRADIENTS: dict[str, tuple[tuple[int,int,int], tuple[int,int,int]]] = {
    "종막":        ((222, 190, 240), (247, 184, 204)),
    "토끼보호협회":  ((247, 184, 204), (242, 254, 138)),
    "야한토끼":     ((255, 192, 203), (221, 160, 221))
}

def _clean_title(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = str(raw)
    s = html.unescape(s)
    s = re.sub(r"<\s*img\b[^>]*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"</?[^>]+>", "", s)
    s = s.replace("\u200b", "").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s
    
def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

@lru_cache(maxsize=64)
def _rounded_rect(size: Tuple[int, int], radius: int, color: Tuple[int, int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w, h], radius=radius, fill=color)
    return img

def _to_int(v) -> int:
    try:
        if v is None: return 0
        if isinstance(v, (int, float)): return int(v)
        s = str(v).strip().replace(",", "")
        return int(float(s))
    except Exception:
        return 0

def _get_frame_by_expedition(exp_lv_any) -> str:
    exp_lv = _to_int(exp_lv_any)
    if exp_lv < 100:
        return "bronze.png"
    elif exp_lv < 200:
        return "silver.png"
    elif exp_lv < 300:
        return "gold.png"
    elif exp_lv < 400:
        return "platinum.png"
    else:
        return "gosu.png"

def get_guild_gradient(text: str | None) -> tuple[tuple[int,int,int], tuple[int,int,int]] | None:
    if not text:
        return None
    t = text.strip()
    if not t or t == "-":
        return None
    
    if t in SPECIAL_GRADIENTS:
        return SPECIAL_GRADIENTS[t]
    for key, grad in SPECIAL_GRADIENTS.items():
        if key in t:
            return grad
    return None
    
def _draw_text_gradient(img: Image.Image, xy: Tuple[int, int], text: str, font: ImageFont.FreeTypeFont,
                        start_rgb: Tuple[int, int, int], end_rgb: Tuple[int, int, int]) -> None:
    if not text:
        return
    d = ImageDraw.Draw(img)
    tx0, ty0, tx1, ty1 = d.textbbox((0, 0), text, font=font)
    w, h = tx1 - tx0, ty1 - ty0
    if w <= 0 or h <= 0:
        return

    # 텍스트 마스크 (안티앨리어싱 포함)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.text((-tx0, -ty0), text, font=font, fill=255)

    # 좌→우 RGB 그라데이션
    grad = Image.new("RGBA", (w, h))
    gd = ImageDraw.Draw(grad)
    sr, sg, sb = start_rgb
    er, eg, eb = end_rgb
    denom = max(w - 1, 1)
    for x in range(w):
        t = x / denom
        r = int(sr + (er - sr) * t)
        g = int(sg + (eg - sg) * t)
        b = int(sb + (eb - sb) * t)
        gd.line([(x, 0), (x, h)], fill=(r, g, b, 255))

    # 마스크를 알파로 적용 후 합성
    grad.putalpha(mask)
    x, y = xy
    img.alpha_composite(grad, (int(x + tx0), int(y + ty0)))

def _pick_bg_specs(class_name: str) -> Tuple[Tuple[int,int], Tuple[int,int]]:
    return _CLASS_BG_PLACEMENTS.get(class_name, _CLASS_BG_PLACEMENTS["_default"])

def _apply_scale(size_xy: Tuple[int,int]) -> Tuple[int,int]:
    return (size_xy[0]*RESIZEING, size_xy[1]*RESIZEING)

def render_new_card(
    server_name: str,
    nickname: str,
    class_name: str,
    class_job: str,
    title: str,
    item_level_value: str,
    expedition_level_value: str,
    honor_point_value: str,
    combat_power_value: str,
    pvp_value: str,
    guild_name: str,
    character_image: Optional[Image.Image] = None,
    special: bool = False,
    class_deco_bg: Optional[Image.Image] = None,
    is_mokoko: bool = False
) -> BytesIO:
    img = Image.new("RGBA", (CARD_W, CARD_H), CARD_BG)

    if character_image is not None:
        if character_image.mode != "RGBA":
            character_image = character_image.convert("RGBA")
        (w, h), (x, y) = _pick_bg_specs(class_name)
        rw, rh = _apply_scale((w, h))
        rx, ry = _apply_scale((x, y))
        fg = character_image.resize((rw, rh), Image.LANCZOS)
        img.alpha_composite(fg, (rx, ry))

    if class_deco_bg is not None:
        deco = class_deco_bg
        if deco.mode != "RGBA":
            deco = deco.convert("RGBA")
        dw, dh = 38 * RESIZEING, 32 * RESIZEING
        dx, dy = 110 * RESIZEING, 7 * RESIZEING
        deco = deco.resize((dw, dh), Image.LANCZOS)
        img.alpha_composite(deco, (dx, dy))

    clean_title = _clean_title(title)

    if "심연의 군주" in clean_title:
        frame_path = BASE_DIR / "border" / _get_frame_by_expedition(expedition_level_value)
    else:
        if special:
            frame_path = BASE_DIR / "border" / "mococo.png"
            if not frame_path.exists():
                frame_path = BASE_DIR / "border" / _get_frame_by_expedition(expedition_level_value)
        else:
            frame_path = BASE_DIR / "border" / _get_frame_by_expedition(expedition_level_value)

    if frame_path.exists():
        frame = _get_frame_image(str(frame_path))
        img.alpha_composite(frame, (0, 0))

    icon_file = BASE_DIR / "class_symbol" / f"{class_name}.png"
    if icon_file.exists():
        raw_icon = _get_raw_image(str(icon_file))
        iw, ih = 16 * RESIZEING, 16 * RESIZEING
        if class_deco_bg is not None:
            ix, iy = 121 * RESIZEING, 15 * RESIZEING
        else:
            ix, iy = 127 * RESIZEING, 13 * RESIZEING
        icon = raw_icon.resize((iw, ih), Image.LANCZOS)
        img.alpha_composite(icon, (ix, iy))

    d = ImageDraw.Draw(img)
    font7   = _load_font(FONT_REGULAR, 7*RESIZEING)
    font10b = _load_font(FONT_BOLD, 10*RESIZEING)
    font5b  = _load_font(FONT_BOLD, 5*RESIZEING)

    pill_x = 13 * RESIZEING
    pill_y = 13 * RESIZEING
    pad_x  = 3 * RESIZEING
    pad_y  = 2 * RESIZEING
    pill_r = 2 * RESIZEING

    server_text = server_name or ""
    tx0, ty0, tx1, ty1 = d.textbbox((0, 0), server_text, font=font5b)
    text_w = tx1 - tx0
    text_h = ty1 - ty0

    pill_w = text_w + pad_x * 2
    pill_h = text_h + pad_y * 2
    pill_img = _rounded_rect((pill_w, pill_h), pill_r, (0x23, 0x27, 0x30, 255))
    img.alpha_composite(pill_img, (pill_x, pill_y))

    d.text((13*RESIZEING, 137*RESIZEING), clean_title, font=font7, fill="white")

    nickname_xy = (13*RESIZEING, 149*RESIZEING)
    n_tx0, n_ty0, n_tx1, n_ty1 = d.textbbox((0, 0), nickname, font=font10b)
    n_text_w = n_tx1 - n_tx0
    n_text_h = n_ty1 - n_ty0
    if class_deco_bg is not None:
        _draw_text_gradient(img, nickname_xy, nickname, font10b, (0x2f, 0xab, 0xa8), (0x2f, 0xab, 0xa8))
    elif is_mokoko:
        _draw_text_gradient(img, nickname_xy, nickname, font10b, (0x90, 0xe1, 0xd0), (0x59, 0xb2, 0x5d))
        icon_path = BASE_DIR / "icons" / "mokoko.png"
        if icon_path.exists():
            iw = ih = 10 * RESIZEING
            ix = nickname_xy[0] + n_text_w + (2 * RESIZEING)
            iy = nickname_xy[1] + n_ty0 + max(0, (n_text_h - ih) // 2)
            raw_badge = _get_raw_image(str(icon_path))
            badge = raw_badge.resize((iw, ih), Image.LANCZOS)
            img.alpha_composite(badge, (ix, iy))
    else:
        d.text(nickname_xy, nickname, font=font10b, fill="white")

    d.text((pill_x + pad_x, pill_y + pad_y - ty0), server_text, font=font5b, fill="white")
    class_line = f"{class_name}" + (f" | {class_job}" if class_job else "")
    d.text((13*RESIZEING, 165*RESIZEING), class_line, font=font7, fill="white")

    pos  = (45*RESIZEING, 186*RESIZEING)
    text = (guild_name or "-").strip()
    grad = get_guild_gradient(text)
    if grad:
        start_rgb, end_rgb = grad
        _draw_text_gradient(img, pos, text, font7, start_rgb, end_rgb)
    else:
        d.text(pos, text, font=font7, fill="white")

    d.text((45*RESIZEING, 198*RESIZEING), str(item_level_value or ""), font=font7, fill="white")
    d.text((45*RESIZEING, 210*RESIZEING), str(expedition_level_value or ""), font=font7, fill="white")

    _honor_val = _to_int(honor_point_value)
    if _honor_val >= 1000:
        _honor_icon = "honor_point_5.png"
    elif _honor_val >= 500:
        _honor_icon = "honor_point_4.png"
    elif _honor_val >= 300:
        _honor_icon = "honor_point_3.png"
    elif _honor_val >= 100:
        _honor_icon = "honor_point_2.png"
    else:
        _honor_icon = "honor_point_1.png"

    _iw = _ih = 12 * RESIZEING
    _ix = 133 * RESIZEING
    _iy = 163 * RESIZEING
    _icon_path = BASE_DIR / "icons" / _honor_icon
    if _icon_path.exists():
        raw_badge = _get_raw_image(str(_icon_path))
        _badge = raw_badge.resize((_iw, _ih), Image.LANCZOS)
        img.alpha_composite(_badge, (_ix, _iy))

    _honor_text = str(honor_point_value or "")
    _tx0, _ty0, _tx1, _ty1 = d.textbbox((0, 0), _honor_text, font=font7)
    _tw = _tx1 - _tx0
    _th = _ty1 - _ty0
    _right_edge = _ix - 1
    _text_x = _right_edge - _tw
    _text_y = _iy + (_ih - _th)//2 - _ty0
    d.text((_text_x, _text_y), _honor_text, font=font7, fill="white")
    d.text((109*RESIZEING, 198*RESIZEING), str(combat_power_value or ""), font=font7, fill="white")
    d.text((109*RESIZEING, 210*RESIZEING), str(pvp_value or ""), font=font7, fill="white")

    mask = _rounded_rect((CARD_W, CARD_H), CARD_RADIUS, (255, 255, 255, 255)).convert("L")
    img.putalpha(mask)
    buf = BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf