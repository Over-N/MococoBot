import discord
from io import BytesIO
from core.http_client import http_client
from core.config import SUPPORTER_CLASSES
from typing import List, Dict, Any, Optional, Tuple
from commands.party_manage import MemberDropdown, MentionTypeView, permission_check

_SUCCESS = {200, 201, 204}

# ---------------------- 유틸 ----------------------
def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _fmt_wait_entry(e: Dict[str, Any]) -> str:
    name = (e.get("name") or "이름없음")
    cls = (e.get("class_name") or "직업정보없음")
    emo = (e.get("class_emoji") or "")
    ilvl = e.get("item_level")
    cp = e.get("combat_power")
    pos = e.get("position")
    ilvl_s = "-" if ilvl is None else str(ilvl)
    cp_s = "-" if cp is None else str(cp)
    pos_s = "-" if pos is None else str(pos)
    return f"`{pos_s}` {emo} **{name}** · {cls} · Lv {ilvl_s} · CP {cp_s}"
    
def _format_character_summary(ch: Dict[str, Any]) -> str:
    return (
        f"클래스: {ch.get('class_name', 'Unknown')} | "
        f"아이템 레벨: {ch.get('item_lvl', 'N/A')} | "
        f"전투력: {ch.get('combat_power', 'N/A')}"
    )

