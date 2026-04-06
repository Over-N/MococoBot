from __future__ import annotations

import html
import json
import os
import re
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont, ImageChops

from .constants import (
    FONTS_DIR,
    ICON_DIR,
    CACHE_DIR,
    Canvas,
    GRADE_GRADIENTS,
    ELIXIR_SIZE,
    PILL_BG,
    PILL_RADIUS,
    PILL_PAD,
)

__all__ = [
    "load_font",
    "ellipsis",
    "strip_tags",
    "parse_tooltip_json",
    "norm_type",
    "rounded_mask",
    "make_linear_gradient",
    "load_icon_15",
    "fetch_icon",
    "compute_elixir_total_and_option",
    "compute_transcend_total",
    "iter_tooltip_strings",
    "get_collect_point",
    "parse_discord_emoji",
    "resolve_emoji_path",
]

@lru_cache(maxsize=64)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()

def ellipsis(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """Shorten text with an ellipsis if it exceeds `max_width` pixels."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    while text and draw.textlength(text + ell, font=font) > max_width:
        text = text[:-1]
    return text + ell if text else ell

def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "").replace("&nbsp;", " ")
    return html.unescape(s)

def parse_tooltip_json(item: dict) -> dict:
    raw = item.get("Tooltip")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def norm_type(s: str) -> str:
    from .constants import ACCESSORY_TYPE_ALIAS
    s = (s or "").strip()
    return ACCESSORY_TYPE_ALIAS.get(s, s)

@lru_cache(maxsize=256)
def rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    """Create a reusable mask with rounded corners for alpha compositing."""
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    return m

@lru_cache(maxsize=256)
def make_linear_gradient(w: int, h: int, c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    mask = Image.new("L", (w, h))
    for y in range(h):
        for x in range(w):
            t = (x + y) / (w + h - 2)
            mask.putpixel((x, y), int(255 * t))
    return Image.composite(top, base, mask)

@lru_cache(maxsize=64)
def load_icon_15(name: str) -> Image.Image:
    path = os.path.join(ICON_DIR, name)
    try:
        im = Image.open(path).convert("RGBA")
        if im.size != (15, 15):
            im = im.resize((15, 15), Image.LANCZOS)
        return im
    except Exception:
        return Image.new("RGBA", (15, 15), (0, 0, 0, 0))

@lru_cache(maxsize=256)
def fetch_icon(url: str) -> Image.Image:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    path = os.path.join(CACHE_DIR, f"{h}.png")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            pass
    import httpx
    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        return Image.open(Path(path)).convert("RGBA")

def iter_tooltip_strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        t = node.get("type") if isinstance(node, dict) else None
        if isinstance(t, str) and t in ("NameTagBox", "ItemTitle"):
            return
        for v in node.values():
            yield from iter_tooltip_strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from iter_tooltip_strings(v)

def get_collect_point(payload: Optional[dict], type_name: str) -> str:
    for c in (payload or {}).get("Collectibles", []) or []:
        if (c or {}).get("Type") == type_name:
            return str((c or {}).get("Point", "-"))
    return "-"

import urllib.request
import ssl

def parse_discord_emoji(token: str) -> Tuple[Optional[str], bool]:
    if not token or not isinstance(token, str):
        return None, False
    token = token.strip()
    animated = token.startswith("<a:")
    m = re.match(r"<a?:[^:>]+:(\d{5,})>", token)
    if not m:
        return None, False
    return m.group(1), animated

def _download_emoji_file(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MococoRenderer/1.0)"
        })
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10.0, context=ctx) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False

def _ensure_emoji_downloaded(eid: str, animated: bool) -> Optional[Path]:
    from .constants import EMOJI_DIR
    primary_ext = "gif" if animated else "png"
    secondary_ext = "png" if animated else None
    for ext in filter(None, [primary_ext, secondary_ext]):
        local = EMOJI_DIR / f"{eid}.{ext}"
        if local.exists():
            return local
        url = f"https://cdn.discordapp.com/emojis/{eid}.{ext}"
        if _download_emoji_file(url, local):
            return local
    return None

def resolve_emoji_path(eid: str, animated: bool) -> Optional[Path]:
    from .constants import EMOJI_DIR
    ext = "gif" if animated else "png"
    local = EMOJI_DIR / f"{eid}.{ext}"
    if local.exists():
        return local
    return _ensure_emoji_downloaded(eid, animated)

def compute_elixir_total_and_option(equipments: Iterable[dict]) -> Tuple[int, str, int]:
    from .bracelet import extract_elixir_lines, extract_elixir_option_stage
    total = 0
    opt_name = ""
    opt_stage = 0
    for it in equipments or []:
        for lab in extract_elixir_lines(it):
            m = re.search(r"(\d+)$", lab)
            if m:
                total += int(m.group(1))
        name, stage = extract_elixir_option_stage(it)
        if stage > opt_stage and name:
            opt_name, opt_stage = name, stage
    return total, opt_name, opt_stage

def compute_transcend_total(equipments: Iterable[dict]) -> int:
    from .bracelet import extract_transcend
    total = 0
    for it in equipments or []:
        _, cnt = extract_transcend(it)
        if cnt and str(cnt).isdigit():
            total += int(cnt)
    return total