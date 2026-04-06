from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import datetime, math, os, re, urllib.request, urllib.error, ssl

BASE_DIR = Path(__file__).resolve().parent
FONTS_DIR = BASE_DIR / "fonts"
CLASS_DIR = BASE_DIR / "class_symbol"
EMOJI_DIR = BASE_DIR / "emoji"
BACKGROUND_DIR = BASE_DIR / "background"
LOGO_FILE = BASE_DIR / "logo.png"
SUPPORTER_BADGE = BASE_DIR / "supporter.png"
EMOJI_DIR.mkdir(parents=True, exist_ok=True)

_FONT_MAP = {"Regular": "Pretendard-Regular.otf", "Bold": "Pretendard-Bold.otf"}
def _font_path(weight: str = "Regular") -> Path:
    p = FONTS_DIR / _FONT_MAP.get(weight, _FONT_MAP["Regular"])
    if not p.exists():
        raise RuntimeError(f"Font not found: {p}")
    return p
def F(size: int, weight: str = "Regular"):
    return ImageFont.truetype(str(_font_path(weight)), size)
def text_h(font: ImageFont.FreeTypeFont) -> int:
    b = font.getbbox("Hg")
    return b[3]-b[1]

_EMOJI_RE = re.compile(r'^<a?:[^:]+:(\d{5,})>$')
_EMOJI_TOKEN_RE = re.compile(r'<a?:[^:>]+:(\d{5,})>')
def _first_emoji_token(s: str):
    if not s or not isinstance(s, str):
        return None
    m = _EMOJI_TOKEN_RE.search(s.strip())
    return m.group(0) if m else None
def _parse_discord_emoji(s: str):
    if not s or not isinstance(s, str): return None, False
    animated = s.startswith("<a:")
    m = _EMOJI_RE.match(s.strip())
    if not m: return None, False
    return m.group(1), animated

def _http_get(url: str, timeout: float = 8.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MococoRenderer/1.0)"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except Exception:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

def _ensure_emoji_downloaded(eid: str, animated: bool) -> Optional[Path]:
    ext = "gif" if animated else "png"
    local = EMOJI_DIR / f"{eid}.{ext}"
    try:
        url = f"https://cdn.discordapp.com/emojis/{eid}.{ext}"
        data = _http_get(url, timeout=10.0)
        with open(local, "wb") as f:
            f.write(data)
        return local
    except Exception:
        return None

def _resolve_emoji_path(eid: str, animated: bool) -> Optional[Path]:
    ext = "gif" if animated else "png"
    local = EMOJI_DIR / f"{eid}.{ext}"
    if local.exists(): return local
    return _ensure_emoji_downloaded(eid, animated)

def _hex_to_rgba(s: str) -> tuple:
    s = s.strip().lstrip("#")
    if len(s)==6: r,g,b,a = int(s[0:2],16), int(s[2:4],16), int(s[4:6],16), 255
    elif len(s)==8: r,g,b,a = int(s[0:2],16), int(s[2:4],16), int(s[4:6],16), int(s[6:8],16)
    else: r,g,b,a = 0,0,0,255
    return (r,g,b,a)

BG            = _hex_to_rgba("0d0e12ff")
TEXT          = _hex_to_rgba("ffffffff")
MUTED         = _hex_to_rgba("ffffffb3")
PILL1_FILL    = _hex_to_rgba("232730ff")
PILL1_TEXT    = _hex_to_rgba("ffffffff")
PILL2_FILL    = _hex_to_rgba("2d2210ff")
PILL2_TEXT    = _hex_to_rgba("ffd424ff")
SLOT_FILLED   = _hex_to_rgba("111318ff")
SLOT_EMPTY    = _hex_to_rgba("1b1f25ff")

def _parse_start_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except Exception:
        try:
            return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

def _format_korean_datetime(dt: datetime.datetime) -> str:
    try:
        import zoneinfo
        KST = zoneinfo.ZoneInfo("Asia/Seoul")
        if dt.tzinfo is None:
            kdt = dt
        else:
            kdt = dt.astimezone(KST)
    except Exception:
        kdt = dt
    y = kdt.year
    m = kdt.month
    d_ = kdt.day
    h24 = kdt.hour
    minute = kdt.minute
    ampm = "오전" if h24 < 12 else "오후"
    h12 = h24 % 12
    if h12 == 0:
        h12 = 12
    return f"{y:04d}. {m:02d}. {d_:02d} {ampm} {h12:02d}:{minute:02d}"

