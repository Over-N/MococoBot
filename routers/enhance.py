from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel

from database.connection import get_db

UTC = timezone.utc
router = APIRouter()


class EnhanceAttempt(BaseModel):
    guild_id: int
    user_id: int
    username: Optional[str] = None


class ResetPayload(BaseModel):
    guild_id: int
    user_id: Optional[int] = None
    hard: bool = False


class EstherBindPayload(BaseModel):
    guild_id: int
    user_id: int
    esther: str


class AncestralSelectPayload(BaseModel):
    guild_id: int
    user_id: int
    key: str


COOLDOWN = timedelta(minutes=1)
EXTRA_PENALTY = timedelta(minutes=10)
MAX_LEVEL = 37
ESTHER_LABEL = "에스더"

GAHO_THRESHOLD_NORMAL = 6
GAHO_THRESHOLD_PLUS = 5

CHARITY_BONUS = 3.0
CHARITY_USES_PER_PROC = 3

GAHO_KEYS = (
    "cooldown_plus_10m",
    "level_plus_1",
    "level_minus_1",
    "pity_plus_20",
    "safe_down",
    "safe_destroy",
    "level_plus_2",
    "level_minus_2",
    "gaho_plus_3",
    "gaho_upgrade",
    "dice",
    "charity",
)
GAHO_WEIGHTS_VALUES = (10.0, 10.0, 10.0, 17.0, 7.0, 5.0, 2.5, 2.5, 10.0, 9.0, 8.0, 9.0)

GAHO_PLUS_KEYS = (
    "plus1_and_nextstack2",
    "plus2_and_nextstack2",
    "plus4",
    "safe_destroy_x2",
    "safe_down_x2",
    "nextstack5_and_immediate_retry",
    "three_runs_protect",
    "destroy_becomes_plus1_stack",
)
GAHO_PLUS_WEIGHTS_VALUES = (15.0, 10.0, 1.0, 10.0, 10.0, 20.0, 31.5, 2.5)

ESTHER_EX_LABELS: Dict[int, str] = {
    26: "에스더",
    27: "에스더 + 1",
    28: "에스더 + 2",
    29: "에스더 + 3",
    30: "에스더 + 4",
    31: "에스더 + 5",
    32: "에스더 + 6",
    33: "에스더 + 6 (엘라)",
    34: "에스더 + 7 (엘라)",
    35: "에스더 + 8 (엘라)",
    36: "에스더 + 8 (엘라 × 2)",
    37: "에스더 + 9 (엘라 × 2)",
}

RATES: Dict[int, Dict[str, float]] = {
    1: {"succ": 80.0, "des": 0.0, "down": 0.0, "pity": 12.0},
    2: {"succ": 75.0, "des": 0.0, "down": 0.0, "pity": 12.0},
    3: {"succ": 65.0, "des": 0.0, "down": 1.0, "pity": 7.0},
    4: {"succ": 50.0, "des": 0.0, "down": 1.0, "pity": 7.0},
    5: {"succ": 40.0, "des": 0.0, "down": 2.0, "pity": 7.0},
    6: {"succ": 30.0, "des": 0.0, "down": 2.0, "pity": 7.0},
    7: {"succ": 20.0, "des": 0.0, "down": 2.0, "pity": 7.0},
    8: {"succ": 18.0, "des": 0.0, "down": 3.0, "pity": 7.0},
    9: {"succ": 15.0, "des": 0.0, "down": 3.0, "pity": 7.0},
    10: {"succ": 14.0, "des": 0.5, "down": 3.5, "pity": 4.5},
    11: {"succ": 12.0, "des": 0.5, "down": 3.5, "pity": 4.5},
    12: {"succ": 10.0, "des": 0.5, "down": 3.5, "pity": 4.5},
    13: {"succ": 8.5, "des": 0.5, "down": 4.0, "pity": 4.5},
    14: {"succ": 8.5, "des": 0.5, "down": 4.0, "pity": 4.5},
    15: {"succ": 7.0, "des": 0.5, "down": 4.5, "pity": 4.0},
    16: {"succ": 6.0, "des": 0.5, "down": 5.0, "pity": 4.0},
    17: {"succ": 5.0, "des": 0.5, "down": 5.0, "pity": 4.0},
    18: {"succ": 4.5, "des": 0.5, "down": 5.0, "pity": 4.0},
    19: {"succ": 3.0, "des": 0.5, "down": 5.5, "pity": 4.0},
    20: {"succ": 2.5, "des": 0.5, "down": 5.5, "pity": 3.5},
    21: {"succ": 2.0, "des": 1.0, "down": 6.0, "pity": 3.5},
    22: {"succ": 1.8, "des": 1.0, "down": 6.0, "pity": 3.5},
    23: {"succ": 1.2, "des": 1.0, "down": 6.0, "pity": 3.5},
    24: {"succ": 1.0, "des": 1.0, "down": 6.5, "pity": 3.5},
    25: {"succ": 0.5, "des": 1.0, "down": 6.5, "pity": 3.0},
    26: {"succ": 2.0, "des": 1.2, "down": 8.0, "pity": 3.0},
    27: {"succ": 1.8, "des": 1.4, "down": 8.0, "pity": 3.0},
    28: {"succ": 1.6, "des": 1.6, "down": 9.0, "pity": 3.0},
    29: {"succ": 1.5, "des": 1.8, "down": 9.0, "pity": 3.0},
    30: {"succ": 1.3, "des": 2.0, "down": 10.0, "pity": 3.0},
    31: {"succ": 1.2, "des": 2.5, "down": 10.0, "pity": 3.0},
    32: {"succ": 1.1, "des": 3.0, "down": 12.0, "pity": 3.0},
    33: {"succ": 1.0, "des": 3.5, "down": 12.0, "pity": 3.0},
    34: {"succ": 0.9, "des": 4.0, "down": 14.0, "pity": 3.0},
    35: {"succ": 0.8, "des": 5.0, "down": 14.0, "pity": 3.0},
    36: {"succ": 0.5, "des": 5.0, "down": 15.0, "pity": 3.0},
    37: {"succ": 0.5, "des": 5.0, "down": 15.0, "pity": 1.0},
}

ESTHER_KEYS = [
    "silian",
    "bahunture",
    "ninave",
    "kadan",
    "shandi",
    "wei",
    "azeina",
]
ESTHER_DISPLAY_NAMES = {
    "silian": "패자의 검",
    "bahunture": "피요르긴",
    "ninave": "파르나쿠스",
    "kadan": "나히니르",
    "shandi": "진멸",
    "wei": "도철",
    "azeina": "태초의 창",
}
ESTHER_FULL_NAMES = {
    "silian": "패자의 검 (실리안)",
    "bahunture": "피요르긴 (바훈투르)",
    "ninave": "파르나쿠스 (니나브)",
    "kadan": "나히니르 (카단)",
    "shandi": "진멸 (샨디)",
    "wei": "도철 (웨이)",
    "azeina": "태초의 창 (아제나&이난나)",
}
ESTHER_CHAR_NAMES = {
    "silian": "실리안",
    "bahunture": "바훈투르",
    "ninave": "니나브",
    "kadan": "카단",
    "shandi": "샨디",
    "wei": "웨이",
    "azeina": "아제나&이난나",
}
ESTHER_ABILITIES = {
    "silian": "일기토 성공/하락 80%/20% · 3연성공 시 추가 +1강",
    "bahunture": "강화 시 자동 선조의 가호 발동(20%)",
    "ninave": "가호 스택 최대 8, 일부 보상 2배",
    "kadan": "가호 임계치 하향",
    "shandi": "강화 쿨타임 30초 감소, 10분 쿨증 옵션 제거",
    "wei": "성공률 2배, 파괴 1/3",
    "azeina": "하락 확률 1/2, 하락 옵션 제거",
}

