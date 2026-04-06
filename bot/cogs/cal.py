import discord
from discord.ext import commands
from typing import Dict, Tuple
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, getcontext
import asyncio
import httpx
import time

from core.config import LOSTARK_API_SUB1_KEY, LOSTARK_API_SUB2_KEY

getcontext().prec = 28
CRAFT_UNIT        = Decimal("10")
ENERGY_PER_CRAFT  = Decimal("288")
TIME_PER_CRAFT    = Decimal("3600")
XP_PER_CRAFT      = Decimal("576")
FEE_PCT           = Decimal("5")

CRAFT_TABLE = {
    "ABYDOS": {
        "name": "아비도스 융화 재료",
        "market_name": "아비도스 융화 재료",
        "recipe": {"A": 86, "B": 45, "C": 33},
        "gold_per_craft": Decimal("400"),
        "craft_unit": CRAFT_UNIT,
    },
    "ABYDOS_PLUS": {
        "name": "상급 아비도스 융화 재료",
        "market_name": "상급 아비도스 융화 재료",
        "recipe": {"A": 112, "B": 59, "C": 43},
        "gold_per_craft": Decimal("520"),
        "craft_unit": CRAFT_UNIT,
    },
}
DEFAULT_CRAFT_KEY = "ABYDOS"

LIFE_MATS = {
    "WL": {"name": "벌목", "show_S": True,  "labels": {"A": "목재", "B": "부드러운 목재", "S": "튼튼한 목재", "C": "아비도스 목재",  "P": "벌목의 가루"}},
    "MN": {"name": "채광", "show_S": True,  "labels": {"A": "철광석","B": "묵직한 철광석","S": "단단한 철광석","C": "아비도스 철광석","P": "채광의 가루"}},
    "AR": {"name": "고고학","show_S": False, "labels": {"A": "고대 유물","B": "희귀한 유물","C": "아비도스 유물","P": "고고학의 가루"}},
    "FS": {"name": "낚시",  "show_S": False, "labels": {"A": "생선","B": "붉은 살 생선","C": "아비도스 태양 잉어","P": "낚시의 가루"}},
    "HR": {"name": "수렵",  "show_S": False, "labels": {"A": "두툼한 생고기","B": "다듬은 생고기","C": "아비도스 두툼한 생고기","P": "수렵의 가루"}},
    "CL": {"name": "채집",  "show_S": False, "labels": {"A": "들꽃","B": "수줍은 들꽃","C": "아비도스 들꽃","P": "채집의 가루"}},
}

RECIPE_ORDER = {
    "WL": ("A","B","C"),
    "AR": ("A","C","B"),
    "CL": ("A","B","C"),
    "MN": ("B","C","A"),
    "FS": ("B","A","C"),
    "HR": ("B","A","C"),
}

CATEGORY_FOR_LIFE = {"CL": 90200, "WL": 90300, "MN": 90400, "HR": 90500, "FS": 90600, "AR": 90700}
CATEGORY_FUSION_ABYDOS = 50010