def class_icon_for(class_name: str) -> Optional[str]:
    if not class_name: return None
    p = CLASS_DIR / f"{class_name}.png"
    return str(p) if p.exists() else None

def render_mococo_card(payload: Dict[str, Any], out_path=None, width=600, header_bg_h=110):
    data = payload.get("data", payload)
    emojis_map = data.get("emojis") or {}

    title = str(data.get("title", ""))  # (1)(5) DB title 그대로
    boss_name = str(data.get("boss_name", data.get("raid_name","")))
    difficulty = str(data.get("difficulty",""))
    message = (data.get("message") or "") if isinstance(data, dict) else ""
    start_date_raw = data.get("start_date")
    dt = _parse_start_date(start_date_raw)
    if dt:
        prefix = _format_korean_datetime(dt)
        display_title = f"{prefix} - {message}" if message else prefix
    else:
        display_title = message or title


    _diff = difficulty.strip().lower()
    if _diff in ("노말","normal","노멀"):
        diff_fill = _hex_to_rgba("232730ff"); diff_text = _hex_to_rgba("ffffffff"); diff_label = "노말"
    elif _diff in ("하드","hard"):
        diff_fill = _hex_to_rgba("2d2210ff"); diff_text = _hex_to_rgba("ffd424ff"); diff_label = "하드"
    elif _diff in ("헬","hell"):
        diff_fill = _hex_to_rgba("3c1414ff"); diff_text = _hex_to_rgba("ff6969ff"); diff_label = "헬"
    elif _diff in ("더퍼스트","the first","thefirst","the_first","the-first","thefirst raid","first"):
        diff_fill = _hex_to_rgba("212d60ff"); diff_text = _hex_to_rgba("91a7ffff"); diff_label = "The FIRST"
    else:
        diff_fill = PILL2_FILL; diff_text = PILL2_TEXT; diff_label = difficulty

    parts = data.get("participants") or {}
    dealers = parts.get("dealers") or []
    supporters = parts.get("supporters") or []

    dealer_need = int(data.get("dealer", 0)) or len(dealers)
    supporter_need = int(data.get("supporter", 0)) or len(supporters)

    # 3딜러 == 1블록, 1서폿 == 1블록
    cand_blocks = max((dealer_need + 2)//3, supporter_need)
    blocks = 4 if cand_blocks > 2 else (2 if cand_blocks > 1 else 1)  # 1/2/4 스냅

    # 레이아웃 기본 규격 (디자인 유지)
    cols, row_h, row_gap, col_gap = 2, 62, 12, 20

    # 폰트 설정
    f_title = F(16, "Bold")
    f_pill  = F(12, "Bold")
    f_name  = F(14, "Bold")
    f_meta  = F(14, "Regular")
    f_sub   = F(12, "Regular")
    f_foot  = F(10, "Regular")

    grid_left = 28
    col_w = (width - 2*grid_left - col_gap)//cols
    grid_right = grid_left + 2*col_w + col_gap

    title_x, title_y = 28, 28
    pill_y = title_y + text_h(f_title) + 11
    pill_h, pill_gap = 24, 10

    subnote_y = header_bg_h + 20
    grid_top  = header_bg_h + 44

    # 블록 베이스(row 시작 인덱스) / (col, base_row)
    if blocks == 1:
        block_bases = [(0, 0)]  # 좌상 한 블록
    elif blocks == 2:
        block_bases = [(0, 0), (1, 0)]  # 좌상, 우상
    else:
        block_bases = [(0, 0), (1, 0), (0, 4), (1, 4)]  # 좌상, 우상, 좌하, 우하

    # 전체 행 수 (블록 당 4행)
    rows = 4 * (2 if blocks == 4 else 1)  # 1/2블록=4행, 4블록=8행

    # 그리드 계산은 rows 확정 후
    grid_h = rows*row_h + (rows-1)*row_gap
    grid_bottom = grid_top + grid_h

    logo_h_base = 22
    logo_h = int(round(logo_h_base * 0.95))
    height = max(grid_bottom + 22 + logo_h + 22, 496)

    # 이미지 캔버스
    im = Image.new("RGBA", (width, height), BG)
    d = ImageDraw.Draw(im)
    baseline_y = height - 22 - logo_h

    # 헤더 배경(레이드 일러)
    bg_key = str(data.get("raid_name", boss_name))
    bg_path = BACKGROUND_DIR / f"{bg_key}.png"
    if not bg_path.exists() and boss_name:
        bg_path = BACKGROUND_DIR / f"{boss_name}.png"
    if bg_path.exists():
        try:
            bg = Image.open(str(bg_path)).convert("RGBA")
            scale = header_bg_h / bg.height
            bg = bg.resize((int(bg.width*scale), header_bg_h), Image.Resampling.LANCZOS)
            im.alpha_composite(bg, (width - bg.width, 0))
            overlay = Image.new("RGBA", (width, header_bg_h), (0,0,0,0))
            ov = ImageDraw.Draw(overlay)
            steps = 24
            for i in range(steps):
                alpha = int(180 * (1 - i/(steps-1)))
                x1 = i * (overlay.width // steps)
                x2 = (i+1) * (overlay.width // steps)
                ov.rectangle([x1, 0, x2, header_bg_h], fill=(0,0,0,alpha))
            im.alpha_composite(overlay, (0,0))
        except Exception:
            pass

    # 타이틀 & 필
    d.text((title_x, title_y), display_title, font=f_title, fill=TEXT)  # 타이틀 (start_date/message 우선)

    pill1 = boss_name
    t1 = d.textlength(pill1, font=f_pill)
    p1_w = int(t1 + 16)
    p1 = (title_x, pill_y, title_x + p1_w, pill_y + pill_h)
    d.rounded_rectangle(p1, radius=4, fill=PILL1_FILL)
    d.text((p1[0] + (p1_w - t1)//2, pill_y + (pill_h - text_h(f_pill))//2), pill1, font=f_pill, fill=PILL1_TEXT)

    t2 = d.textlength(diff_label, font=f_pill)
    p2_w = int(t2 + 16)
    p2_x = p1[2] + pill_gap
    p2 = (p2_x, pill_y, p2_x + p2_w, pill_y + pill_h)
    d.rounded_rectangle(p2, radius=4, fill=diff_fill)
    d.text((p2[0] + (p2_w - t2)//2, pill_y + (pill_h - text_h(f_pill))//2), diff_label, font=f_pill, fill=diff_text)

    d.text((28, subnote_y), "*인게임 전투력 기준", font=f_sub, fill=MUTED)  # 95x14 전제

    # Grid 그리기 도우미
    def draw_slot(rect, player):
        x1,y1,x2,y2 = rect
        d.rounded_rectangle(rect, radius=6, fill=SLOT_FILLED if player else SLOT_EMPTY)
        ic_box = 48
        ic_x = x1 + 10
        ic_y = y1 + ((y2-y1) - ic_box)//2
        if player:
            ip = class_icon_for(str(player.get("class_name","")).strip())
            if ip and os.path.exists(ip):
                try:
                    icon = Image.open(ip).convert("RGBA")
                    scale = min(ic_box/icon.width, ic_box/icon.height)
                    icon = icon.resize((int(icon.width*scale), int(icon.height*scale)), Image.Resampling.LANCZOS)
                    im.alpha_composite(icon, (ic_x + (ic_box-icon.width)//2, ic_y + (ic_box-icon.height)//2))
                except Exception:
                    pass
            try:
                if player.get("__role") == "supporter" and SUPPORTER_BADGE.exists():
                    spt = Image.open(str(SUPPORTER_BADGE)).convert("RGBA")
                    bw = max(1, int(spt.width))
                    bh = max(1, int(spt.height))
                    if bh > ic_box:
                        scale = ic_box / bh
                        bw = int(bw * scale); bh = int(bh * scale)
                    spt = spt.resize((bw, bh), Image.Resampling.LANCZOS)
                    bx = ic_x
                    by = ic_y + ic_box - bh
                    im.alpha_composite(spt, (bx, by))
            except Exception:
                pass

        tx = ic_x + ic_box + 16
        ty = y1 + 12
        if player:
            name = str(player.get("name","-"))
            maxw = x2 - tx - 12
            ndraw = name
            while d.textlength(ndraw, font=f_name) > maxw and len(ndraw)>0:
                ndraw = ndraw[:-1]
            if ndraw != name and len(ndraw) > 2:
                ndraw = ndraw[:-2] + "…"
            d.text((tx, ty), ndraw, font=f_name, fill=TEXT)

            try:
                uid = str(player.get("user_id",""))
                raw = player.get("emoji") or emojis_map.get(uid, "")
                tok = _first_emoji_token(raw)
                if tok:
                    eid, animated = _parse_discord_emoji(tok)
                    if eid:
                        pth = _resolve_emoji_path(eid, animated)
                        if pth:
                            try:
                                em = Image.open(str(pth)).convert("RGBA")
                                target_h = 18
                                scale = target_h / em.height
                                ew = max(1, int(em.width * scale))
                                eh = max(1, int(em.height * scale))
                                ex = int(x2 - 13 - ew)
                                ey = int(y1 + 13)
                                em = em.resize((ew, eh), Image.Resampling.LANCZOS)
                                im.alpha_composite(em, (ex, ey))
                            except Exception:
                                pass
            except Exception:
                pass
            # meta
            try: ilv = float(player.get("item_level",0.0))
            except: ilv = 0.0
            try: cp = float(player.get("combat_power",0.0))
            except: cp = 0.0
            meta = f"Lv. {ilv:.2f}  |  {cp:.2f}"
            d.text((tx, ty + text_h(f_name) + 6), meta, font=f_meta, fill=MUTED)
        else:
            bt="공석"; tw = d.textlength(bt, font=f_name)
            d.text(((x1+x2)//2 - tw//2, (y1+y2)//2 - text_h(f_name)//2), bt, font=f_name, fill=MUTED)

    # 슬롯 좌표
    def slot_xy(col, row):
        x1 = grid_left + col * (col_w + col_gap)
        y1 = grid_top + row * (row_h + row_gap)
        x2 = x1 + col_w
        y2 = y1 + row_h
        return (x1, y1, x2, y2)

    # 블록 내 슬롯: 딜러(위 3칸), 서폿(맨 아래 1칸)
    dealer_slots: list[tuple[int,int]] = []
    supporter_slots: list[tuple[int,int]] = []
    for (bc, br) in block_bases:
        dealer_slots += [(bc, br + 0), (bc, br + 1), (bc, br + 2)]
        supporter_slots += [(bc, br + 3)]

    # 참가자 목록을 역할별로 태깅
    dealer_list = [dict(p, **{"__role": "dealer"}) for p in dealers]
    supporter_list = [dict(p, **{"__role": "supporter"}) for p in supporters]

    # (col,row) → player 매핑
    placed: Dict[tuple, dict] = {}

    # 블록 순서대로 딜러 채우기 (좌상→우상→좌하→우하)
    for pos, pl in zip(dealer_slots, dealer_list):
        placed[pos] = pl
    # 각 블록 맨 아래에 서포터 채우기
    for pos, pl in zip(supporter_slots, supporter_list):
        placed[pos] = pl

    # 그리기: 확정 rows/cols 루프
    for r in range(rows):
        for c in range(cols):
            rect = slot_xy(c, r)
            player = placed.get((c, r))
            draw_slot(rect, player)

    # 하단 스탬프/로고
    stamp = datetime.datetime.now().strftime("%y.%m.%d %H:%M")
    d.text((28, baseline_y), f"Image created by Mococo | {stamp}", font=f_foot, fill=MUTED)

    if LOGO_FILE.exists():
        try:
            lg = Image.open(str(LOGO_FILE)).convert("RGBA")
            h = logo_h
            scale = h / lg.height
            w = int(lg.width * scale)
            lg = lg.resize((w, h), Image.Resampling.LANCZOS)
            x = int(grid_right - w)
            y = int(baseline_y)
            im.alpha_composite(lg, (x, y))
        except Exception:
            pass

    if out_path: im.save(out_path, "PNG")
    return im
