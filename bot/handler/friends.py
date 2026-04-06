import math
import time
from collections import OrderedDict

import discord
from core.http_client import http_client

# ========= consts / utils =========

_COLOR = discord.Color.from_rgb(90, 115, 255)
_AUTHOR = "🎭 익명 매칭"
_FOOTER = "좋아요를 누르면 상대에게도 알림이 가요. 서로 좋아요면 매칭!"

def _pick(d: dict, *keys, default=None):
    """여러 키 후보 중 첫 번째 유효값 반환"""
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default

def _bucket_ilvl(v) -> str:
    """아이템 레벨을 10 단위 버킷으로 표기"""
    if not v:
        return "??+"
    try:
        f = float(str(v).replace(",", "").replace("+", "").strip())
        return f"{int(math.floor(f / 10.0) * 10)}+"
    except (TypeError, ValueError):
        return "??+"

def _embed(title: str, description: str = "", fields: list | None = None) -> discord.Embed:
    e = discord.Embed(title=title, description=description or "", color=_COLOR)
    e.set_author(name=_AUTHOR)
    if fields:
        for name, value, inline in fields:
            e.add_field(name=name, value=value, inline=inline)
    e.set_footer(text=_FOOTER)
    return e

# ========= 효율적인 TTL 캐시 구현 =========