ANCESTRAL_KEYS = ("luteran", "galatur", "sien")
ANCESTRAL_LABEL = {"luteran": "루테란", "galatur": "갈라투르", "sien": "시엔"}
ANCESTRAL_WARNING = "선대 에스더의 가호를 최초 선택하면 일반 에스더 결속이 모두 해제됩니다."


@dataclass
class EnhanceState:
    guild_id: int
    user_id: int
    username: str
    level: int
    pity: float
    last_attempt: Optional[datetime]
    gaho_count: int
    gaho_ready: int
    cooldown_penalty_until: Optional[datetime]
    gaho_extra_try: int
    gaho_shield: int
    gaho_down_shield: int
    gaho_upgrade_pending: int
    tok_three_runs_left: int
    tok_destroy_becomes_plus1: int
    tok_dice_pending: int
    tok_duel_successes: int
    esther_list: List[str]
    ancestral_list: List[str]
    ancestral_started: int

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "EnhanceState":
        return cls(
            guild_id=row.get("guild_id"),
            user_id=row.get("user_id"),
            username=row.get("username") or "",
            level=int(row.get("level") or 1),
            pity=float(row.get("pity") or 0.0),
            last_attempt=_to_utc(row.get("last_attempt")),
            gaho_count=int(row.get("gaho_count") or 0),
            gaho_ready=int(row.get("gaho_ready") or 0),
            cooldown_penalty_until=_to_utc(row.get("cooldown_penalty_until")),
            gaho_extra_try=int(row.get("gaho_extra_try") or 0),
            gaho_shield=int(row.get("gaho_shield") or 0),
            gaho_down_shield=int(row.get("gaho_down_shield") or 0),
            gaho_upgrade_pending=int(row.get("gaho_upgrade_pending") or 0),
            tok_three_runs_left=int(row.get("tok_three_runs_left") or 0),
            tok_destroy_becomes_plus1=int(row.get("tok_destroy_becomes_plus1") or 0),
            tok_dice_pending=int(row.get("tok_dice_pending") or 0),
            tok_duel_successes=int(row.get("tok_duel_successes") or 0),
            esther_list=[x for x in _parse_esthers(row.get("esther_bindings"))],
            ancestral_list=[x for x in (_parse_list_json(row.get("ancestral_blessings")) or []) if x in ANCESTRAL_KEYS],
            ancestral_started=int(row.get("ancestral_started") or 0),
        )

    def to_db_tuple(self) -> Tuple:
        return (
            self.level,
            self.pity,
            self.last_attempt,
            self.username[:100] if self.username else None,
            self.gaho_count,
            self.gaho_ready,
            self.gaho_extra_try,
            self.gaho_shield,
            self.gaho_down_shield,
            self.gaho_upgrade_pending,
            self.tok_three_runs_left,
            self.tok_destroy_becomes_plus1,
            self.tok_dice_pending,
            self.tok_duel_successes,
            self.guild_id,
            self.user_id,
        )


def _level_label(level: int) -> str:
    lv = int(level) if isinstance(level, (int, float)) else 1
    if lv <= 25:
        return f"{lv}강"
    return ESTHER_EX_LABELS.get(lv, ESTHER_EX_LABELS[37])


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _to_utc(dt: Optional[datetime | str]) -> Optional[datetime]:
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    try:
        d = dt.replace("Z", "+00:00")
        obj = datetime.fromisoformat(d)
    except Exception:
        return None
    return obj if obj.tzinfo else obj.replace(tzinfo=UTC)