def _flatten_participants(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    participants = []
    raw = data.get("participants") or {}
    for role, plist in raw.items():
        for p in plist:
            item = dict(p)
            item["role"] = 0 if role == "dealers" else 1
            participants.append(item)
    return participants
    
async def _send_ephemeral(interaction: discord.Interaction, content: str, **kwargs):
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass
        
async def auto_join_party_by_nickname(party_id: int, user_id: Any, nickname: str) -> tuple[bool, str]:
    nickname = (nickname or "").strip()
    if not nickname:
        return False, "닉네임이 비어 있습니다."

    try:
        resp = await http_client.get(f"/character/search/{nickname}")
        if resp.status_code != 200:
            return False, f"'{nickname}' 캐릭터를 찾을 수 없습니다."

        payload = resp.json() or {}
        data = payload.get("data")
        if not data:
            return False, f"'{nickname}' 캐릭터 정보를 가져올 수 없습니다."

        class_name = (data.get("class_name") or "").strip()
        role = 1 if class_name in SUPPORTER_CLASSES else 0

        char_id = int(data["id"])
        code, message = await _post_join(int(party_id), user_id, char_id, role)

        if code in (200, 201, 204):
            return True, message or "파티에 성공적으로 참가했습니다."
        return False, message or "파티 참가에 실패했습니다."
    except Exception as e:
        return False, f"자동 참가 중 오류가 발생했습니다: {e}"

async def _post_join(party_id: int, user_id: Any, character_id: int, role: int) -> tuple[Optional[int], Optional[str]]:
    """파티 참가 HTTP 요청"""
    try:
        resp = await http_client.post(
            f"/party/{party_id}/join",
            json={
                "character_id": character_id,
                "user_id": str(user_id),
                "role": role,
            },
        )
        
        # JSON 응답 파싱 시도
        try:
            response_data = resp.json()
            message = response_data.get("message", "")
        except Exception:
            # JSON 파싱 실패 시 기본 메시지
            if resp.status_code in (200, 201, 204):
                message = "파티에 성공적으로 참가했습니다."
            else:
                message = "파티 참가에 실패하였어요."
        
        return resp.status_code, message
            
    except Exception as e:
        return None, f"네트워크 오류: {e}"

# ---------------------- 역할 선택 ----------------------

class RoleSelectView(discord.ui.View):
    """딜러/서포터 역할 선택 버튼"""
    def __init__(self, party_id: int, character_data: Dict[str, Any], user_id: Any):
        super().__init__(timeout=60)
        self.party_id = int(party_id)
        self.character_data = character_data
        self.user_id = str(user_id)

    async def _join_party(self, interaction: discord.Interaction, role: int):
        # 진행 중 중복 클릭 방지
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        code, message = await _post_join(self.party_id, self.user_id, int(self.character_data['id']), role)
        if code in (200, 201, 204):
            role_name = "서포터" if role == 1 else "딜러"
            try:
                await interaction.edit_original_response(
                    content=(
                        f"**{self.character_data['char_name']}** ({role_name})로 파티에 참가했습니다!\n"
                        + _format_character_summary(self.character_data)
                    ),
                    view=None,
                )
            except Exception:
                pass
        else:
            try:
                await interaction.edit_original_response(
                    content=message, view=None
                )
            except Exception:
                pass

    @discord.ui.button(label="딜러", style=discord.ButtonStyle.primary)
    async def dealer_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._join_party(interaction, role=0)

    @discord.ui.button(label="서포터", style=discord.ButtonStyle.success)
    async def supporter_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._join_party(interaction, role=1)

class PartyImageView(discord.ui.View):
    def __init__(self, party_id: int):
        super().__init__(timeout=300)
        self.party_id = party_id

    async def _render_and_edit(self, interaction: discord.Interaction, lounge: bool):
        party_id = self.party_id

        if lounge:
            api_path = f"/render/lounge/{party_id}"
            filename = f"raid_lounge_{party_id}.png"
        else:
            api_path = f"/render/party/{party_id}"
            filename = f"raid_party_{party_id}.png"

        await interaction.response.edit_message(
            content="<a:move_mococo:1440263296656867390> 이미지 생성중입니다..",
            view=None,
        )

        try:
            res = await http_client.get(api_path, timeout=300.0)
            res.raise_for_status()

            ctype = res.headers.get("content-type", "")
            if "image" not in ctype:
                try:
                    detail = res.json()
                except Exception:
                    detail = res.text[:200]
                raise RuntimeError(f"{detail}\n관리자에게 문의하세요.")

            buf = BytesIO(res.content)
            buf.seek(0)
            file = discord.File(buf, filename=filename)

            await interaction.edit_original_response(
                content=None,
                attachments=[],
                view=None,
                files=[file],
            )

        except Exception as e:
            await interaction.edit_original_response(
                content=f"오류: {e}",
                attachments=[],
                view=None,
            )

    @discord.ui.button(label="심플", style=discord.ButtonStyle.primary)
    async def simple(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._render_and_edit(interaction, lounge=False)

    @discord.ui.button(label="라운지", style=discord.ButtonStyle.secondary)
    async def lounge(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._render_and_edit(interaction, lounge=True)

        
# ---------------------- 등록된 캐릭터 선택 ----------------------

class RegisteredCharacterSelect(discord.ui.Select):
    """등록된 캐릭터 선택 드롭다운"""
    def __init__(self, party_id: int, characters: List[Dict[str, Any]], user_id: Any):
        options = []
        seen_values = set()
        
        for i, ch in enumerate(characters[:25]):
            char_id = ch.get('char_id')
            if not char_id:
                value = f"unknown_{i}"
            else:
                value = str(char_id)
                if value in seen_values:
                    value = f"{char_id}_{i}"
            
            seen_values.add(value)
            
            label = f"{ch.get('char_name', '이름없음')} (레벨 {ch.get('item_lvl', 'N/A')})"
            desc = f"{ch.get('class_name', '직업정보없음')} | 전투력 {ch.get('combat_power', 'N/A')}"
            
            options.append(
                discord.SelectOption(
                    label=str(label)[:100],
                    description=str(desc)[:100],
                    value=value,
                )
            )
            
        super().__init__(
            placeholder="등록된 캐릭터를 선택하세요...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.party_id = int(party_id)
        self.user_id = str(user_id)
        self._char_map: Dict[str, Dict[str, Any]] = {}
        for i, ch in enumerate(characters[:25]):
            char_id = ch.get('char_id')
            if not char_id:
                key = f"unknown_{i}"
            else:
                key = str(char_id)
                if key in self._char_map:
                    key = f"{char_id}_{i}"
            self._char_map[key] = ch

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            selected_value = self.values[0]
            ch = self._char_map.get(selected_value)
            
            if not ch:
                await interaction.followup.send("선택한 캐릭터 정보를 찾을 수 없습니다.", ephemeral=True)
                return

            # char_id 추출 (value에서 _숫자 부분 제거)
            char_id = ch.get('char_id')
            if not char_id:
                await interaction.followup.send("캐릭터 ID를 찾을 수 없습니다.", ephemeral=True)
                return

            # 서포터/딜러 선택이 필요한 클래스인가?
            if (ch.get('class_name') or '') in SUPPORTER_CLASSES:
                await interaction.followup.send(
                    (
                        f"**{ch.get('char_name', '이름없음')}** ({ch.get('class_name', '')})은(는) 딜러/서포터 모두 가능해요.\n"
                        "원하는 역할을 선택하세요."
                    ),
                    view=RoleSelectView(self.party_id, {
                        'id': char_id,
                        'char_name': ch.get('char_name', '이름없음'),
                        'class_name': ch.get('class_name'),
                        'item_lvl': ch.get('item_lvl'),
                        'combat_power': ch.get('combat_power'),
                    }, self.user_id),
                    ephemeral=True,
                )
                return

            code, message = await _post_join(self.party_id, self.user_id, int(char_id), role=0)
            
            if code in (200, 201, 204):
                success_msg = (
                    f"**{ch.get('char_name', '이름없음')}** 캐릭터로 파티에 참가했습니다!\n" + 
                    _format_character_summary(ch)
                )
                if message:
                    success_msg += f"\n✅ {message}"
                await interaction.followup.send(success_msg, ephemeral=True)
            else:
                error_content = message if message else "파티 참가 중 오류가 발생했습니다."
                await interaction.followup.send(f"❌ {error_content}", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)

# ---------------------- 닉네임 입력 모달 ----------------------

class CharacterNicknameModal(discord.ui.Modal):
    """닉네임으로 캐릭터 검색 후 참가"""
    def __init__(self, party_id: int, user_id: Any):
        super().__init__(title="캐릭터 닉네임 입력")
        self.party_id = int(party_id)
        self.user_id = str(user_id)
        self.add_item(
            discord.ui.InputText(
                label="캐릭터 닉네임",
                placeholder="참가할 캐릭터의 닉네임을 입력하세요...",
                required=True,
                max_length=100,
            )
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            nickname = self.children[0].value.strip()
            # 닉네임 → 캐릭터 정보 조회
            resp = await http_client.get(f"/character/search/{nickname}")
            if resp.status_code != 200:
                await interaction.followup.send(f"'{nickname}' 캐릭터를 찾을 수 없습니다.", ephemeral=True)
                return

            data = resp.json().get('data')
            if not data:
                await interaction.followup.send(f"'{nickname}' 캐릭터 정보를 가져올 수 없습니다.", ephemeral=True)
                return

            if (data.get('class_name') or '') in SUPPORTER_CLASSES:
                await interaction.followup.send(
                    (
                        f"**{data['char_name']}** ({data.get('class_name', '')})은(는) 딜러/서포터 모두 가능해요.\n"
                        "원하는 역할을 선택하세요."
                    ),
                    view=RoleSelectView(self.party_id, data, self.user_id),
                    ephemeral=True,
                )
                return

            code, message = await _post_join(self.party_id, self.user_id, int(data['id']), role=0)
            
            if code in (200, 201, 204):
                success_msg = (
                    f"**{data['char_name']}** 캐릭터로 파티에 참가했습니다!\n" +
                    _format_character_summary(data)
                )
                if message:
                    success_msg += f"\n✅ {message}"
                await interaction.followup.send(success_msg, ephemeral=True)
            else:
                error_content = message if message else "파티 참가 중 오류가 발생했습니다."
                await interaction.followup.send(f"❌ {error_content}", ephemeral=True)

        except Exception as e:
            await interaction.followup.send("캐릭터 등록 중 오류가 발생했습니다.", ephemeral=True)

class ForceCancelView(discord.ui.View):
    def __init__(
        self,
        party_id: int,
        participants: List[Dict[str, Any]],
        is_dealer_closed: int,
        is_supporter_closed: int,
    ):
        super().__init__(timeout=180)
        self.party_id = int(party_id)
        self.is_dealer_closed = int(is_dealer_closed or 0)
        self.is_supporter_closed = int(is_supporter_closed or 0)

        if participants:
            self.add_item(MemberDropdown(participants, party_id))

        self.dealer_button = discord.ui.Button(
            custom_id=f"party_dealer_toggle_{party_id}",
            label="딜러 마감",
            style=discord.ButtonStyle.danger,
        )
        self.dealer_button.callback = self._on_dealer_toggle
        self.add_item(self.dealer_button)

        self.supporter_button = discord.ui.Button(
            custom_id=f"party_supporter_toggle_{party_id}",
            label="서포터 마감",
            style=discord.ButtonStyle.danger,
        )
        self.supporter_button.callback = self._on_supporter_toggle
        self.add_item(self.supporter_button)

        self._sync_buttons()

    def _sync_buttons(self):
        if self.is_dealer_closed:
            self.dealer_button.label = "딜러 마감 해제"
            self.dealer_button.style = discord.ButtonStyle.success
        else:
            self.dealer_button.label = "딜러 마감"
            self.dealer_button.style = discord.ButtonStyle.danger

        if self.is_supporter_closed:
            self.supporter_button.label = "서포터 마감 해제"
            self.supporter_button.style = discord.ButtonStyle.success
        else:
            self.supporter_button.label = "서포터 마감"
            self.supporter_button.style = discord.ButtonStyle.danger

    async def _toggle(self, interaction: discord.Interaction, role: int):
        try:
            resp = await http_client.post(f"/party/{self.party_id}/toggle/{role}")
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"상태 변경 중 오류가 발생했습니다.\n{e}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"상태 변경 중 오류가 발생했습니다.\n{e}",
                    ephemeral=True,
                )
            return

        if resp.status_code != 200:
            text = f"상태 변경 실패 (코드: {resp.status_code})"
            try:
                payload = resp.json()
                msg = payload.get("message")
                if msg:
                    text = msg
            except Exception:
                pass

            if not interaction.response.is_done():
                await interaction.response.send_message(text, ephemeral=True)
            else:
                await interaction.followup.send(text, ephemeral=True)
            return

        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}

        self.is_dealer_closed = int(payload.get("is_dealer_closed", self.is_dealer_closed))
        self.is_supporter_closed = int(payload.get("is_supporter_closed", self.is_supporter_closed))
        self._sync_buttons()

        if not interaction.response.is_done():
            await interaction.response.edit_message(view=self)
        else:
            await interaction.edit_original_response(view=self)

    async def _on_dealer_toggle(self, interaction: discord.Interaction):
        await self._toggle(interaction, role=0)

    async def _on_supporter_toggle(self, interaction: discord.Interaction):
        await self._toggle(interaction, role=1)

# ---------------------- 참가/탈퇴 뷰 ----------------------

class PartyJoinSelectView(discord.ui.View):
    """파티 참가 방법 선택 뷰"""
    def __init__(self, party_id: int, user_id: Any, characters: Optional[List[Dict[str, Any]]] = None):
        super().__init__(timeout=300)
        self.party_id = int(party_id)
        self.user_id = str(user_id)
        if characters:
            self.add_item(RegisteredCharacterSelect(party_id, characters, user_id))

    @discord.ui.button(label="직접 입력", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def manual_input(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = CharacterNicknameModal(self.party_id, self.user_id)
        await interaction.response.send_modal(modal)

# ---------------------- 엔트리 함수 ----------------------

async def handle_party_join(interaction: discord.Interaction, party_id: int):
    """파티 참가 처리"""
    try:
        resp = await http_client.get(f"/siblings/{interaction.user.id}")
        characters: List[Dict[str, Any]] = []
        if resp.status_code == 200:
            payload = resp.json()
            characters = payload.get('characters', []) or []

        if characters:
            await interaction.response.defer(ephemeral=True)
            embed = discord.Embed(
                title="🏴‍☠️ 파티 참가",
                description=f"등록된 캐릭터 **{len(characters)}개**가 있어요!\n아래에서 선택하거나 직접 입력하세요.",
                color=discord.Color.blue(),
            )
            preview = [
                f"**{c['char_name']}** ({c.get('class_name', 'N/A')}) - 레벨 {c.get('item_lvl', 'N/A')}"
                for c in characters[:5]
            ]
            if preview:
                embed.add_field(
                    name="📋 등록된 캐릭터",
                    value="\n".join(preview) + (f"\n... 외 {len(characters) - 5}개" if len(characters) > 5 else ""),
                    inline=False,
                )
            view = PartyJoinSelectView(party_id, interaction.user.id, characters)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            # 등록된 캐릭터 없으면 바로 모달
            modal = CharacterNicknameModal(party_id, interaction.user.id)
            await interaction.response.send_modal(modal)

    except Exception as e:
        try:
            await interaction.response.send_message(f"참가 처리 중 오류가 발생했습니다.\n{e}", ephemeral=True)
        except Exception:
            await interaction.followup.send(f"참가 처리 중 오류가 발생했습니다.\n{e}", ephemeral=True)

async def handle_party_leave(interaction: discord.Interaction, party_id: int):
    """파티 탈퇴 처리"""
    try:
        user_id = int(interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        resp = await http_client.delete(f"/party/{party_id}/participants/{user_id}")
        if resp.status_code in (200, 204):
            await interaction.followup.send("파티에서 탈퇴했습니다.", ephemeral=True)
        elif resp.status_code == 404:
            await interaction.followup.send("참가하지 않은 파티입니다.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"탈퇴 처리 중 오류가 발생했습니다. (상태 코드: {resp.status_code})",
                ephemeral=True,
            )
    except Exception as e:
        await interaction.followup.send(f"탈퇴 처리 중 오류가 발생했습니다.\n{e}", ephemeral=True)

async def handle_party_force_cancel(interaction: discord.Interaction, party_id: int):
    data = await permission_check(interaction, party_id)
    if not data:
        return

    participants = _flatten_participants(data)

    is_dealer_closed = int(data.get("is_dealer_closed") or 0)
    is_supporter_closed = int(data.get("is_supporter_closed") or 0)

    view = ForceCancelView(
        party_id=party_id,
        participants=participants,
        is_dealer_closed=is_dealer_closed,
        is_supporter_closed=is_supporter_closed,
    )

    if participants:
        msg = "강제 참가 취소할 인원을 선택하세요."
    else:
        msg = "참가 중인 인원이 없어 딜러/서포터 마감 상태만 변경할 수 있어요."

    await _send_ephemeral(interaction, msg, view=view)

    
async def handle_party_mention(interaction: discord.Interaction, party_id: int):
    data = await permission_check(interaction, party_id)
    if not data:
        return

    participants = _flatten_participants(data)
    if not participants:
        await _send_ephemeral(interaction, "참가 중인 인원이 없습니다.")
        return

    chat_channel_id = data.get("thread_manage_id") or data.get("thread_id")
    if not chat_channel_id:
        await _send_ephemeral(interaction, "멘션을 보낼 스레드 정보를 찾을 수 없습니다.")
        return

    view = MentionTypeView(party_id, chat_channel_id, participants)
    await _send_ephemeral(interaction, "멘션 방식을 선택하세요.", view=view)

async def handle_party_delete(interaction: discord.Interaction, party_id: int):
    data = await permission_check(interaction, party_id)
    if not data:
        return

    resp = await http_client.delete(f"/party/{party_id}/delete")
    msg = "일정이 삭제되었습니다." if resp.status_code == 200 else "일정 삭제 실패!"
    await _send_ephemeral(interaction, msg)
    
async def handle_party_public(interaction: discord.Interaction, party_id: int):
    data = await permission_check(interaction, party_id)
    if not data:
        return

    try:
        resp = await http_client.post(f"/party/public/{party_id}")
    except Exception as e:
        await _send_ephemeral(interaction, f"상태 변경 중 오류가 발생했습니다.\n{e}")
        return

    if resp.status_code != 200:
        await _send_ephemeral(interaction, f"상태 변경 실패 (코드: {resp.status_code})")
        return

    payload = resp.json() or {}
    is_active = int(payload.get("is_active", 0))

    if is_active == 1:
        msg = f"✅ 일정이 공개 상태로 변경되었습니다.\n🔗 https://mococobot.kr/party/{party_id}"
        public_label = "일정 공개 🟢"
    else:
        msg = "✅ 일정이 비공개 상태로 변경되었습니다."
        public_label = "일정 공개 🔴"

    await _send_ephemeral(interaction, msg)

    try:
        view = discord.ui.View.from_message(interaction.message)
        target_custom_id = f"party_public_{party_id}"

        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == target_custom_id:
                child.label = public_label

        await interaction.message.edit(view=view)
    except Exception:
        pass

async def handle_party_image(interaction: discord.Interaction, party_id: int):
    data = await permission_check(interaction, party_id)
    if not data:
        return

    view = PartyImageView(party_id)

    await interaction.response.send_message(
        "어떤 스타일로 이미지를 생성할까요?",
        view=view,
        ephemeral=True,
    )

async def _get_waitlist(party_id: int) -> Tuple[Optional[int], Dict[str, Any]]:
    try:
        resp = await http_client.get(f"/party/{int(party_id)}/waitlist")
        status = getattr(resp, "status_code", None)
        payload = {}
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        return status, payload
    except Exception:
        return None, {}


async def _get_my_wait_pos(party_id: int, user_id: str, role: int) -> Tuple[Optional[int], Dict[str, Any]]:
    try:
        resp = await http_client.get(
            f"/party/{int(party_id)}/waitlist/me",
            params={"user_id": str(user_id), "role": int(role)},
        )
        status = getattr(resp, "status_code", None)
        payload = {}
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        return status, payload
    except Exception:
        return None, {}


async def _cancel_waitlist(party_id: int, user_id: str, role: int) -> Tuple[Optional[int], Dict[str, Any]]:
    try:
        resp = await http_client.delete(
            f"/party/{int(party_id)}/waitlist",
            json={"user_id": str(user_id), "role": int(role)},
        )
        status = getattr(resp, "status_code", None)
        payload = {}
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        return status, payload
    except Exception:
        return None, {}


class WaitlistActionView(discord.ui.View):
    def __init__(self, party_id: int, role: int):
        super().__init__(timeout=180)
        self.party_id = int(party_id)
        self.role = int(role)
        self._update_tab_buttons()

    def _update_tab_buttons(self):
        if self.role == 0:
            self.dealer_tab.style = discord.ButtonStyle.primary
            self.dealer_tab.disabled = True
            self.dealer_tab.label = "✅ 딜러 목록"
            self.supporter_tab.style = discord.ButtonStyle.secondary
            self.supporter_tab.disabled = False
            self.supporter_tab.label = "서포터 목록 보기"
        else:
            self.dealer_tab.style = discord.ButtonStyle.secondary
            self.dealer_tab.disabled = False
            self.dealer_tab.label = "딜러 목록 보기"
            self.supporter_tab.style = discord.ButtonStyle.primary
            self.supporter_tab.disabled = True
            self.supporter_tab.label = "✅ 서포터 목록"

    @discord.ui.button(row=0)
    async def dealer_tab(self, button: discord.ui.Button, interaction: discord.Interaction):
        await show_waitlist_detail(interaction, self.party_id, 0, edit=True)

    @discord.ui.button(row=0)
    async def supporter_tab(self, button: discord.ui.Button, interaction: discord.Interaction):
        await show_waitlist_detail(interaction, self.party_id, 1, edit=True)

    @discord.ui.button(label="내 순번 확인", style=discord.ButtonStyle.success, custom_id="wait_me", row=1)
    async def me_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        status, payload = await _get_my_wait_pos(self.party_id, str(interaction.user.id), self.role)
        
        if status not in _SUCCESS:
            await interaction.followup.send("내 대기열 정보를 불러오지 못했습니다.", ephemeral=True)
            return

        data = (payload or {}).get("data") or {}
        if not isinstance(data, dict) or not data.get("in_waitlist"):
            role_name = "딜러" if self.role == 0 else "서포터"
            await interaction.followup.send(f"현재 [{role_name}] 대기열에 등록되어 있지 않습니다.\n(다른 역할을 신청하셨다면 탭을 바꿔 확인해보세요.)", ephemeral=True)
            return

        pos = data.get("position")
        await interaction.followup.send(f"현재 대기열 순번은 **{pos}번** 입니다.", ephemeral=True)

    @discord.ui.button(label="대기열 취소", style=discord.ButtonStyle.danger, custom_id="wait_cancel", row=1)
    async def cancel_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        status, payload = await _cancel_waitlist(self.party_id, str(interaction.user.id), self.role)
        
        if status not in _SUCCESS:
            await interaction.followup.send("대기열 취소에 실패했습니다.", ephemeral=True)
            return

        data = (payload or {}) if isinstance(payload, dict) else {}
        deleted = bool(data.get("deleted"))
        
        if deleted:
            await interaction.followup.send("대기열 등록을 취소했습니다.", ephemeral=True)
        else:
            await interaction.followup.send("취소할 대기열 등록이 없습니다.", ephemeral=True)


async def show_waitlist_detail(interaction: discord.Interaction, party_id: int, role: int, edit: bool = False):
    if not edit:
        await interaction.response.defer(ephemeral=True)

    status, payload = await _get_waitlist(party_id)
    if status not in _SUCCESS:
        msg = "대기열 정보를 불러오지 못했습니다."
        if edit:
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return

    data = (payload or {}).get("data") or {}
    if not isinstance(data, dict):
        msg = "대기열 데이터 형식이 올바르지 않습니다."
        if edit:
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return

    key = "dealers" if int(role) == 0 else "supporters"
    title_role = "딜러" if int(role) == 0 else "서포터"
    items = data.get(key) or []
    if not isinstance(items, list):
        items = []

    embed = discord.Embed(
        title=f"📋 대기열 현황 ({title_role})",
        description="",
        color=discord.Color.green() if int(role) == 0 else discord.Color.gold(),
    )

    if not items:
        embed.description = "```\n현재 대기 인원이 없습니다.\n```"
    else:
        lines = [_fmt_wait_entry(e) for e in items[:30]]
        embed.description = "\n".join(lines)
        if len(items) > 30:
            embed.set_footer(text=f"... 외 {len(items) - 30}명 더 있음")

    view = WaitlistActionView(party_id, role)

    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def handle_party_waitlist(interaction: discord.Interaction, party_id: int):
    try:
        await show_waitlist_detail(interaction, party_id, 0, edit=False)
    except Exception as e:
        await _send_ephemeral(interaction, f"오류가 발생했습니다: {str(e)}")