class _TTLCache:
    def __init__(self, maxsize=256, ttl=120):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[int, tuple] = OrderedDict()
        self._last_cleanup = time.time()
        self._cleanup_interval = max(60, ttl // 2)
    
    def get(self, key):
        now = time.time()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_expired(now)
            
        item = self._data.get(key)
        if not item:
            return None
        value, exp = item
        if exp < now:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value
    
    def set(self, key, value):
        now = time.time()
        self._data[key] = (value, now + self.ttl)
        self._data.move_to_end(key)
        if len(self._data) > self.maxsize:
            self._data.popitem(last=False)
            
    def _cleanup_expired(self, now=None):
        """만료된 항목 일괄 삭제"""
        if now is None:
            now = time.time()
        expired_keys = [k for k, (_, exp) in self._data.items() if exp < now]
        for k in expired_keys:
            del self._data[k]
        self._last_cleanup = now
        
    def clear(self):
        self._data.clear()
        
    def invalidate(self, key):
        """특정 키만 무효화"""
        if key in self._data:
            del self._data[key]

PROFILE_CACHE = _TTLCache(maxsize=128, ttl=120)
PARTNER_CACHE = _TTLCache(maxsize=256, ttl=10)
HAS_PROFILE_CACHE = _TTLCache(maxsize=256, ttl=300)

async def _fetch_profile_cached(user_id: int):
    """프로필 조회"""
    cached = PROFILE_CACHE.get(user_id)
    if cached is not None:
        return cached
    
    r = await http_client.get(f"/friends/profile/{user_id}")
    if r.status_code != 200:
        return None
        
    data = r.json() or {}
    PROFILE_CACHE.set(user_id, data)
    
    if data.get("has_profile"):
        HAS_PROFILE_CACHE.set(user_id, True)
    return data

async def _is_already_matched(inter: discord.Interaction) -> bool:
    """매칭 상태 확인"""
    cached = PARTNER_CACHE.get(inter.user.id)
    if cached is not None:
        if cached:
            await inter.response.send_message(
                "❌ 이미 매칭 중입니다! 새로운 친구를 찾으려면 `/매칭해제`를 먼저 해주세요.",
                ephemeral=True
            )
        return cached

    try:
        r = await http_client.get(f"/friends/partner?user_id={inter.user.id}")
        is_matched = (r.status_code == 200) and ((r.json() or {}).get("partner_id") is not None)
    except Exception:
        is_matched = False

    PARTNER_CACHE.set(inter.user.id, is_matched)
    if is_matched:
        await inter.response.send_message(
            "❌ 이미 매칭 중입니다! 새로운 친구를 찾으려면 `/매칭해제`를 먼저 해주세요.",
            ephemeral=True
        )
    return is_matched

LIKE_NOTIFY_CACHE = _TTLCache(maxsize=2048, ttl=180)

async def _notify_target_of_like(bot: discord.Client, liker_id: int, target_id: int) -> bool:
    """
    타겟에게 '누군가 당신을 좋아요' DM 전송.
    버튼 custom_id는 ff_like:{liker_id} 로 설정하여 상대가 '맞좋아요'를 누르면 즉시 매칭되도록 한다.
    """
    # 중복 방지 (짧은 TTL)
    cache_key = (int(liker_id), int(target_id))
    if LIKE_NOTIFY_CACHE.get(cache_key):
        return True

    # 좋아요 보낸 사람(=liker)의 프로필로 카드 구성
    liker_profile = await _fetch_profile_cached(int(liker_id))
    p = (liker_profile or {}).get("profile", {}) or {}
    ch = p.get("character", {}) or {}

    detail_cls = _pick(ch, "class_name", "class", "job_name") or "?"
    detail_ilv = _bucket_ilvl(_pick(ch, "item_level", "item_lvl", "ilvl", "itemLv"))
    # DM 임베드
    fields = [
        ("⚔️ 직업", f"**{detail_cls}**", True),
        ("💎 아이템 레벨", f"**{detail_ilv}**", True),
    ]
    desc = (
        "익명의 누군가가 대화를 원해요!\n"
        "아래 **[좋아요]**를 눌러 서로 매칭을 맺어보세요."
    )
    dm_embed = _embed("누군가가 당신에게 **좋아요**를 보냈어요! 💌", description=desc, fields=fields)

    # 버튼: 타겟이 누르면 liker에게 '맞좋아요'
    view = discord.ui.View(timeout=120)
    view.add_item(discord.ui.Button(
        label="좋아요",
        style=discord.ButtonStyle.success,
        custom_id=f"ff_like:{int(liker_id)}"
    ))

    try:
        other = await bot.fetch_user(int(target_id))
        await other.send(embed=dm_embed, view=view)
        LIKE_NOTIFY_CACHE.set(cache_key, True)
        return True
    except Exception:
        return False

def _extract_profile_data(cand: dict, profile_payload: dict | None):
    """효율적인 프로필 데이터 추출"""
    if not profile_payload:
        return {
            "detail_cls": _pick(cand, "class_name") or "?",
            "detail_ilv": _bucket_ilvl(_pick(cand, "item_lvl", "item_level")),
            "char_name": _pick(cand, "char_name") or "익명 사용자",
            "class_emoji": "⚔️",
            "intro": ""
        }
    
    p = profile_payload.get("profile", {}) or {}
    ch = p.get("character", {}) or {}
    
    return {
        "detail_cls": _pick(ch, "class_name", "class", "job_name") or _pick(cand, "class_name") or "?",
        "detail_ilv": _bucket_ilvl(_pick(ch, "item_level", "item_lvl", "ilvl", "itemLv") or _pick(cand, "item_lvl", "item_level")),
        "char_name": _pick(ch, "name", "char_name", "character_name") or "익명 사용자",
        "class_emoji": _pick(ch, "class_emoji") or "⚔️",
        "intro": (_pick(p, "intro") or "").strip()
    }

def _build_candidate_view_and_embed(profile_data: dict, target_user_id: int):
    """후보 표시 생성"""
    fields = [
        (f"{profile_data['class_emoji']} 직업", f"**{profile_data['detail_cls']}**", True),
        ("💎 아이템 레벨", f"**{profile_data['detail_ilv']}**", True),
    ]
    
    if profile_data["intro"]:
        fields.append(("📝 자기소개", profile_data["intro"], False))
    else:
        fields.append(("📝 자기소개", "*자기소개를 작성하지 않은 사용자입니다.*", False))

    emb = _embed("익명 프로필", description="", fields=fields)
    
    # 버튼 재사용 방지
    view = discord.ui.View(timeout=120)
    view.add_item(discord.ui.Button(
        label="❤️ 좋아요", 
        style=discord.ButtonStyle.success, 
        custom_id=f"ff_like:{target_user_id}"
    ))
    view.add_item(discord.ui.Button(
        label="➡️ 넘기기", 
        style=discord.ButtonStyle.secondary, 
        custom_id=f"ff_pass:{target_user_id}"
    ))
    
    return emb, view

async def _safe_send_or_edit(
    inter: discord.Interaction, *, content: str | None = None, embed: discord.Embed | None = None,
    view: discord.ui.View | None = None, ephemeral: bool = True, edit: bool = False
):
    """메시지 전송/편집 함수"""
    try:
        if edit:
            if not inter.response.is_done():
                await inter.response.edit_message(content=content, embed=embed, view=view)
                return
                
            try:
                await inter.edit_original_response(content=content, embed=embed, view=view)
            except:
                await inter.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)
        else:
            if not inter.response.is_done():
                await inter.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral)
                return
                
            await inter.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)
    except:
        pass