NAME_EMOJI = {
    "목재": "<:wood:1412303588817895605>",
    "부드러운 목재": "<:plainwood:1412303708397768724>",
    "튼튼한 목재": "<:strongwood:1412303813070553178>",
    "아비도스 목재": "<:tier4wood:1412303890279301140>",
    "벌목의 가루": "<:wood_powder:1412304055480352849>",

    "철광석": "<:stone:1412304130759987200>",
    "묵직한 철광석": "<:heavy_stone:1412304339095130162>",
    "단단한 철광석": "<:strong_stone:1412304399971258408>",
    "아비도스 철광석": "<:tier4stone:1412304469315686431>",
    "채광의 가루": "<:stone_powder:1412304750262616145>",

    "고대 유물": "<:relics:1412307467240734874>",
    "희귀한 유물": "<:epic_relics:1412305096687226881>",
    "아비도스 유물": "<:tier4relics:1412305106568740936>",
    "고고학의 가루": "<:relics_powder:1412305178769490011>",

    "생선": "<:fish:1412305732111433850>",
    "붉은 살 생선": "<:red_fish:1412305740927995935>",
    "아비도스 태양 잉어": "<:tire4fish:1412305754869858334>",
    "낚시의 가루": "<:fish_powder:1412305764055384135>",

    "두툼한 생고기": "<:meat:1412305771735158814>",
    "다듬은 생고기": "<:slice_meat:1412305780870352926>",
    "아비도스 두툼한 생고기": "<:tier4meat:1412305919575855206>",
    "수렵의 가루": "<:meat_powder:1412305926102450237>",

    "들꽃": "<:flower:1412306240532647956>",
    "수줍은 들꽃": "<:cute_flower:1412306251731435651>",
    "아비도스 들꽃": "<:tier4flower:1412306263550726216>",
    "채집의 가루": "<:flower_powder:1412306271050403881>",
}

def emojify(name: str) -> str:
    try:
        e = NAME_EMOJI.get(name)
    except Exception:
        e = None
    return f"{e} {name}" if e else name

def _safe_int(x) -> int:
    try:
        return max(0, int(str(x).strip()))
    except:
        return 0

RATES = {
    "StoA": {"input": 5,  "output": 50},
    "BtoA": {"input": 25, "output": 50},
    "AtoP": {"input": 100,"output": 80},
    "BtoP": {"input": 50, "output": 80},
    "PtoC": {"input": 100,"output": 10},
}

def smart_range(stop: int, steps: int = 50):
    if stop <= steps:
        return range(stop)
    
    step_size = stop // steps
    return range(0, stop, step_size)