def _parse_list_json(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass
    return []


def _parse_esthers(raw: Any) -> List[str]:
    vals = _parse_list_json(raw)
    return [x for x in vals if x in ESTHER_KEYS]


def _serialize_esthers(esthers: List[str]) -> str:
    return json.dumps(list(dict.fromkeys(esthers)))


def _resolve_esther_key(s: str) -> Optional[str]:
    s = (s or "").strip().lower()
    if s in ESTHER_KEYS:
        return s
    for k in ESTHER_KEYS:
        if s in {
            ESTHER_DISPLAY_NAMES[k].lower(),
            ESTHER_FULL_NAMES[k].lower(),
            ESTHER_CHAR_NAMES[k].lower(),
        }:
            return k
    return None


def _effective_cooldown_timedelta(esthers: List[str]) -> timedelta:
    if "shandi" in esthers:
        delta = COOLDOWN - timedelta(seconds=30)
        return delta if delta.total_seconds() > 0 else timedelta(seconds=0)
    return COOLDOWN


def _cooldown_until(last_attempt: Optional[datetime | str], penalty_until: Optional[datetime | str], esthers: Optional[List[str]] = None) -> Optional[datetime]:
    la = _to_utc(last_attempt)
    pu = _to_utc(penalty_until)
    cd = _effective_cooldown_timedelta(esthers or [])
    next_cd = la + cd if la else None
    if next_cd and pu:
        return next_cd if next_cd > pu else pu
    return next_cd or pu


def _cooldown_remain(last_attempt: Optional[datetime | str], penalty_until: Optional[datetime | str], esthers: Optional[List[str]] = None) -> int:
    until = _cooldown_until(last_attempt, penalty_until, esthers or [])
    if not until:
        return 0
    remain = until - _now_utc()
    sec = int(remain.total_seconds())
    return sec if sec > 0 else 0


@lru_cache(maxsize=None)
def _rates_for_level(level: int) -> Dict[str, float]:
    base = RATES.get(level, {"succ": 1.0, "des": 5.0, "down": 10.0, "pity": 2.0})
    succ = base["succ"]
    des = base["des"]
    down = base["down"] * (2.0 / 3.0)
    fail = 100.0 - (succ + des + down)
    if fail < 0.0:
        fail = 0.0
    return {"success": succ, "destroy": des, "down": max(down, 0.0), "fail": fail, "pity_gain": base["pity"]}


def _effective_rates(level: int, pity: float, esthers: List[str]) -> Dict[str, float]:
    if level >= MAX_LEVEL:
        return {"success": 0.0, "destroy": 0.0, "down": 0.0, "fail": 0.0, "pity_gain": 0.0}
    if pity >= 100.0:
        return {"success": 100.0, "destroy": 0.0, "down": 0.0, "fail": 0.0, "pity_gain": 0.0}
    rates = _rates_for_level(level)
    succ = rates["success"]
    des = rates["destroy"]
    down = rates["down"]
    pity_gain = rates["pity_gain"]
    if "wei" in esthers:
        succ = min(succ * 2.0, 100.0)
        des = des / 3.0
    if "azeina" in esthers:
        down = down / 2.0
    fail = 100.0 - (succ + des + down)
    if fail < 0.0:
        fail = 0.0
    return {"success": succ, "destroy": des, "down": down, "fail": fail, "pity_gain": pity_gain}


def _calc_thresholds(esthers: List[str]) -> Tuple[int, int]:
    ninave_bound = "ninave" in esthers
    kadan_bound = "kadan" in esthers
    if ninave_bound and kadan_bound:
        return (5, 5)
    if ninave_bound:
        return (8, 7)
    if kadan_bound:
        return (4, 3)
    return (GAHO_THRESHOLD_NORMAL, GAHO_THRESHOLD_PLUS)


def _current_cap(esthers: List[str], upgrade_pending: bool) -> int:
    normal_cap, plus_cap = _calc_thresholds(esthers)
    return plus_cap if upgrade_pending else normal_cap


def _has_all_esthers(esthers: List[str]) -> bool:
    return all(k in esthers for k in ESTHER_KEYS)


def _ancestral_available(st: EnhanceState) -> List[str]:
    if st.level < MAX_LEVEL:
        return []
    if not _has_all_esthers(st.esther_list):
        return []
    owned = st.ancestral_list or []
    return [k for k in ANCESTRAL_KEYS if k not in owned]


def _ancestral_payload(st: EnhanceState) -> Dict[str, Any]:
    owned = st.ancestral_list or []
    avail = _ancestral_available(st)
    return {
        "owned": [ANCESTRAL_LABEL[k] for k in owned],
        "owned_keys": owned,
        "available": [ANCESTRAL_LABEL[k] for k in avail],
        "available_keys": avail,
        "started": bool(st.ancestral_started),
        "warning": ANCESTRAL_WARNING if (avail and not st.ancestral_started) else None,
    }


def _weighted_choice(keys: Tuple[str, ...], weights: Tuple[float, ...]) -> str:
    return random.choices(keys, weights=weights, k=1)[0]


async def _get_or_init_state(guild_id: int, user_id: int, username: Optional[str]) -> EnhanceState:
    uname = (username or "")[:100]
    async with get_db() as db:
        rows = await db.execute(
            "SELECT guild_id, user_id, username, level, pity, last_attempt, gaho_count, gaho_ready, "
            "cooldown_penalty_until, gaho_extra_try, gaho_shield, gaho_down_shield, "
            "gaho_upgrade_pending, tok_three_runs_left, tok_destroy_becomes_plus1, tok_dice_pending, "
            "esther_bindings, tok_duel_successes, ancestral_blessings, ancestral_started "
            "FROM enhance_state WHERE guild_id=? AND user_id=? LIMIT 1",
            (guild_id, user_id),
        )
        if rows:
            row = rows[0]
            if username and (row.get("username") or "") != uname:
                await db.execute(
                    "UPDATE enhance_state SET username=? WHERE guild_id=? AND user_id=?",
                    (uname, guild_id, user_id),
                )
                await db.commit()
                row["username"] = uname
            st = EnhanceState.from_row(row)
            return st
        await db.execute(
            "INSERT INTO enhance_state (guild_id, user_id, username, level, pity, gaho_count, gaho_ready, gaho_extra_try, "
            "gaho_shield, gaho_down_shield, gaho_upgrade_pending, tok_three_runs_left, tok_destroy_becomes_plus1, "
            "tok_dice_pending, esther_bindings, tok_duel_successes, ancestral_blessings, ancestral_started) "
            "VALUES (?, ?, ?, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '[]', 0, '[]', 0)",
            (guild_id, user_id, uname),
        )
        await db.commit()
        return EnhanceState(
            guild_id=guild_id,
            user_id=user_id,
            username=uname,
            level=1,
            pity=0.0,
            last_attempt=None,
            gaho_count=0,
            gaho_ready=0,
            cooldown_penalty_until=None,
            gaho_extra_try=0,
            gaho_shield=0,
            gaho_down_shield=0,
            gaho_upgrade_pending=0,
            tok_three_runs_left=0,
            tok_destroy_becomes_plus1=0,
            tok_dice_pending=0,
            tok_duel_successes=0,
            esther_list=[],
            ancestral_list=[],
            ancestral_started=0,
        )


async def _pull_charity_buff(guild_id: int, actor_user_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        rows = await db.execute(
            "SELECT id, donor_user_id, donor_username, amount, uses_left FROM enhance_server_buffs "
            "WHERE guild_id=? AND uses_left>0 AND donor_user_id<>? ORDER BY id ASC LIMIT 1 FOR UPDATE",
            (guild_id, actor_user_id),
        )
        if not rows:
            return None
        b = rows[0]
        await db.execute(
            "UPDATE enhance_server_buffs SET uses_left=uses_left-1 WHERE id=? AND uses_left>0 LIMIT 1",
            (b["id"],),
        )
        await db.commit()
        return {
            "from_user_id": int(b["donor_user_id"]),
            "from_username": b.get("donor_username") or str(b["donor_user_id"]),
            "amount": float(b["amount"]),
        }


async def _update_state_partial(guild_id: int, user_id: int, updates: Dict[str, Any]) -> None:
    if not updates:
        return
    fields = []
    params = []
    for k, v in updates.items():
        fields.append(f"{k}=?")
        params.append(v)
    params.extend([guild_id, user_id])
    async with get_db() as db:
        await db.execute(
            f"UPDATE enhance_state SET {', '.join(fields)} WHERE guild_id=? AND user_id=?",
            tuple(params),
        )
        await db.commit()


async def _apply_gaho_effect(
    guild_id: int,
    user_id: int,
    st: EnhanceState,
    upgrade_mode: bool,
    from_auto: bool = False,
    triggered_by_silian: bool = False,
    ninave_bound: bool = False,
    kadan_bound: bool = False,
    esthers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    est_list = esthers or st.esther_list or []
    azeina_bound = "azeina" in est_list
    shandi_bound = "shandi" in est_list
    ancestral = st.ancestral_list or []
    if "galatur" in ancestral:
        upgrade_mode = True
    if upgrade_mode:
        keys, weights = GAHO_PLUS_KEYS, GAHO_PLUS_WEIGHTS_VALUES
        filtered_keys = list(keys)
        filtered_weights = list(weights)
    else:
        keys, weights = GAHO_KEYS, GAHO_WEIGHTS_VALUES
        filtered_keys = []
        filtered_weights = []
        for k, w in zip(keys, weights):
            if azeina_bound and k in ("level_minus_1", "level_minus_2"):
                continue
            if shandi_bound and k == "cooldown_plus_10m":
                continue
            filtered_keys.append(k)
            filtered_weights.append(w)
        if not filtered_keys:
            filtered_keys = list(keys)
            filtered_weights = list(weights)
    effect_key = _weighted_choice(tuple(filtered_keys), tuple(filtered_weights))
    level = st.level
    pity = st.pity
    penalty_until = st.cooldown_penalty_until
    shield = st.gaho_shield
    down_shield = st.gaho_down_shield
    gaho_count = st.gaho_count
    three_runs_left = st.tok_three_runs_left
    destroy_to_plus1 = st.tok_destroy_becomes_plus1
    dice_pending = st.tok_dice_pending
    gaho_extra_try = st.gaho_extra_try
    upgrade_pending_after = st.gaho_upgrade_pending
    next_gaho_count = None
    applied: Dict[str, Any] = {}
    now_utc = _now_utc()
    normal_threshold, plus_threshold = _calc_thresholds(est_list)
    threshold = plus_threshold if upgrade_mode else normal_threshold
    def maybe_double(v: int) -> int:
        return v * 2 if ninave_bound else v
    if not upgrade_mode:
        if effect_key == "gaho_upgrade":
            applied = {"type": effect_key, "desc": "다음 선조의 가호 1회가 강화됩니다."}
            upgrade_pending_after = 1
        elif effect_key == "cooldown_plus_10m":
            applied = {"type": effect_key, "desc": "10분 쿨타임 증가"}
            base_until = _cooldown_until(st.last_attempt, penalty_until, est_list) or now_utc
            penalty_until = base_until + EXTRA_PENALTY
        elif effect_key == "level_plus_1":
            inc = maybe_double(1)
            applied = {"type": effect_key, "desc": f"{inc}강 증가"}
            level = min(level + inc, MAX_LEVEL)
        elif effect_key == "level_plus_2":
            inc = maybe_double(2)
            applied = {"type": effect_key, "desc": f"{inc}강 증가"}
            level = min(level + inc, MAX_LEVEL)
        elif effect_key == "level_minus_1":
            if level == 1:
                applied = {"type": effect_key, "desc": "더 낮아질 구간이 없어 -1강 미적용"}
            else:
                applied = {"type": effect_key, "desc": "1강 하락"}
                level = max(level - 1, 1)
        elif effect_key == "level_minus_2":
            if level == 1:
                applied = {"type": effect_key, "desc": "더 낮아질 구간이 없어 -2강 미적용"}
            else:
                applied = {"type": effect_key, "desc": "2강 하락"}
                level = max(level - 2, 1)
        elif effect_key == "pity_plus_20":
            inc = maybe_double(20)
            applied = {"type": effect_key, "desc": f"장기백 +{inc}"}
            pity = min(pity + inc, 100.0)
        elif effect_key == "safe_destroy":
            inc = maybe_double(1)
            shield += inc
            applied = {"type": effect_key, "desc": f"파괴 방지권 +{inc}"}
        elif effect_key == "safe_down":
            inc = maybe_double(1)
            down_shield += inc
            applied = {"type": effect_key, "desc": f"하락 방지권 +{inc}"}
        elif effect_key == "gaho_plus_3":
            inc = maybe_double(5)
            next_gaho_count = min(gaho_count + inc, threshold)
            applied = {"type": effect_key, "desc": f"가호 스택 +{inc}"}
        elif effect_key == "dice":
            dice_pending = 1
            applied = {"type": effect_key, "desc": "주사위: 버튼으로 성공/하락 50% 즉시 판정"}
        elif effect_key == "charity":
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO enhance_server_buffs (guild_id, donor_user_id, donor_username, amount, uses_left, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (guild_id, user_id, st.username[:100], CHARITY_BONUS, CHARITY_USES_PER_PROC, now_utc),
                )
                await db.commit()
            applied = {"type": effect_key, "desc": f"사회 환원: 서버에 성공확률 +{CHARITY_BONUS:.0f}% 버프(3회) 생성"}
    else:
        if effect_key == "plus1_and_nextstack2":
            lvl_inc = maybe_double(1)
            stack_inc = maybe_double(2)
            level = min(level + lvl_inc, MAX_LEVEL)
            next_gaho_count = min(gaho_count + stack_inc, threshold)
            applied = {"type": effect_key, "desc": f"+{lvl_inc}강 & 다음 가호 스택 +{stack_inc}"}
        elif effect_key == "plus2_and_nextstack2":
            lvl_inc = maybe_double(2)
            stack_inc = maybe_double(2)
            level = min(level + lvl_inc, MAX_LEVEL)
            next_gaho_count = min(gaho_count + stack_inc, threshold)
            applied = {"type": effect_key, "desc": f"+{lvl_inc}강 & 다음 가호 스택 +{stack_inc}"}
        elif effect_key == "plus4":
            inc = maybe_double(5)
            level = min(level + inc, MAX_LEVEL)
            applied = {"type": effect_key, "desc": f"+{inc}강"}
        elif effect_key == "safe_destroy_x2":
            inc = maybe_double(2)
            shield += inc
            applied = {"type": effect_key, "desc": f"파괴 방지권 +{inc}"}
        elif effect_key == "safe_down_x2":
            inc = maybe_double(2)
            down_shield += inc
            applied = {"type": effect_key, "desc": f"하락 방지권 +{inc}"}
        elif effect_key == "nextstack5_and_immediate_retry":
            stack_inc = maybe_double(5)
            next_gaho_count = min(gaho_count + stack_inc, threshold)
            gaho_extra_try = 1
            applied = {"type": effect_key, "desc": f"다음 가호 스택 +{stack_inc} & 즉시 재시도"}
        elif effect_key == "three_runs_protect":
            three = three_runs_left + 3
            three_runs_left = 3 if three > 3 else three
            applied = {"type": effect_key, "desc": "3연속: 실패·파괴 0%, 쿨타임 무시"}
        elif effect_key == "destroy_becomes_plus1_stack":
            cur = destroy_to_plus1 + 1
            destroy_to_plus1 = 3 if cur > 3 else cur
            applied = {"type": effect_key, "desc": "파괴 시 +1강 전환(스택)"}
        upgrade_pending_after = st.gaho_upgrade_pending if from_auto else 0
    if from_auto:
        gaho_ready_after = st.gaho_ready
        new_gaho_count = gaho_count if next_gaho_count is None else next_gaho_count
    else:
        gaho_ready_after = 0
        new_gaho_count = next_gaho_count if next_gaho_count is not None else 0
    await _update_state_partial(
        guild_id,
        user_id,
        {
            "level": level,
            "pity": pity,
            "gaho_ready": gaho_ready_after,
            "gaho_count": new_gaho_count,
            "cooldown_penalty_until": penalty_until,
            "gaho_shield": shield,
            "gaho_down_shield": down_shield,
            "gaho_upgrade_pending": upgrade_pending_after,
            "tok_three_runs_left": three_runs_left,
            "tok_destroy_becomes_plus1": destroy_to_plus1,
            "tok_dice_pending": dice_pending,
            "gaho_extra_try": gaho_extra_try,
            "tok_duel_successes": 0 if effect_key == "three_runs_protect" else st.tok_duel_successes,
        },
    )
    async with get_db() as db:
        await db.execute(
            "INSERT INTO enhance_gaho_log (guild_id,user_id,effect,weight) VALUES (?,?,?,?)",
            (guild_id, user_id, effect_key, 0.0),
        )
        await db.commit()
    cap_now = _current_cap(est_list, bool(upgrade_mode if from_auto else upgrade_pending_after))
    return {
        "effect": applied,
        "level": level,
        "pity": pity,
        "gaho_count": new_gaho_count,
        "shield": shield,
        "down_shield": down_shield,
        "upgrade_pending": bool(upgrade_mode if from_auto else upgrade_pending_after),
        "three_runs_left": three_runs_left,
        "destroy_becomes_plus1": destroy_to_plus1,
        "dice_pending": dice_pending,
        "gaho_extra_try": gaho_extra_try,
        "gaho_cap": cap_now,
    }


@router.get("/state")
async def get_state(guild_id: int, user_id: int):
    st = await _get_or_init_state(guild_id, user_id, None)
    level = st.level
    pity = st.pity
    esthers = st.esther_list
    cd_remain = _cooldown_remain(st.last_attempt, st.cooldown_penalty_until, esthers)
    upgrade_pending = bool(st.gaho_upgrade_pending)
    normal_cap, plus_cap = _calc_thresholds(esthers)
    cap_now = plus_cap if upgrade_pending else normal_cap
    available_labels: List[str] = []
    available_keys: List[str] = []
    tooltips: Dict[str, str] = {}
    if level >= MAX_LEVEL:
        remaining = [k for k in ESTHER_KEYS if k not in esthers]
        available_labels = [ESTHER_CHAR_NAMES[k] for k in remaining]
        available_keys = remaining
        tooltips = {k: ESTHER_ABILITIES.get(k, "") for k in remaining}
    return ORJSONResponse(
        {
            "guild_id": guild_id,
            "user_id": user_id,
            "username": st.username,
            "level": level,
            "level_label": _level_label(level),
            "pity": pity,
            "cooldown_remain_sec": cd_remain,
            "max_level_label": ESTHER_LABEL,
            "current_rates": _effective_rates(level, pity, esthers),
            "gaho_cap": cap_now,
            "state": {"gaho_cap": cap_now},
            "gaho": {
                "ready": bool(st.gaho_ready),
                "count": st.gaho_count,
                "shield": st.gaho_shield,
                "down_shield": st.gaho_down_shield,
                "upgrade_pending": upgrade_pending,
                "cap": cap_now,
                "stack_cap": cap_now,
            },
            "tokens": {
                "three_runs_left": st.tok_three_runs_left,
                "destroy_becomes_plus1": st.tok_destroy_becomes_plus1,
                "dice_pending": st.tok_dice_pending,
            },
            "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
            "available_esthers": available_labels,
            "available_esther_keys": available_keys,
            "esther_tooltips": tooltips,
            "ancestral": _ancestral_payload(st),
        }
    )


async def _post_attempt_bahunture_auto_gaho_if_any(guild_id: int, user_id: int, base_state_after: Dict[str, Any], response_data: Dict[str, Any]) -> Dict[str, Any]:
    st_after = await _get_or_init_state(guild_id, user_id, None)
    esthers = st_after.esther_list
    if "bahunture" not in esthers:
        response_data["auto_gaho"] = {"triggered": False}
        return response_data
    if random.random() >= 0.20:
        response_data["auto_gaho"] = {"triggered": False}
        return response_data
    upgrade_mode = True if "galatur" in (st_after.ancestral_list or []) else bool(st_after.gaho_upgrade_pending)
    result = await _apply_gaho_effect(
        guild_id,
        user_id,
        st_after,
        upgrade_mode=upgrade_mode,
        from_auto=True,
        esthers=esthers,
        ninave_bound=("ninave" in esthers),
        kadan_bound=("kadan" in esthers),
    )
    cap_now = _current_cap(esthers, result["upgrade_pending"])
    response_data["auto_gaho"] = {
        "triggered": True,
        "effect": {**result["effect"], "upgrade_mode": upgrade_mode},
        "gaho_cap": cap_now,
    }
    response_data["state"]["level"] = result["level"]
    response_data["state"]["level_label"] = _level_label(result["level"])
    response_data["state"]["pity"] = result["pity"]
    response_data["state"]["gaho_cap"] = cap_now
    response_data["gaho"]["ready"] = base_state_after.get("gaho", {}).get("ready") and response_data["gaho"]["ready"]
    response_data["gaho"]["count"] = result["gaho_count"]
    response_data["gaho"]["shield"] = result["shield"]
    response_data["gaho"]["down_shield"] = result["down_shield"]
    response_data["gaho"]["upgrade_pending"] = result["upgrade_pending"]
    response_data["gaho"]["cap"] = cap_now
    response_data["gaho"]["stack_cap"] = cap_now
    response_data["tokens"]["three_runs_left"] = result["three_runs_left"]
    response_data["tokens"]["destroy_becomes_plus1"] = result["destroy_becomes_plus1"]
    response_data["tokens"]["dice_pending"] = result["dice_pending"]
    if result["level"] >= MAX_LEVEL:
        remaining = [k for k in ESTHER_KEYS if k not in esthers]
        response_data.update(
            {
                "available_esthers": [ESTHER_CHAR_NAMES[k] for k in remaining],
                "available_esther_keys": remaining,
                "esther_tooltips": {k: ESTHER_ABILITIES.get(k, "") for k in remaining},
            }
        )
    st_after2 = await _get_or_init_state(guild_id, user_id, None)
    response_data["ancestral"] = _ancestral_payload(st_after2)
    return response_data


@router.post("/try")
async def enhance_try(payload: EnhanceAttempt):
    guild_id = payload.guild_id
    user_id = payload.user_id
    username = (payload.username or "").strip() or None
    st = await _get_or_init_state(guild_id, user_id, username)
    cur_level = st.level
    pity = st.pity
    esthers = st.esther_list
    ancestral = st.ancestral_list
    if cur_level >= MAX_LEVEL:
        remaining = [k for k in ESTHER_KEYS if k not in esthers]
        cap_now = _current_cap(esthers, bool(st.gaho_upgrade_pending))
        resp = {
            "ok": True,
            "outcome": "already_max",
            "message": f"이미 {ESTHER_LABEL}입니다.",
            "state": {
                "level": cur_level,
                "level_label": _level_label(cur_level),
                "pity": pity,
                "cooldown_remain_sec": 0,
                "gaho_cap": cap_now,
            },
            "gaho_cap": cap_now,
            "gaho": {
                "ready": bool(st.gaho_ready),
                "count": st.gaho_count,
                "shield": st.gaho_shield,
                "down_shield": st.gaho_down_shield,
                "upgrade_pending": bool(st.gaho_upgrade_pending),
                "cap": cap_now,
                "stack_cap": cap_now,
            },
            "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
            "available_esthers": [ESTHER_CHAR_NAMES[k] for k in remaining],
            "available_esther_keys": remaining,
            "esther_tooltips": {k: ESTHER_ABILITIES.get(k, "") for k in remaining},
            "auto_gaho": {"triggered": False},
            "ancestral": _ancestral_payload(st),
        }
        return ORJSONResponse(resp)
    now_utc = _now_utc()
    three_runs_left = st.tok_three_runs_left
    ignore_cooldown = three_runs_left > 0
    extra_try_flag = st.gaho_extra_try > 0
    remain_cd = _cooldown_remain(st.last_attempt, st.cooldown_penalty_until, esthers)
    used_extra_try = extra_try_flag and remain_cd > 0
    if remain_cd > 0 and not extra_try_flag and not ignore_cooldown:
        raise HTTPException(status_code=429, detail={"message": "강화 대기 중입니다.", "cooldown_remain_sec": remain_cd})
    shield = st.gaho_shield
    down_shield = st.gaho_down_shield
    if pity >= 100.0:
        new_level = cur_level + 1 if cur_level < MAX_LEVEL else MAX_LEVEL
        pity_after = max(0.0, pity - 100.0)
        new_gaho_count = st.gaho_count + 1
        normal_cap, plus_cap = _calc_thresholds(esthers)
        threshold = plus_cap if st.gaho_upgrade_pending else normal_cap
        became_ready = new_gaho_count >= threshold
        duel_successes = st.tok_duel_successes
        if three_runs_left > 0:
            duel_successes += 1
        three_runs_after = three_runs_left - 1 if three_runs_left > 0 else 0
        duel_bonus_applied = False
        if three_runs_left > 0 and three_runs_after == 0:
            if ("silian" in esthers) and duel_successes >= 3 and new_level < MAX_LEVEL:
                new_level += 1
                duel_bonus_applied = True
            duel_successes = 0
        next_extra = 0 if used_extra_try else st.gaho_extra_try
        await _update_state_partial(
            guild_id,
            user_id,
            {
                "level": new_level,
                "pity": pity_after,
                "last_attempt": now_utc,
                "username": username[:100] if username else st.username,
                "gaho_count": 0 if became_ready else new_gaho_count,
                "gaho_ready": 1 if became_ready else 0,
                "gaho_extra_try": next_extra,
                "tok_three_runs_left": three_runs_after,
                "tok_duel_successes": duel_successes,
            },
        )
        async with get_db() as db:
            await db.execute(
                "INSERT INTO enhance_log (guild_id,user_id,before_level,after_level,pity_before,pity_after,outcome,roll) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (guild_id, user_id, cur_level, new_level, pity, pity_after, "pity_forced", -1.0),
            )
            await db.commit()
        rates_after = _effective_rates(new_level, pity_after, esthers)
        cap_now = _current_cap(esthers, bool(st.gaho_upgrade_pending))
        resp: Dict[str, Any] = {
            "ok": True,
            "outcome": "pity_forced",
            "roll": -1.0,
            "rates_before": _effective_rates(cur_level, pity, esthers),
            "rates_after": rates_after,
            "gaho_cap": cap_now,
            "state": {
                "level": new_level,
                "level_label": _level_label(new_level),
                "pity": pity_after,
                "cooldown_remain_sec": int(_effective_cooldown_timedelta(esthers).total_seconds()),
                "max_level_label": ESTHER_LABEL,
                "gaho_cap": cap_now,
            },
            "destroy_reset": False,
            "gaho": {
                "ready": bool(became_ready),
                "count": 0 if became_ready else new_gaho_count,
                "shield": shield,
                "down_shield": down_shield,
                "upgrade_pending": bool(st.gaho_upgrade_pending),
                "cap": cap_now,
                "stack_cap": cap_now,
            },
            "tokens": {
                "three_runs_left": three_runs_after,
                "destroy_becomes_plus1": st.tok_destroy_becomes_plus1,
                "dice_pending": st.tok_dice_pending,
            },
            "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
            "ancestral": _ancestral_payload(await _get_or_init_state(guild_id, user_id, None)),
        }
        if three_runs_left > 0:
            resp["duel"] = {
                "active": True,
                "succ": 80.0 if "silian" in esthers else 50.0,
                "down": 20.0 if "silian" in esthers else 50.0,
                "extra_plus1_on_3_success": "silian" in esthers,
                "bonus_applied_now": duel_bonus_applied,
            }
        if new_level >= MAX_LEVEL:
            remaining = [k for k in ESTHER_KEYS if k not in esthers]
            resp.update(
                {
                    "available_esthers": [ESTHER_CHAR_NAMES[k] for k in remaining],
                    "available_esther_keys": remaining,
                    "esther_tooltips": {k: ESTHER_ABILITIES.get(k, "") for k in remaining},
                }
            )
        resp = await _post_attempt_bahunture_auto_gaho_if_any(guild_id, user_id, resp, resp)
        return ORJSONResponse(resp)
    charity = await _pull_charity_buff(guild_id, user_id)
    rates_base = _effective_rates(cur_level, pity, esthers)
    succ = rates_base["success"]
    des = rates_base["destroy"]
    down = rates_base["down"]
    pity_gain_base = rates_base["pity_gain"]
    sien_owned = "sien" in ancestral
    pity_gain_eff = pity_gain_base * (2.0 if sien_owned else 1.0)
    if charity:
        succ = min(100.0, succ + charity["amount"])
    if three_runs_left > 0:
        if "silian" in esthers:
            succ_eff, des_eff, down_eff = 80.0, 0.0, 20.0
        else:
            succ_eff, des_eff, down_eff = 50.0, 0.0, 50.0
    else:
        succ_eff, des_eff, down_eff = succ, des, down
    boundary_succ = succ_eff
    boundary_des = succ_eff + des_eff
    boundary_down = boundary_des + down_eff
    roll_val = random.random() * 100.0
    outcome = "fail"
    lvl_after = cur_level
    pity_after = pity
    destroy_reset = False
    destroy_prevented = False
    shield_after = shield
    down_shield_after = down_shield
    if roll_val < boundary_succ:
        outcome = "success"
        lvl_after = cur_level + 1 if cur_level < MAX_LEVEL else MAX_LEVEL
        pity_after = 0.0
    elif roll_val < boundary_des:
        if shield > 0:
            outcome = "destroy_prevented"
            destroy_prevented = True
            shield_after = shield - 1
            pity_after = min(100.0, pity + pity_gain_eff)
        else:
            if sien_owned and random.random() < 0.5:
                outcome = "destroy_prevented"
                destroy_prevented = True
                pity_after = min(100.0, pity + pity_gain_eff)
            else:
                if st.tok_destroy_becomes_plus1 > 0:
                    outcome = "destroy_to_plus1"
                    lvl_after = cur_level + 1 if cur_level < MAX_LEVEL else MAX_LEVEL
                    pity_after = 0.0
                    st.tok_destroy_becomes_plus1 -= 1
                else:
                    outcome = "destroy"
                    lvl_after = 1
                    pity_after = 0.0
                    destroy_reset = True
    elif roll_val < boundary_down:
        if down_shield > 0:
            outcome = "down_prevented"
            down_shield_after = down_shield - 1
            pity_after = min(100.0, pity + pity_gain_eff)
        else:
            if sien_owned and random.random() < 0.5:
                outcome = "down_prevented"
                pity_after = min(100.0, pity + pity_gain_eff)
            else:
                outcome = "down"
                lvl_after = cur_level - 1 if cur_level > 1 else 1
                pity_after = min(100.0, pity + pity_gain_eff)
    else:
        pity_after = min(100.0, pity + pity_gain_eff)
    duel_successes = st.tok_duel_successes
    is_success_like = outcome in ("success", "pity_forced", "destroy_to_plus1", "dice_success")
    if three_runs_left > 0 and is_success_like:
        duel_successes += 1
    three_runs_after = three_runs_left - 1 if three_runs_left > 0 else 0
    duel_bonus_applied = False
    if three_runs_left > 0 and three_runs_after == 0:
        if ("silian" in esthers) and duel_successes >= 3 and lvl_after < MAX_LEVEL:
            lvl_after += 1
            duel_bonus_applied = True
        duel_successes = 0
    next_extra = st.gaho_extra_try
    if extra_try_flag and _cooldown_remain(st.last_attempt, st.cooldown_penalty_until, esthers) > 0:
        next_extra = 0
    normal_cap, plus_cap = _calc_thresholds(esthers)
    threshold = plus_cap if st.gaho_upgrade_pending else normal_cap
    new_gaho_count = st.gaho_count + 1
    became_ready = new_gaho_count >= threshold
    new_gaho_count_after = 0 if became_ready else new_gaho_count
    gaho_ready_after = 1 if became_ready else 0
    await _update_state_partial(
        guild_id,
        user_id,
        {
            "level": lvl_after,
            "pity": pity_after,
            "last_attempt": now_utc,
            "username": username[:100] if username else st.username,
            "gaho_count": new_gaho_count_after,
            "gaho_ready": gaho_ready_after,
            "gaho_extra_try": next_extra,
            "gaho_shield": shield_after,
            "gaho_down_shield": down_shield_after,
            "tok_three_runs_left": three_runs_after,
            "tok_destroy_becomes_plus1": st.tok_destroy_becomes_plus1,
            "tok_dice_pending": st.tok_dice_pending,
            "tok_duel_successes": duel_successes,
        },
    )
    async with get_db() as db:
        await db.execute(
            "INSERT INTO enhance_log (guild_id,user_id,before_level,after_level,pity_before,pity_after,outcome,roll) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (guild_id, user_id, cur_level, lvl_after, pity, pity_after, outcome, round(roll_val, 3)),
        )
        await db.commit()
    cap_now = _current_cap(esthers, bool(st.gaho_upgrade_pending))
    response_data: Dict[str, Any] = {
        "ok": True,
        "outcome": outcome,
        "destroy_prevented": destroy_prevented,
        "down_prevented": outcome == "down_prevented",
        "roll": round(roll_val, 3),
        "rates_before": {
            "success": succ,
            "destroy": des,
            "down": down,
            "fail": max(0.0, 100.0 - (succ + des + down)),
            "pity_gain": pity_gain_eff,
        },
        "rates_after": _effective_rates(lvl_after, pity_after, esthers),
        "gaho_cap": cap_now,
        "state": {
            "level": lvl_after,
            "level_label": _level_label(lvl_after),
            "pity": pity_after,
            "cooldown_remain_sec": int(_effective_cooldown_timedelta(esthers).total_seconds()),
            "max_level_label": ESTHER_LABEL,
            "gaho_cap": cap_now,
        },
        "destroy_reset": destroy_reset,
        "gaho": {
            "ready": bool(became_ready),
            "count": 0 if became_ready else new_gaho_count,
            "shield": shield_after,
            "down_shield": down_shield_after,
            "upgrade_pending": bool(st.gaho_upgrade_pending),
            "cap": cap_now,
            "stack_cap": cap_now,
        },
        "tokens": {
            "three_runs_left": three_runs_after,
            "destroy_becomes_plus1": st.tok_destroy_becomes_plus1,
            "dice_pending": st.tok_dice_pending,
        },
        "server_buff": (
            {"applied": True, "from_user_id": charity["from_user_id"], "from_username": charity["from_username"], "amount": charity["amount"]}
            if charity
            else {"applied": False}
        ),
        "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
        "ancestral": _ancestral_payload(await _get_or_init_state(guild_id, user_id, None)),
    }
    if three_runs_left > 0:
        response_data["duel"] = {
            "active": True,
            "succ": 80.0 if "silian" in esthers else 50.0,
            "down": 20.0 if "silian" in esthers else 50.0,
            "extra_plus1_on_3_success": "silian" in esthers,
            "bonus_applied_now": duel_bonus_applied,
        }
    if lvl_after >= MAX_LEVEL:
        remaining = [k for k in ESTHER_KEYS if k not in esthers]
        response_data.update(
            {
                "available_esthers": [ESTHER_CHAR_NAMES[k] for k in remaining],
                "available_esther_keys": remaining,
                "esther_tooltips": {k: ESTHER_ABILITIES.get(k, "") for k in remaining},
            }
        )
    if outcome in ("success", "destroy_to_plus1") and "luteran" in ancestral:
        if response_data["state"]["level"] < MAX_LEVEL and random.random() < 0.5:
            new_lv = response_data["state"]["level"] + 1
            await _update_state_partial(
                guild_id,
                user_id,
                {"level": new_lv},
            )
            response_data["state"]["level"] = new_lv
            response_data["state"]["level_label"] = _level_label(new_lv)
        st_after = await _get_or_init_state(guild_id, user_id, None)
        upg = True if "galatur" in (st_after.ancestral_list or []) else bool(st_after.gaho_upgrade_pending)
        auto_res = await _apply_gaho_effect(
            guild_id,
            user_id,
            st_after,
            upgrade_mode=upg,
            from_auto=True,
            esthers=st_after.esther_list,
        )
        response_data["auto_gaho_luteran"] = {"triggered": True, "effect": {**auto_res["effect"], "upgrade_mode": upg}}
        response_data["state"]["level"] = auto_res["level"]
        response_data["state"]["level_label"] = _level_label(auto_res["level"])
        response_data["state"]["pity"] = auto_res["pity"]
        response_data["gaho"]["count"] = auto_res["gaho_count"]
        response_data["gaho"]["shield"] = auto_res["shield"]
        response_data["gaho"]["down_shield"] = auto_res["down_shield"]
        response_data["gaho"]["upgrade_pending"] = auto_res["upgrade_pending"]
    response_data = await _post_attempt_bahunture_auto_gaho_if_any(guild_id, user_id, response_data, response_data)
    return ORJSONResponse(response_data)


@router.get("/gaho/state")
async def gaho_state(guild_id: int, user_id: int):
    st = await _get_or_init_state(guild_id, user_id, None)
    esthers = st.esther_list
    cap_now = _current_cap(esthers, bool(st.gaho_upgrade_pending))
    return ORJSONResponse(
        {
            "ready": bool(st.gaho_ready),
            "count": st.gaho_count,
            "shield": st.gaho_shield,
            "down_shield": st.gaho_down_shield,
            "upgrade_pending": bool(st.gaho_upgrade_pending),
            "cap": cap_now,
            "stack_cap": cap_now,
            "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
            "ancestral": _ancestral_payload(st),
        }
    )


@router.post("/gaho/skip")
async def gaho_skip(payload: EnhanceAttempt):
    st = await _get_or_init_state(payload.guild_id, payload.user_id, payload.username)
    if not st.gaho_ready:
        raise HTTPException(status_code=400, detail="가호를 사용할 수 있는 상태가 아닙니다.")
    await _update_state_partial(
        payload.guild_id,
        payload.user_id,
        {"gaho_ready": 0, "gaho_count": 0},
    )
    st2 = await _get_or_init_state(payload.guild_id, payload.user_id, None)
    return ORJSONResponse({"ok": True, "skipped": True, "ancestral": _ancestral_payload(st2)})


@router.post("/gaho/draw")
async def gaho_draw(payload: EnhanceAttempt):
    guild_id = payload.guild_id
    user_id = payload.user_id
    st = await _get_or_init_state(guild_id, user_id, payload.username)
    if not st.gaho_ready:
        raise HTTPException(status_code=400, detail="가호를 사용할 수 있는 상태가 아닙니다.")
    esthers = st.esther_list
    upgrade_mode = True if "galatur" in (st.ancestral_list or []) else bool(st.gaho_upgrade_pending)
    ninave_bound = "ninave" in esthers
    kadan_bound = "kadan" in esthers
    result = await _apply_gaho_effect(
        guild_id,
        user_id,
        st,
        upgrade_mode=upgrade_mode,
        triggered_by_silian=("silian" in esthers),
        ninave_bound=ninave_bound,
        kadan_bound=kadan_bound,
        esthers=esthers,
    )
    cap_now = _current_cap(esthers, upgrade_mode)
    st2 = await _get_or_init_state(guild_id, user_id, None)
    return ORJSONResponse(
        {
            "ok": True,
            "gaho_cap": cap_now,
            "effect": {**result["effect"], "upgrade_mode": upgrade_mode},
            "state": {
                "level": result["level"],
                "level_label": _level_label(result["level"]),
                "pity": result["pity"],
                "cooldown_remain_sec": _cooldown_remain(st.last_attempt, st.cooldown_penalty_until, esthers),
                "gaho_count": result["gaho_count"],
                "gaho_shield": result["shield"],
                "gaho_down_shield": result["down_shield"],
                "gaho_upgrade_pending": result["upgrade_pending"],
                "gaho_cap": cap_now,
            },
            "gaho": {
                "ready": False,
                "count": result["gaho_count"],
                "shield": result["shield"],
                "down_shield": result["down_shield"],
                "upgrade_pending": result["upgrade_pending"],
                "cap": cap_now,
                "stack_cap": cap_now,
            },
            "tokens": {
                "three_runs_left": result["three_runs_left"],
                "destroy_becomes_plus1": result["destroy_becomes_plus1"],
                "dice_pending": result["dice_pending"],
            },
            "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
            "ancestral": _ancestral_payload(st2),
        }
    )


@router.post("/gaho/dice")
async def gaho_dice(payload: EnhanceAttempt):
    st = await _get_or_init_state(payload.guild_id, payload.user_id, payload.username)
    if st.tok_dice_pending == 0:
        raise HTTPException(status_code=400, detail="주사위를 사용할 수 있는 상태가 아닙니다.")
    level = st.level
    pity = st.pity
    esthers = st.esther_list
    sien_owned = "sien" in (st.ancestral_list or [])
    roll = random.random()
    if roll < 0.5:
        outcome = "dice_success"
        level_after = level + 1 if level < MAX_LEVEL else MAX_LEVEL
        pity_after = 0.0
    else:
        outcome = "dice_down"
        level_after = level - 1 if level > 1 else 1
        base_pity = (RATES.get(level) or {"pity": 2.0})["pity"]
        pity_after = pity + (base_pity * (2.0 if sien_owned else 1.0))
        if pity_after > 100.0:
            pity_after = 100.0
    await _update_state_partial(
        payload.guild_id,
        payload.user_id,
        {"level": level_after, "pity": pity_after, "tok_dice_pending": 0},
    )
    async with get_db() as db:
        await db.execute(
            "INSERT INTO enhance_log (guild_id,user_id,before_level,after_level,pity_before,pity_after,outcome,roll) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (payload.guild_id, payload.user_id, level, level_after, pity, pity_after, outcome, round(roll * 100, 3)),
        )
        await db.commit()
    cap_now = _current_cap(esthers, bool(st.gaho_upgrade_pending))
    st2 = await _get_or_init_state(payload.guild_id, payload.user_id, None)
    return ORJSONResponse(
        {
            "ok": True,
            "outcome": outcome,
            "gaho_cap": cap_now,
            "state": {
                "level": level_after,
                "level_label": _level_label(level_after),
                "pity": pity_after,
                "gaho_cap": cap_now,
            },
            "gaho": {"cap": cap_now, "stack_cap": cap_now},
            "esthers": [ESTHER_CHAR_NAMES[e] for e in esthers],
            "ancestral": _ancestral_payload(st2),
        }
    )


@router.get("/leaderboard")
async def enhance_leaderboard(
    guild_id: int,
    mode: str = Query("level", pattern="^(level|success)$"),
    limit: int = Query(20, ge=1, le=50),
):
    async with get_db() as db:
        if mode == "level":
            rows = await db.execute(
                """
                SELECT user_id, COALESCE(username, CAST(user_id AS CHAR)) AS username, level, pity, esther_bindings
                  FROM enhance_state
                 WHERE guild_id = ?
                 ORDER BY level DESC, pity DESC, user_id ASC
                 LIMIT ?
                """,
                (guild_id, limit),
            ) or []
            return ORJSONResponse(
                [
                    {
                        "user_id": r["user_id"],
                        "username": r["username"],
                        "level": r["level"],
                        "pity": float(r["pity"]),
                        "esthers": [ESTHER_CHAR_NAMES[e] for e in _parse_esthers(r.get("esther_bindings"))],
                    }
                    for r in rows
                ]
            )
        rows = await db.execute(
            """
            SELECT l.user_id, COALESCE(MAX(s.username), CAST(l.user_id AS CHAR)) AS username,
                   SUM(CASE WHEN l.outcome IN ('success','pity_forced','dice_success') THEN 1 ELSE 0 END) AS success_count
              FROM enhance_log l
              LEFT JOIN enhance_state s ON s.guild_id = l.guild_id AND s.user_id = l.user_id
             WHERE l.guild_id = ?
             GROUP BY l.user_id
             ORDER BY success_count DESC, l.user_id ASC
             LIMIT ?
            """,
            (guild_id, limit),
        ) or []
        return ORJSONResponse(
            [
                {
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "success_count": int(r["success_count"] or 0),
                }
                for r in rows
            ]
        )


@router.post("/reset")
async def reset_states(payload: ResetPayload):
    guild_id = payload.guild_id
    user_id = payload.user_id
    hard = bool(payload.hard)
    async with get_db() as db:
        if user_id is not None:
            if hard:
                await db.execute(
                    "DELETE FROM enhance_state WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                )
                await db.execute(
                    "INSERT INTO enhance_state (guild_id, user_id, username, level, pity, gaho_count, gaho_ready, gaho_extra_try, "
                    "gaho_shield, gaho_down_shield, gaho_upgrade_pending, tok_three_runs_left, tok_destroy_becomes_plus1, "
                    "tok_dice_pending, esther_bindings, tok_duel_successes, ancestral_blessings, ancestral_started) "
                    "VALUES (?, ?, '', 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '[]', 0, '[]', 0)",
                    (guild_id, user_id),
                )
            else:
                await db.execute(
                    "UPDATE enhance_state SET level=1, pity=0, last_attempt=NULL, gaho_count=0, gaho_ready=0, gaho_extra_try=0, "
                    "cooldown_penalty_until=NULL, gaho_shield=0, gaho_down_shield=0, gaho_upgrade_pending=0, "
                    "tok_three_runs_left=0, tok_destroy_becomes_plus1=0, tok_dice_pending=0, tok_duel_successes=0, "
                    "esther_bindings=?, ancestral_blessings='[]', ancestral_started=0 WHERE guild_id=? AND user_id=?",
                    (_serialize_esthers([]), guild_id, user_id),
                )
        else:
            if hard:
                await db.execute("DELETE FROM enhance_state WHERE guild_id=?", (guild_id,))
            else:
                await db.execute(
                    "UPDATE enhance_state SET level=1, pity=0, last_attempt=NULL, gaho_count=0, gaho_ready=0, gaho_extra_try=0, "
                    "cooldown_penalty_until=NULL, gaho_shield=0, gaho_down_shield=0, gaho_upgrade_pending=0, "
                    "tok_three_runs_left=0, tok_destroy_becomes_plus1=0, tok_dice_pending=0, tok_duel_successes=0, "
                    "esther_bindings='[]', ancestral_blessings='[]', ancestral_started=0 WHERE guild_id=?",
                    (guild_id,),
                )
        await db.commit()
    return ORJSONResponse({"ok": True, "guild_id": guild_id, "user_id": user_id, "hard": hard})


@router.post("/esther/bind")
async def esther_bind(payload: EstherBindPayload):
    guild_id = payload.guild_id
    user_id = payload.user_id
    key = _resolve_esther_key(payload.esther)
    if not key:
        raise HTTPException(status_code=400, detail="잘못된 에스더입니다.")
    st = await _get_or_init_state(guild_id, user_id, None)
    cur_level = st.level
    if cur_level < MAX_LEVEL:
        raise HTTPException(status_code=400, detail="에스더 결속은 37강 달성 후 가능합니다.")
    esthers = st.esther_list
    if key in esthers:
        raise HTTPException(status_code=400, detail="이미 결속된 에스더입니다.")
    new_list = esthers + [key]
    async with get_db() as db:
        await db.execute(
            "UPDATE enhance_state "
            "SET esther_bindings=?, "
            "    level=1, pity=0, last_attempt=NULL, cooldown_penalty_until=NULL, "
            "    gaho_count=0, gaho_ready=0, gaho_extra_try=0, "
            "    gaho_shield=0, gaho_down_shield=0, gaho_upgrade_pending=0, "
            "    tok_three_runs_left=0, tok_destroy_becomes_plus1=0, tok_dice_pending=0, tok_duel_successes=0 "
            "WHERE guild_id=? AND user_id=?",
            (_serialize_esthers(new_list), guild_id, user_id),
        )
        await db.commit()
    remaining = [k for k in ESTHER_KEYS if k not in new_list]
    cap_now = _current_cap(new_list, False)
    st2 = await _get_or_init_state(guild_id, user_id, None)
    return ORJSONResponse(
        {
            "ok": True,
            "bound_esther": ESTHER_CHAR_NAMES[key],
            "esthers": [ESTHER_CHAR_NAMES[e] for e in new_list],
            "available_esthers": [ESTHER_CHAR_NAMES[k] for k in remaining],
            "available_esther_keys": remaining,
            "esther_tooltips": {k: ESTHER_ABILITIES.get(k, "") for k in remaining},
            "gaho_cap": cap_now,
            "gaho": {"cap": cap_now, "stack_cap": cap_now},
            "state": {"gaho_cap": cap_now},
            "ancestral": _ancestral_payload(st2),
        }
    )


@router.post("/ancestral/select")
async def ancestral_select(payload: AncestralSelectPayload):
    guild_id = payload.guild_id
    user_id = payload.user_id
    key = (payload.key or "").strip().lower()
    if key not in ANCESTRAL_KEYS:
        raise HTTPException(status_code=400, detail="잘못된 선대 키")
    st = await _get_or_init_state(guild_id, user_id, None)
    avail = _ancestral_available(st)
    if key not in avail:
        raise HTTPException(status_code=400, detail="현재 선택할 수 없습니다.")
    owned = st.ancestral_list or []
    if key not in owned:
        owned.append(key)
    warning = None
    if not st.ancestral_started:
        warning = ANCESTRAL_WARNING
        async with get_db() as db:
            await db.execute(
                "UPDATE enhance_state SET ancestral_blessings=?, ancestral_started=1, esther_bindings='[]', "
                "level=1, pity=0, last_attempt=NULL, cooldown_penalty_until=NULL, "
                "gaho_count=0, gaho_ready=0, gaho_extra_try=0, "
                "gaho_shield=0, gaho_down_shield=0, gaho_upgrade_pending=0, "
                "tok_three_runs_left=0, tok_destroy_becomes_plus1=0, tok_dice_pending=0, tok_duel_successes=0 "
                "WHERE guild_id=? AND user_id=?",
                (json.dumps(owned), guild_id, user_id),
            )
            await db.commit()
    else:
        async with get_db() as db:
            await db.execute(
                "UPDATE enhance_state SET ancestral_blessings=?, "
                "level=1, pity=0, last_attempt=NULL, cooldown_penalty_until=NULL, "
                "gaho_count=0, gaho_ready=0, gaho_extra_try=0, "
                "gaho_shield=0, gaho_down_shield=0, gaho_upgrade_pending=0, "
                "tok_three_runs_left=0, tok_destroy_becomes_plus1=0, tok_dice_pending=0, tok_duel_successes=0 "
                "WHERE guild_id=? AND user_id=?",
                (json.dumps(owned), guild_id, user_id),
            )
            await db.commit()
    st2 = await _get_or_init_state(guild_id, user_id, None)
    return ORJSONResponse(
        {
            "ok": True,
            "selected": ANCESTRAL_LABEL[key],
            "owned": [ANCESTRAL_LABEL[x] for x in (st2.ancestral_list or [])],
            "warning": warning,
            "ancestral": _ancestral_payload(st2),
        }
    )