async def _try_dm_user(user: discord.User | discord.Member, *, embed: discord.Embed | None = None, text: str | None = None):
    """DM 전송 함수"""
    try:
        if embed is not None:
            await user.send(embed=embed)
        elif text:
            await user.send(text)
        return True
    except:
        return False

# ========= 모달 =========

class ProfileModal(discord.ui.Modal):
    """프로필 등록 모달"""
    def __init__(self):
        super().__init__(title="프로필 등록")
        
        self.nickname = discord.ui.InputText(
            label="캐릭터 닉네임 (정확히 입력)",
            max_length=20,
            placeholder="예) 조교병"
        )
        self.add_item(self.nickname)
        
        self.intro = discord.ui.InputText(
            label="자기소개 (선택사항)",
            style=discord.InputTextStyle.paragraph,
            max_length=300,
            placeholder="예) 카제로스 레이드 클리어 목표! 친목 위주로 활동하고 있어요 :)",
            required=False
        )
        self.add_item(self.intro)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        nickname = (self.nickname.value or "").strip()
        intro_text = (self.intro.value or "").strip()
        
        if not nickname:
            return await interaction.followup.send("닉네임을 입력해 주세요.", ephemeral=True)

        # 캐릭터 검색 및 프로필 등록 병합
        resp = await http_client.get(f"/character/search/{nickname}")
        if resp.status_code != 200:
            return await interaction.followup.send("❌ 캐릭터 검색에 실패했어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)

        payload = resp.json() or {}
        item = _pick(payload, "data", default=payload) or {}
        
        char_id = _pick(item, "id")
        if not char_id:
            return await interaction.followup.send("❌ 캐릭터 정보를 찾을 수 없습니다.", ephemeral=True)
            
        char_name = _pick(item, "char_name") or nickname
        class_name = _pick(item, "class_name") or "?"
        item_lvl = _pick(item, "item_lvl") or 0
        # 프로필 저장 (단일 API 호출)
        save_resp = await http_client.post("/friends/profile", json={
            "user_id": interaction.user.id,
            "character_id": int(char_id),
            "intro": intro_text
        })
        
        if save_resp.status_code == 200:
            HAS_PROFILE_CACHE.set(interaction.user.id, True)
            PROFILE_CACHE.invalidate(interaction.user.id)  # 특정 키만 무효화
            
            fields = [
                ("⚔️ 직업", f"**{class_name}**", True),
                ("💎 아이템 레벨", f"**{item_lvl}**", True),
            ]
            if intro_text:
                fields.append(("📝 자기소개", intro_text, False))

            emb = _embed(
                "✅ 프로필 등록 완료!",
                description=f"**{char_name}**\n\n이제 친구 매칭을 시작할 수 있어요!",
                fields=fields
            )
            await interaction.followup.send(embed=emb, ephemeral=True)
        else:
            error_data = {}
            if save_resp.status_code != 500:
                try:
                    error_data = save_resp.json()
                except:
                    pass
            error_msg = error_data.get("detail", f"저장 실패 (상태: {save_resp.status_code})")
            await interaction.followup.send(f"❌ {error_msg}", ephemeral=True)

# ========= 엔트리 / 액션 =========

async def open_profile_modal(interaction: discord.Interaction):
    """프로필 등록 모달 열기"""
    await interaction.response.send_modal(ProfileModal())

async def open_match_candidate(interaction: discord.Interaction):
    """친구 매칭"""
    # 매칭 상태 확인
    if await _is_already_matched(interaction):
        return

    # 내 프로필 확인 (캐시 우선)
    has_profile = HAS_PROFILE_CACHE.get(interaction.user.id)
    if has_profile is None:
        profile_resp = await http_client.get(f"/friends/profile/{interaction.user.id}")
        if profile_resp.status_code == 404:
            return await interaction.response.send_message(
                "❌ 먼저 프로필을 등록해야 친구 매칭을 할 수 있어요!\n\n**[프로필 등록]** 버튼을 눌러 캐릭터를 등록해 주세요.",
                ephemeral=True
            )
        elif profile_resp.status_code != 200:
            return await interaction.response.send_message(
                "❌ 프로필 확인 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
                ephemeral=True
            )
            
        profile_data = profile_resp.json() or {}
        profile = profile_data.get("profile", {}) or {}
        has_profile = profile.get("is_active", False)
        
        if not has_profile:
            return await interaction.response.send_message(
                "❌ 프로필이 비활성화 상태입니다. 프로필을 다시 등록해 주세요.",
                ephemeral=True
            )
            
        # 캐시 업데이트
        HAS_PROFILE_CACHE.set(interaction.user.id, True)
        PROFILE_CACHE.set(interaction.user.id, profile_data)

    # 후보 조회 및 표시 (효율적인 단일 API 호출)
    resp = await http_client.get(f"/friends/candidate?user_id={interaction.user.id}")
    if resp.status_code != 200:
        return await interaction.response.send_message("❌ 후보 조회 실패", ephemeral=True)

    cand = resp.json()
    if not cand:
        return await interaction.response.send_message("표시할 후보가 없어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)

    # 후보 프로필 조회 (캐시 활용)
    pr = await _fetch_profile_cached(int(cand["user_id"]))
    profile_data = _extract_profile_data(cand, pr)
    emb, view = _build_candidate_view_and_embed(profile_data, cand["user_id"])
    
    await interaction.response.send_message(embed=emb, view=view, ephemeral=True)

async def handle_like(interaction: discord.Interaction, target_user_id: int):
    """좋아요 처리"""
    # 매칭 상태 확인
    if await _is_already_matched(interaction):
        return

    # 좋아요 요청 (단일 API 호출)
    r = await http_client.post("/friends/like", json={"viewer_id": interaction.user.id, "target_id": target_user_id})
    data = r.json() if r.status_code == 200 else {}
    
    # 매칭 성사 시 처리
    if data.get("matched"):
        await interaction.response.send_message("🎉 매칭 성사! 이제 **봇에게 DM**을 보내면 상대에게 전달돼요.", ephemeral=True)
        
        # 프로필 정보 조회
        target_profile = None
        my_profile = None
        
        try:
            # 동시에 프로필 요청
            target_profile_task = _fetch_profile_cached(int(target_user_id))
            my_profile_task = _fetch_profile_cached(int(interaction.user.id))
            
            target_profile = await target_profile_task
            my_profile = await my_profile_task
            
            if target_profile and my_profile:
                # 상대방 정보 추출
                target_char = (target_profile.get("profile", {}) or {}).get("character", {}) or {}
                my_char = (my_profile.get("profile", {}) or {}).get("character", {}) or {}
                
                # 임베드 구성
                t_fields = [
                    ("⚔️ 직업", f"**{_pick(target_char, 'class_name') or '?'}**", True),
                    ("💎 아이템 레벨", f"**{_bucket_ilvl(_pick(target_char, 'item_level'))}**", True)
                ]
                
                m_fields = [
                    ("⚔️ 직업", f"**{_pick(my_char, 'class_name') or '?'}**", True),
                    ("💎 아이템 레벨", f"**{_bucket_ilvl(_pick(my_char, 'item_level'))}**", True)
                ]
                
                t_embed = _embed(
                    "🎉 누군가와 매칭 되었어요!",
                    description="\n이제 봇으로 DM을 보내면 상대에게 전달됩니다!",
                    fields=t_fields
                )
                
                m_embed = _embed(
                    "🎉 누군가와 매칭 되었어요!",
                    description="\n이제 봇으로 DM을 보내면 상대에게 전달됩니다!",
                    fields=m_fields
                )
                
                # DM 전송
                dm_sent = await _try_dm_user(interaction.user, embed=t_embed)
                if not dm_sent:
                    await interaction.followup.send(embed=t_embed, ephemeral=True)
                
                try:
                    other = await interaction.client.fetch_user(int(target_user_id))
                    await _try_dm_user(other, embed=m_embed)
                except:
                    pass
                
                # 캐시 무효화 (매칭 상태 변경)
                PARTNER_CACHE.invalidate(interaction.user.id)
                PARTNER_CACHE.invalidate(target_user_id)
                return
        except:
            pass
            
        # 기본 메시지 전송 (오류 발생 또는 프로필 없는 경우)
        base_msg = "✅ 매칭이 성사되었습니다. 여기로 메시지를 보내면 상대에게 전달됩니다."
        await _try_dm_user(interaction.user, text=base_msg)
        
        try:
            other = await interaction.client.fetch_user(int(target_user_id))
            await _try_dm_user(other, text=base_msg)
        except:
            pass
            
        return

    reason = data.get("reason")
    if reason == "awaiting_other_like":
        sent = await _notify_target_of_like(
            bot=interaction.client,
            liker_id=interaction.user.id,
            target_id=target_user_id
        )
        base_msg = "좋아요를 기록했어요! 상대방에게 알림을 보냈어요. 상대도 좋아요하면 매칭됩니다."
        if not sent:
            base_msg += "\n(상대가 DM을 차단했을 수도 있어요.)"
        return await interaction.response.send_message(base_msg, ephemeral=True)

    elif reason == "cooldown":
        secs = int(data.get("retry_after", 1800))
        m, s = divmod(secs, 60)
        msg = f"이미 좋아요를 보냈어요. **{m}분 {s}초** 뒤에 다시 보낼 수 있어요."
    elif reason == "viewer_already_matched":
        msg = "이미 매칭 중입니다! 새로운 친구를 찾으려면 `/매칭해제`를 먼저 해주세요."
    elif reason == "target_already_matched":
        msg = "상대가 이미 다른 매칭 중이에요."
    elif reason == "self_like_forbidden":
        msg = "자기 자신에게는 좋아요를 보낼 수 없어요."
    elif reason == "profile_missing":
        msg = "프로필이 없거나 비활성 상태예요. 먼저 프로필을 등록해 주세요."
    else:
        msg = "요청을 처리할 수 없어요. 잠시 후 다시 시도해 주세요."

    await interaction.response.send_message(msg, ephemeral=True)

async def handle_pass(interaction: discord.Interaction, target_user_id: int):
    """넘기기 처리"""
    # 매칭 상태 확인
    if await _is_already_matched(interaction):
        return

    # 패스 요청 (단일 API 호출)
    r = await http_client.post("/friends/pass", json={"viewer_id": interaction.user.id, "target_id": target_user_id})
    if r.status_code != 200:
        return await interaction.response.send_message("❌ 넘기기 실패", ephemeral=True)

    data = r.json() or {}
    cand = data.get("next")
    if not cand:
        # 다음 후보 없음
        try:
            await interaction.response.edit_message(content="표시할 후보가 없어요. 잠시 후 다시 시도해 주세요.", embed=None, view=None)
        except:
            await interaction.response.send_message("표시할 후보가 없어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)
        return

    # 다음 후보 프로필 조회 (캐시 활용)
    pr = await _fetch_profile_cached(int(cand["user_id"]))
    profile_data = _extract_profile_data(cand, pr)
    emb, view = _build_candidate_view_and_embed(profile_data, cand["user_id"])
    
    await _safe_send_or_edit(interaction, embed=emb, view=view, edit=True)

async def open_my_profile(interaction: discord.Interaction):
    """내 프로필"""
    # 캐시 활용
    data = PROFILE_CACHE.get(interaction.user.id)
    if data is None:
        r = await http_client.get(f"/friends/profile/{interaction.user.id}")
        if r.status_code == 404:
            return await interaction.response.send_message(
                "아직 프로필이 없어요. **/친구 → 프로필 등록**으로 만들어주세요!",
                ephemeral=True
            )
        if r.status_code != 200:
            return await interaction.response.send_message("❌ 프로필 조회 실패", ephemeral=True)
            
        data = r.json() or {}
        PROFILE_CACHE.set(interaction.user.id, data)
        HAS_PROFILE_CACHE.set(interaction.user.id, True)

    p = data.get("profile") or {}
    c = p.get("character") or {}
    stats = data.get("statistics") or {}
    cur = data.get("current_match") or {}

    fields = [
        ("⚔️ 직업", f"**{_pick(c, 'class_name', 'class', 'job_name') or '?'}**", True),
        ("💎 아이템 레벨", f"**{_bucket_ilvl(_pick(c, 'item_level', 'item_lvl', 'ilvl', 'itemLv'))}**", True),
        ("🧩 상태", f"{'✅ 매칭 중' if cur.get('is_matched') else '❌ 매칭 없음'} · {'🟢 활성' if p.get('is_active') else '⚪ 비활성'}", False),
    ]
    
    intro = (_pick(p, "intro") or "").strip()
    if intro:
        fields.append(("📝 자기소개", intro, False))

    fields.append((
        "📊 통계",
        f"- 본 프로필 수: **{stats.get('viewed_profiles', 0)}**\n"
        f"- 받은 좋아요: **{stats.get('received_likes', 0)}**\n"
        f"- 보낸 좋아요: **{stats.get('sent_likes', 0)}**\n"
        f"- 총 매칭 수: **{stats.get('total_matches', 0)}**",
        False
    ))

    emb = _embed("내 익명 프로필", description=f"**{_pick(c, 'name', 'char_name', 'character_name') or '?'}**", fields=fields)
    
    # 메모리 효율적인 View 생성
    v = discord.ui.View(timeout=90)
    v.add_item(discord.ui.Button(label="✂️ 프로필 삭제", style=discord.ButtonStyle.danger, custom_id="ff_profile_delete"))
    
    await interaction.response.send_message(embed=emb, view=v, ephemeral=True)

async def start_profile_delete(interaction: discord.Interaction):
    """프로필 삭제 시작"""
    notice = (
        "정말 삭제할까요?\n\n"
        "- **삭제**되어 더 이상 후보/매칭 대상에 노출되지 않습니다.\n"
        "- 이미 성사된 매칭의 DM 릴레이는 계속됩니다. 완전히 끊으려면 **/매칭해제**를 사용하세요."
    )
    emb = _embed("프로필 삭제 확인", description=notice)
    
    v = discord.ui.View(timeout=60)
    v.add_item(discord.ui.Button(label="✅ 삭제", style=discord.ButtonStyle.danger, custom_id="ff_profile_delete_confirm"))
    
    await interaction.response.send_message(embed=emb, view=v, ephemeral=True)

async def confirm_profile_delete(interaction: discord.Interaction):
    """프로필 삭제 확인 처리"""
    r = await http_client.delete(f"/friends/profile/{interaction.user.id}")
    ok = r.status_code == 200 and (r.json() or {}).get("ok")
    
    msg = "✅ 프로필이 **삭제**되었습니다. 이제 후보/매칭 대상에서 제외됩니다." if ok else "❌ 삭제 실패"
    
    if ok:
        PROFILE_CACHE.invalidate(interaction.user.id)
        HAS_PROFILE_CACHE.set(interaction.user.id, False)
    
    await _safe_send_or_edit(interaction, content=msg, edit=True, ephemeral=True)

async def handle_close(interaction: discord.Interaction):
    """닫기 버튼 처리"""
    try:
        await interaction.response.edit_message(view=None)
    except:
        pass