def compute_best_production(selected: str, resources: Dict[str, int], recipe: Dict[str, int]) -> Tuple[int, Dict[str, int]]:
    life = LIFE_MATS[selected]; show_s = life["show_S"]
    A = _safe_int(resources.get("A", 0))
    B = _safe_int(resources.get("B", 0))
    C = _safe_int(resources.get("C", 0))
    P = _safe_int(resources.get("P", 0))
    S = _safe_int(resources.get("S", 0)) if show_s else 0

    exchanges = {"StoA": 0, "BtoA": 0, "AtoP": 0, "BtoP": 0, "PtoC": 0}
    
    if show_s and S > 0:
        stoA = S // RATES["StoA"]["input"]
        if stoA > 0:
            exchanges["StoA"] = stoA
            A += stoA * RATES["StoA"]["output"]
            S -= stoA * RATES["StoA"]["input"]

    maxO = -1
    best_ex = dict(exchanges)
    
    canBtoA = (selected in ("WL", "MN"))
    maxBtoA = (B // RATES["BtoA"]["input"]) if canBtoA else 0

    LOOP_LIMIT = 40 

    for BtoA in smart_range(maxBtoA + 1, LOOP_LIMIT):
        tA = A + BtoA * RATES["BtoA"]["output"]
        tB = B - BtoA * RATES["BtoA"]["input"]
        
        rP_base = P

        base_pre = min(tA // recipe["A"], tB // recipe["B"], C // recipe["C"])
        
        rA = tA - base_pre * recipe["A"]
        rB = tB - base_pre * recipe["B"]
        rC = C - base_pre * recipe["C"]
        rP = rP_base

        maxAtoP = rA // RATES["AtoP"]["input"]
        
        for AtoP in smart_range(maxAtoP + 1, LOOP_LIMIT):
            curA = rA - AtoP * RATES["AtoP"]["input"]
            totP = rP + AtoP * RATES["AtoP"]["output"]
            
            # 남은 rB에 대해
            maxBtoP = rB // RATES["BtoP"]["input"]
            
            for BtoP in smart_range(maxBtoP + 1, LOOP_LIMIT):
                curB = rB - BtoP * RATES["BtoP"]["input"]
                curP = totP + BtoP * RATES["BtoP"]["output"]

                PtoC = curP // RATES["PtoC"]["input"]
                curC = rC + PtoC * RATES["PtoC"]["output"]

                # 추가 생산분
                added = min(curA // recipe["A"], curB // recipe["B"], curC // recipe["C"])
                total_produced = base_pre + added
                
                if total_produced > maxO:
                    maxO = total_produced
                    best_ex = {**exchanges, "BtoA": BtoA, "AtoP": AtoP, "BtoP": BtoP, "PtoC": PtoC}

    return max(0, maxO), best_ex

class CalcCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._market_cache: dict[str, dict] = {}
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._ttl_default = 180
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _fetch_market_price_min(self, item_name: str, api_key: str, *, category_code: int | None = None, ttl: int | None = None):
        url = "https://developer-lostark.game.onstove.com/markets/items"
        headers = {"accept": "application/json", "authorization": f"bearer {api_key}", "Content-Type": "application/json"}
        payload = {"ItemName": item_name, "SortCondition": "ASC"}
        if category_code:
            payload["CategoryCode"] = category_code

        ttl = int(ttl if ttl is not None else self._ttl_default)
        key = f"{api_key}::{category_code or 0}::{item_name}"
        now = time.time()

        cached = self._market_cache.get(key)
        if cached and cached["expires"] > now:
            return dict(cached["value"], cached_at=cached["cached_at"])

        lock = self._key_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._market_cache.get(key)
            if cached and cached["expires"] > time.time():
                return dict(cached["value"], cached_at=cached["cached_at"])

            try:
                r = await self.http_client.post(url, headers=headers, json=payload)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                if category_code and e.response is not None and e.response.status_code == 400:
                    payload.pop("CategoryCode", None)
                    r = await self.http_client.post(url, headers=headers, json=payload)
                    r.raise_for_status()
                else:
                    raise
            except Exception:
                pass
            
            try:
                data = r.json()
            except:
                data = {}

            items = data.get("Items") or []
            if not items:
                miss = {"CurrentMinPrice": None, "RecentPrice": None, "YDayAvgPrice": None, "BundleCount": None}
                entry = {"expires": time.time() + 60, "cached_at": time.time(), "value": miss}
                self._market_cache[key] = entry
                return dict(miss, cached_at=entry["cached_at"])

            first = items[0]
            value = {
                "CurrentMinPrice": first.get("CurrentMinPrice"),
                "RecentPrice": first.get("RecentPrice"),
                "YDayAvgPrice": first.get("YDayAvgPrice"),
                "BundleCount": first.get("BundleCount"),
            }
            entry = {"expires": time.time() + ttl, "cached_at": time.time(), "value": value}
            self._market_cache[key] = entry
            return dict(value, cached_at=entry["cached_at"])

    # --- price helpers ---
    @staticmethod
    def _pick_recent(item: dict) -> Decimal | None:
        for k in ("RecentPrice", "CurrentMinPrice", "YDayAvgPrice"):
            v = (item or {}).get(k)
            if v is not None:
                return Decimal(str(v))
        return None

    @staticmethod
    def _unit_price(price: Decimal | None, bundle: int | None) -> Decimal | None:
        if price is None:
            return None
        b = Decimal(str(bundle or 1))
        if b <= 0:
            b = Decimal("1")
        return price / b

    @staticmethod
    def _fee_per_unit_ceil(sale_unit_price: Decimal) -> Decimal:
        return (sale_unit_price * FEE_PCT / Decimal("100")).to_integral_value(rounding=ROUND_CEILING)

    @staticmethod
    def _fmt_money(x: Decimal | float | int) -> str:
        d = (x if isinstance(x, Decimal) else Decimal(str(x)))
        return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}🪙"

    @discord.slash_command(name="제작", description="생활 재료 최적 계산기에요.")
    @discord.option("제작", description="제작 아이템", type=str,
        choices=[
            discord.OptionChoice(name="아비도스 융화 재료", value="ABYDOS"),
            discord.OptionChoice(name="상급 아비도스 융화 재료", value="ABYDOS_PLUS"),
        ])
    @discord.option("분야", description="생활 분야", type=str,
        choices=[
            discord.OptionChoice(name="벌목", value="WL"),
            discord.OptionChoice(name="채광", value="MN"),
            discord.OptionChoice(name="고고학", value="AR"),
            discord.OptionChoice(name="낚시", value="FS"),
            discord.OptionChoice(name="수렵", value="HR"),
            discord.OptionChoice(name="채집", value="CL"),
        ])
    async def craft_calc(self, ctx: discord.ApplicationContext, 제작: str, 분야: str):
        await ctx.response.send_modal(CraftModal(분야, 제작, self))

class CraftModal(discord.ui.Modal):
    def __init__(self, life_code: str, craft_key: str, cog: CalcCog):
        life = LIFE_MATS[life_code]
        craft_cfg = CRAFT_TABLE.get(craft_key) or CRAFT_TABLE[DEFAULT_CRAFT_KEY]
        super().__init__(title=f"🛠️ {craft_cfg['name']} 제작 계산 — {life['name']}", timeout=180.0)
        self.cog = cog
        self.life_code = life_code
        self.life = life
        self.craft_key = craft_key
        self.craft_cfg = craft_cfg
        labels = life["labels"]

        self.input_A = discord.ui.InputText(label=f"{labels['A']} 수량", placeholder="정수", required=False)
        self.input_B = discord.ui.InputText(label=f"{labels['B']} 수량", placeholder="정수", required=False)
        self.input_S = discord.ui.InputText(label=f"{labels['S']} 수량 (3티어)", placeholder="정수", required=False) if life["show_S"] else None
        self.input_C = discord.ui.InputText(label=f"{labels['C']} 수량 (4티어)", placeholder="정수", required=False)
        self.input_P = discord.ui.InputText(label=f"{labels['P']} 수량", placeholder="정수", required=False)

        self.add_item(self.input_A)
        self.add_item(self.input_B)
        if self.input_S:
            self.add_item(self.input_S)
        self.add_item(self.input_C)
        self.add_item(self.input_P)

    async def callback(self, interaction: discord.Interaction):
        start_ts = time.time()
        start_perf = time.perf_counter()

        labels = self.life["labels"]
        res = {
            "A": _safe_int(self.input_A.value or 0),
            "B": _safe_int(self.input_B.value or 0),
            "C": _safe_int(self.input_C.value or 0),
            "P": _safe_int(self.input_P.value or 0),
        }
        if self.input_S:
            res["S"] = _safe_int(self.input_S.value or 0)

        def _lines_in():
            out = []
            for k in ("A","B","S","C","P"):
                if k == "S" and not self.life.get("show_S", False):
                    continue
                out.append(f"• **{emojify(labels.get(k,k))}**: `{res.get(k,0):,}`")
            return "\n".join(out) or "없음"

        emb_wait = discord.Embed(
            title=f"계산중 — {self.life['name']}",
            description="최적의 제작 경로를 계산하고 있습니다...",
            color=discord.Color.gold(),
        )
        emb_wait.add_field(name="입력 자원", value=_lines_in(), inline=False)
        await interaction.response.send_message(embed=emb_wait, ephemeral=True)

        total, logs = await asyncio.to_thread(compute_best_production, self.life_code, res, self.craft_cfg['recipe'])

        end_ts = time.time()
        end_perf = time.perf_counter()
        duration_sec = end_perf - start_perf
        time_str = f"{duration_sec:.2f}초"
        start_str = datetime.fromtimestamp(start_ts).strftime('%H:%M:%S')
        end_str = datetime.fromtimestamp(end_ts).strftime('%H:%M:%S')
        
        calc_info_str = f"시작: `{start_str}` | 종료: `{end_str}` | 소요: `{time_str}`"

        trans_label = {"StoA": (labels.get("S","S"), labels["A"]),
                    "BtoA": (labels["B"], labels["A"]),
                    "AtoP": (labels["A"], labels["P"]),
                    "BtoP": (labels["B"], labels["P"]),
                    "PtoC": (labels["P"], labels["C"])}
        lines_log = []
        for key, cnt in logs.items():
            if cnt > 0 and key in trans_label:
                f, t = trans_label[key]
                lines_log.append(f"• **{emojify(f)} → {emojify(t)}**: `{cnt:,}`회")

        emb_done = discord.Embed(
            title=f"제작 결과 — {self.life['name']}",
            description="입력 자원을 최적으로 교환하여 제작 가능한 최대 횟수에요.",
            color=discord.Color.gold() if total > 0 else discord.Color.red(),
        )
        emb_done.add_field(name="입력 자원", value=_lines_in(), inline=False)
        emb_done.add_field(name="교환 내역", value="\n".join(lines_log) if lines_log else "교환 없음", inline=False)
        emb_done.add_field(name=f"최대 제작({self.craft_cfg['name']})", value=f"**`{total:,}` 회** (1회 = {int(CRAFT_UNIT)}개)", inline=False)
        emb_done.add_field(name="⏱️ 계산 시간", value=calc_info_str, inline=False)

        view = PriceCalcView(
            life_code=self.life_code,
            craft_key=self.craft_key,
            cog=self.cog,
            initial_embed=emb_done,
            resources=res,
            total=total,
            logs=logs,
            prev_time_info=calc_info_str
        )

        await interaction.edit_original_response(embed=emb_done, view=view)


class PriceCalcView(discord.ui.View):
    def __init__(self, life_code: str, craft_key: str, cog: CalcCog, initial_embed: discord.Embed, resources: Dict[str,int], total: int, logs: Dict[str,int], prev_time_info: str):
        super().__init__(timeout=180.0)
        self.life_code = life_code
        self.craft_key = craft_key
        self.craft_cfg = CRAFT_TABLE.get(craft_key) or CRAFT_TABLE[DEFAULT_CRAFT_KEY]
        self.cog = cog
        self.initial_embed = initial_embed
        self.resources = resources
        self.total = total
        self.logs = logs
        self.prev_time_info = prev_time_info

    @discord.ui.button(label="시세 계산 하기", style=discord.ButtonStyle.primary, custom_id="mococo_price_calc")
    async def calc_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        button.disabled = True
        await interaction.response.edit_message(embed=self.initial_embed, view=self)

        # ---- 시세 계산 시작 ----
        life = LIFE_MATS[self.life_code]
        labels = life["labels"]

        craft_cfg = self.craft_cfg
        recipe = craft_cfg['recipe']
        gold_per_craft = craft_cfg['gold_per_craft']
        craft_unit = craft_cfg['craft_unit']

        fetch = {}
        for k in ("A","B","C","P"):
            fetch[k] = await self.cog._fetch_market_price_min(labels[k], LOSTARK_API_SUB1_KEY, category_code=CATEGORY_FOR_LIFE[self.life_code])
        if life.get("show_S", False):
            fetch["S"] = await self.cog._fetch_market_price_min(labels["S"], LOSTARK_API_SUB1_KEY, category_code=CATEGORY_FOR_LIFE[self.life_code])
        prod = await self.cog._fetch_market_price_min(self.craft_cfg['market_name'], LOSTARK_API_SUB2_KEY, category_code=CATEGORY_FUSION_ABYDOS)

        def U(d):
            base = self.cog._pick_recent(d)
            bundle = (d or {}).get("BundleCount") or 1
            return (self.cog._unit_price(base, bundle), Decimal(str(bundle)), base)

        A_u, Au_b, A_base = U(fetch["A"])
        B_u, Bu_b, B_base = U(fetch["B"])
        C_u, Cu_b, C_base = U(fetch["C"])
        P_u, Pu_b, P_base = U(fetch["P"])
        if life.get("show_S", False):
            S_u, Su_b, S_base = U(fetch.get("S"))
        else:
            S_u = Su_b = S_base = None
        prod_u, Pr_b, Pr_base = U(prod)

        crafts = max(0, self.total)

        needA = Decimal(recipe["A"]); needB = Decimal(recipe["B"]); needC = Decimal(recipe["C"])
        mats_cost_per_craft = (needA*(A_u or 0) + needB*(B_u or 0) + needC*(C_u or 0)) 
        craft_cost_per_craft = mats_cost_per_craft + gold_per_craft
        craft_cost_per_unit  = (craft_cost_per_craft / craft_unit)
        total_craft_cost     = craft_cost_per_craft * Decimal(crafts)

        prod_u_val = prod_u or Decimal(0)
        fee_per_unit    = self.cog._fee_per_unit_ceil(prod_u_val)
        cost_per_unit   = craft_cost_per_unit + fee_per_unit
        profit_per_unit = (prod_u_val - cost_per_unit)

        produced_units = Decimal(crafts) * craft_unit

        fee_total    = fee_per_unit * produced_units
        total_cost   = cost_per_unit * produced_units
        total_profit = profit_per_unit * produced_units

        # 지표
        roi_cost_basis = ((profit_per_unit*craft_unit)/craft_cost_per_craft*Decimal("100")) if craft_cost_per_craft>0 else Decimal("0")
        roi_energy     = ((profit_per_unit*craft_unit)/ENERGY_PER_CRAFT  *Decimal("100")) if ENERGY_PER_CRAFT>0 else Decimal("0")

        # 경매장가 라인
        m = self.cog._fmt_money
        price_lines = [
            f"• {emojify(labels['A'])} 묶음({int(Au_b)}개): `{m(A_base or Decimal('0'))}` / 개당 `{m(A_u or Decimal('0'))}`",
            f"• {emojify(labels['B'])} 묶음({int(Bu_b)}개): `{m(B_base or Decimal('0'))}` / 개당 `{m(B_u or Decimal('0'))}`",
            f"• {emojify(labels['C'])} 묶음({int(Cu_b)}개): `{m(C_base or Decimal('0'))}` / 개당 `{m(C_u or Decimal('0'))}`",
        ]
        if life.get("show_S", False):
            price_lines.append(f"• {emojify(labels['S'])} 묶음({int(Su_b)}개): `{m(S_base or Decimal('0'))}` / 개당 `{m(S_u or Decimal('0'))}`")
        price_lines.append(f"• {self.craft_cfg['name']} 판매단위({int(Pr_b)}개): `{m(Pr_base or Decimal('0'))}` / 개당 `{m(prod_u or Decimal('0'))}` (제작 1회 = {int(craft_unit)}개)")

        pct = lambda x: f"{x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
        order = RECIPE_ORDER.get(self.life_code, ("A","B","C"))
        qty_map  = {"A": Decimal(recipe["A"]), "B": Decimal(recipe["B"]), "C": Decimal(recipe["C"])}
        unit_map = {"A": A_u or 0, "B": B_u or 0, "C": C_u or 0}
        
        mats_lines = []
        for key in order:
            cost_part = qty_map[key] * unit_map[key]
            mats_lines.append(
                f"• {emojify(labels[key])}: 필요 `{int(recipe[key])}`개, 단가 `{m(unit_map[key])}`, 합계 `{m(cost_part)}`"
            )
        mats_lines.append(f"• 골드: `{int(gold_per_craft):,}`")

        emb_final = discord.Embed(
            title=f"가격·원가·이익 — {life['name']} ({self.craft_cfg['name']})",
            color=discord.Color.gold()
        )
        emb_final.add_field(name=f"최대 제작({self.craft_cfg['name']})", value=f"**`{self.total:,}` 회** (1회 = {int(craft_unit)}개)", inline=False)
        
        # 교환 내역 표시
        if self.logs:
            trans_label = {"StoA": (labels.get("S","S"), labels["A"]),
                           "BtoA": (labels["B"], labels["A"]),
                           "AtoP": (labels["A"], labels["P"]),
                           "BtoP": (labels["B"], labels["P"]),
                           "PtoC": (labels["P"], labels["C"])}
            lines_log = []
            for k, cnt in self.logs.items():
                if cnt>0 and k in trans_label:
                    f,t = trans_label[k]; lines_log.append(f"• **{emojify(f)} → {emojify(t)}**: `{cnt:,}`회")
            emb_final.add_field(name="교환 내역", value="\n".join(lines_log) if lines_log else "교환 없음", inline=False)

        # 시간 정보 유지
        emb_final.add_field(name="⏱️ 수량 계산 시간", value=self.prev_time_info, inline=False)

        emb_final.add_field(name="경매장 시세", value="\n".join(price_lines), inline=False)
        emb_final.add_field(
            name="**제작정보**",
            value="\n".join([
                f"• 활동력: `{int(ENERGY_PER_CRAFT)}`",
                f"• 제작시간: `{int(TIME_PER_CRAFT)}`초",
                f"• 경험치: `{int(XP_PER_CRAFT)}`",
                f"• 판매단위 당 제작비용: `{m(craft_cost_per_unit)}`",
                f"• 제작단위 당 제작비용({int(craft_unit)}개): `{m(craft_cost_per_craft)}`",
                f"• 제작 묶음수량(자동): `{crafts}`",
                f"• 총 제작비용: `{m(total_craft_cost)}`",
            ]),
            inline=True,
        )
        emb_final.add_field(
            name="**판매정보**",
            value="\n".join([
                f"• 시세: `{m(prod_u_val)}`",
                f"• 판매단위 당 수수료(ceil): `{m(fee_per_unit)}`",
                f"• 판매단위 당 원가: `{m(cost_per_unit)}`",
                f"• 판매단위 당 판매차익: `{m(profit_per_unit)}`",
                f"• 판매 수량(자동): `{int(produced_units):,}`개",
                f"• 총 수수료: `{m(fee_total)}`",
                f"• 총 원가: `{m(total_cost)}`",
                f"• 총 판매차익: `{m(total_profit)}`",
                f"• 원가 대비 이익률: `{pct(roi_cost_basis)}`",
                f"• 활동력 대비 이익률: `{pct(roi_energy)}`",
            ]),
            inline=True,
        )
        emb_final.add_field(name="재료정보", value="\n".join(mats_lines), inline=False)
        # === 최종 판정 ===
        if crafts <= 0 or produced_units <= 0:
            verdict = "➖ 제작 가능한 수량이 없어 판정할 수 없어요."
        else:
            if profit_per_unit > 0:
                verdict = f"✅ **제작해서 파는 게 이득이에요.** (총 이득: `{m(total_profit)}`)"
            elif profit_per_unit < 0:
                verdict = f"✅ **재료 그대로 파는 게 이득이에요.** (손해 회피: `{m(-total_profit)}`)"
            else:
                verdict = "➖ 차이 없음"

        emb_final.add_field(name=" ", value=verdict, inline=False)

        ts = []
        for d in list(fetch.values()) + [prod]:
            if isinstance(d, dict) and d.get("cached_at"):
                ts.append(d["cached_at"])
        cached_str = datetime.fromtimestamp(min(ts) if ts else time.time()).strftime("%Y-%m-%d %H:%M")
        emb_final.set_footer(text=f"( 시세 기준: {cached_str} )")

        await interaction.edit_original_response(embed=emb_final, view=self)

# ====== setup ======
def setup(bot: discord.AutoShardedBot):
    bot.add_cog(CalcCog(bot))