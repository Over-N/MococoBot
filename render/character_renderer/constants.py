from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = BASE_DIR / "fonts"
CLASS_DIR = BASE_DIR / "class_symbol"
EMOJI_DIR = BASE_DIR / "emoji"
BACKGROUND_DIR = BASE_DIR / "background"
ICON_DIR = BASE_DIR / "icons"
CACHE_DIR = BASE_DIR / ".cache" / "icons"
LOGO_FILE = BASE_DIR / "logo.png"
SUPPORTER_BADGE = BASE_DIR / "supporter.png"

for p in [FONTS_DIR, CLASS_DIR, EMOJI_DIR, BACKGROUND_DIR, ICON_DIR, CACHE_DIR]:
    p.mkdir(parents=True, exist_ok=True)

@dataclass(frozen=True)
class Canvas:
    W: int = 943
    H: int = 702
    BG = (0x0D, 0x0E, 0x12, 255)
    WHITE = (255, 255, 255, 255)

@dataclass(frozen=True)
class Typo:
    SERVER_SIZE: int = 11
    NICK_SIZE: int = 18
    CLASS_SIZE: int = 11

@dataclass(frozen=True)
class Spacing:
    LEFT: int = 26
    TOP: int = 26
    GAP_SERVER_NICK: int = 7
    GAP_NICK_CLASS: int = 6

BOX_W = BOX_H = 47
BOX_RADIUS = 3
BOX_COLOR = (0xD9, 0xD9, 0xD9, 255)
BOX_GAP_Y = 8
BORDER_INSET = 2

ACCESSORY_OFFSET_X = 158
ACCESSORY_GAP_Y = BOX_GAP_Y
ACCESS_OPT_SIZE = 11
ACCESS_OPT_COLOR = Canvas.WHITE
ACCESS_OPT_X_GAP = 9
ACCESS_OPT_Y_FROM_ICON_TOP = 3
ACCESS_OPT_LINE_GAP = 4
ACCESS_DIAMOND_HALF = 4

ORANGE = (0xE5, 0x9C, 0x35, 255)
TITLE_SIZE = 12
UPGRADE_SIZE = 11
TRANSCEND_ICON_SIZE = 14
LINE_GAP_BELOW_TITLE = 6
TEXT_RIGHT_GAP_FROM_ICON = 9
TEXT_GAP_FROM_TICON = 5

ELIXIR_SIZE = 9
PILL_BG = (0x23, 0x27, 0x30, 255)
PILL_RADIUS = 3
PILL_PAD = (2, 2, 2, 2)
PILL_H_GAP = 3

BADGE_W, BADGE_H = 24, 16
BADGE_RADIUS = 3

ORDER = ["투구", "견갑", "상의", "하의", "장갑", "무기", "보주"]
TYPE_ALIAS = {"어깨": "견갑"}

ACCESSORY_ORDER = [
    ("목걸이", 1), ("귀걸이", 2), ("반지", 2), ("어빌리티 스톤", 1)
]
ACCESSORY_TYPE_ALIAS = {
    "어빌리티스톤": "어빌리티 스톤",
    "어빌리티  스톤": "어빌리티 스톤",
    "귀 고리": "귀걸이",
    "반 지": "반지",
}

GRADE_GRADIENTS = {
    "에스더": ((0x0c, 0x2e, 0x2c), (0x2f, 0xab, 0xa8)),
    "고대": ((0x40, 0x35, 0x27), (0xd2, 0xbf, 0x91)),
    "유물": ((0x3b, 0x13, 0x03), (0xa2, 0x34, 0x05)),
    "전설": ((0x3c, 0x22, 0x01), (0xa8, 0x62, 0x00)),
    "영웅": ((0x27, 0x01, 0x3d), (0x6e, 0x00, 0xaa)),
    "희귀": ((0x11, 0x1d, 0x29), (0x10, 0x35, 0x50)),
    "고급": ((0x1a, 0x23, 0x0e), (0x37, 0x4e, 0x18)),
}

SPECIAL_ARK = {"절실한 구원", "만개", "축복의 오라", "해방자"}

__all__ = [
    "Canvas",
    "Typo",
    "Spacing",
    "BASE_DIR",
    "FONTS_DIR",
    "CLASS_DIR",
    "EMOJI_DIR",
    "BACKGROUND_DIR",
    "LOGO_FILE",
    "SUPPORTER_BADGE",
    "ICON_DIR",
    "CACHE_DIR",
    "BOX_W",
    "BOX_H",
    "BOX_RADIUS",
    "BOX_COLOR",
    "BOX_GAP_Y",
    "BORDER_INSET",
    "ORANGE",
    "TITLE_SIZE",
    "UPGRADE_SIZE",
    "TRANSCEND_ICON_SIZE",
    "LINE_GAP_BELOW_TITLE",
    "TEXT_RIGHT_GAP_FROM_ICON",
    "TEXT_GAP_FROM_TICON",
    "ELIXIR_SIZE",
    "PILL_BG",
    "PILL_RADIUS",
    "PILL_PAD",
    "PILL_H_GAP",
    "BADGE_W",
    "BADGE_H",
    "BADGE_RADIUS",
    "ACCESSORY_OFFSET_X",
    "ACCESSORY_GAP_Y",
    "ACCESS_OPT_SIZE",
    "ACCESS_OPT_COLOR",
    "ACCESS_OPT_X_GAP",
    "ACCESS_OPT_Y_FROM_ICON_TOP",
    "ACCESS_OPT_LINE_GAP",
    "ACCESS_DIAMOND_HALF",
    "ORDER",
    "TYPE_ALIAS",
    "ACCESSORY_ORDER",
    "ACCESSORY_TYPE_ALIAS",
    "GRADE_GRADIENTS",
    "SPECIAL_ARK",
]