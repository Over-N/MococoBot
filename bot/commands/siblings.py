import discord
import aiohttp
from core.config import LOSTARK_API_KEY
from core.http_client import http_client
from typing import List, Dict, Any

class ExpeditionRegisterChannelModal(discord.ui.Modal):
    """원정대 등록 채널 메시지 입력 모달"""
    def __init__(self, target_channel: discord.TextChannel):
        super().__init__(title="원정대 등록 안내 메시지 입력")
        self.target_channel = target_channel
        self.add_item(
            discord.ui.InputText(
                label="안내 메시지",
                placeholder="원정대 등록 안내 메시지를 입력하세요.",
                style=discord.InputTextStyle.long,
                required=True
            )
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            message = self.children[0].value
            embed = discord.Embed(
                title="🏴‍☠️ 원정대 등록하기",
                description=message,
                color=discord.Color.blue()
            )
            view = ExpeditionRegisterButtonView()
            
            # 채널에 메시지 전송
            await self.target_channel.send(embed=embed, view=view)
            
            # 성공 응답
            await interaction.response.send_message(
                f"✅ {self.target_channel.mention} 채널에 원정대 등록 버튼을 보냈어요!", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ 메시지 전송에 실패했습니다: {str(e)}", 
                ephemeral=True
            )

class ExpeditionRegisterButtonView(discord.ui.View):
    """원정대 등록 버튼 뷰 - 단순 버튼만"""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(
        label="원정대 등록", 
        style=discord.ButtonStyle.success, 
        custom_id="expedition_register_button",
        emoji="🏴‍☠️"
    )
    async def expedition_register_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        pass

class CharacterRegisterModal(discord.ui.Modal):
    """개별 캐릭터 등록 모달"""
    def __init__(self):
        super().__init__(title="캐릭터 등록")
        self.add_item(discord.ui.InputText(
            label="캐릭터 이름",
            placeholder="등록할 캐릭터의 이름을 입력하세요",
            required=True,
            max_length=16
        ))
    
    async def callback(self, interaction: discord.Interaction):
        char_name = self.children[0].value.strip()
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            # 먼저 이미 등록된 캐릭터인지 확인
            existing_response = await http_client.get(f"/siblings/{interaction.user.id}")
            if existing_response.status_code == 200:
                existing_data = existing_response.json()
                existing_characters = existing_data.get('characters', [])
                
                # 중복 확인 (대소문자 구분 없이)
                for existing_char in existing_characters:
                    if existing_char.get('char_name', '').lower() == char_name.lower():
                        embed = discord.Embed(
                            title="⚠️ 이미 등록된 캐릭터",
                            description=f"**{char_name}** 캐릭터는 이미 원정대에 등록되어 있습니다.",
                            color=discord.Color.orange()
                        )
                        await interaction.edit_original_response(embed=embed)
                        return
            
            # 중복이 아닌 경우 등록 진행
            response = await http_client.post(f"/siblings/{interaction.user.id}/register", json={
                "char_name": char_name
            })
            
            if response.status_code == 200:
                data = response.json()
                embed = discord.Embed(
                    title="✅ 캐릭터 등록 완료!",
                    description=f"**{char_name}** 캐릭터가 {interaction.user.mention} 님의 원정대에 등록되었어요!",
                    color=discord.Color.green()
                )
                
                if data.get('character'):
                    char_info = data['character']
                    embed.add_field(
                        name="캐릭터 정보",
                        value=f"**직업:** {char_info.get('class_name', 'N/A')}\n**아이템 레벨:** {char_info.get('item_lvl', 0)}\n**전투력:** {char_info.get('combat_power', 0)}",
                        inline=True
                    )
            
            elif response.status_code == 404:
                embed = discord.Embed(
                    title="❌ 캐릭터를 찾을 수 없음",
                    description=f"**{char_name}** 캐릭터를 찾을 수 없습니다.\n캐릭터 이름을 정확히 입력했는지 확인해주세요.",
                    color=discord.Color.red()
                )
            
            elif response.status_code == 409:
                embed = discord.Embed(
                    title="⚠️ 이미 등록된 캐릭터",
                    description=f"**{char_name}** 캐릭터는 이미 원정대에 등록되어 있습니다.",
                    color=discord.Color.orange()
                )
            
            else:
                embed = discord.Embed(
                    title="❌ 등록 실패",
                    description=f"캐릭터 등록에 실패했습니다. (상태 코드: {response.status_code})",
                    color=discord.Color.red()
                )
            
            await interaction.edit_original_response(embed=embed)
            
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}"
            )

class ExpeditionRegisterModal(discord.ui.Modal):
    """원정대 일괄 등록을 위한 모달"""
    def __init__(self):
        super().__init__(title="원정대 일괄 등록")
        self.add_item(discord.ui.InputText(
            label="캐릭터 이름",
            placeholder="원정대를 조회할 캐릭터의 이름을 입력하세요",
            required=True,
            max_length=16
        ))
    
    async def callback(self, interaction: discord.Interaction):
        char_name = self.children[0].value.strip()
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            # 로스트아크 API에서 원정대 정보 조회
            url = f"https://developer-lostark.game.onstove.com/characters/{char_name}/siblings"
            headers = {
                "accept": "application/json",
                "authorization": f"bearer {LOSTARK_API_KEY}"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        character_data = await resp.json()
                    else:
                        character_data = None
            
            if character_data is None or not character_data:
                embed = discord.Embed(
                    title="❌ 원정대 정보 없음",
                    description=f"**{char_name}** 캐릭터의 원정대 정보를 찾을 수 없습니다.\n캐릭터 이름을 정확히 입력했는지 확인해주세요.",
                    color=discord.Color.red()
                )
                await interaction.edit_original_response(embed=embed)
                return
            
            embed = discord.Embed(
                title="📋 원정대 캐릭터 목록",
                description=f"**{char_name}**의 원정대에서 등록할 캐릭터를 선택해주세요.",
                color=discord.Color.blue()
            )
            
            view = ExpeditionSelectView(str(interaction.user.id), character_data)
            await interaction.edit_original_response(embed=embed, view=view)
            
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}"
            )

class ExpeditionCharacterSelect(discord.ui.Select):
    """원정대 캐릭터 선택 드롭다운"""
    def __init__(self, user_id: str, characters: List[Dict[str, Any]]):
        options = []
        for char in characters[:25]:  # Discord 제한
            server_info = f" ({char.get('ServerName', '')})" if char.get('ServerName') else ""
            option_label = f"{char['CharacterName']}{server_info}"
            
            options.append(discord.SelectOption(
                label=option_label[:100],  # Discord 제한
                description=f"{char.get('CharacterClassName', '직업정보없음')} | {char.get('ItemAvgLevel', 0)}",
                value=char['CharacterName'],
                emoji="⚔️"
            ))
        
        super().__init__(
            placeholder="등록할 캐릭터들을 선택하세요...",
            options=options,
            min_values=1,
            max_values=min(len(options), 25)
        )
        self.user_id = user_id
        self.characters = characters
    
    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            
            # 이미 등록된 캐릭터 목록 먼저 조회
            existing_response = await http_client.get(f"/siblings/{self.user_id}")
            existing_characters = []
            if existing_response.status_code == 200:
                existing_data = existing_response.json()
                existing_characters = [char.get('char_name', '').lower() for char in existing_data.get('characters', [])]
            
            selected_characters = self.values
            success_count = 0
            failed_characters = []
            duplicate_characters = []
            
            for char_name in selected_characters:
                # 중복 확인 (대소문자 구분 없이)
                if char_name.lower() in existing_characters:
                    duplicate_characters.append(char_name)
                    continue
                
                try:
                    response = await http_client.post(f"/siblings/{self.user_id}/register", json={
                        "char_name": char_name
                    })
                    
                    if response.status_code == 200:
                        success_count += 1
                        # 성공한 캐릭터는 중복 방지를 위해 기존 목록에 추가
                        existing_characters.append(char_name.lower())
                    else:
                        failed_characters.append(char_name)
                
                except Exception as e:
                    failed_characters.append(char_name)
            
            # 결과 임베드 생성
            if success_count > 0:
                embed = discord.Embed(
                    title="✅ 원정대 등록 완료",
                    description=f"{success_count}개 캐릭터가 성공적으로 등록되었습니다.",
                    color=discord.Color.green()
                )
                
                # 성공한 캐릭터 목록 표시
                success_chars = [name for name in selected_characters 
                               if name not in failed_characters and name not in duplicate_characters]
                if success_chars:
                    embed.add_field(
                        name="✅ 등록 성공",
                        value="\n".join([f"• {name}" for name in success_chars]),
                        inline=False
                    )
            else:
                embed = discord.Embed(
                    title="❌ 원정대 등록 실패",
                    description="등록된 캐릭터가 없습니다.",
                    color=discord.Color.red()
                )
            
            # 중복 캐릭터 표시
            if duplicate_characters:
                embed.add_field(
                    name="⚠️ 이미 등록된 캐릭터 (건너뜀)",
                    value="\n".join([f"• {name}" for name in duplicate_characters]),
                    inline=False
                )
            
            # 실패한 캐릭터 표시 (중복 제외)
            if failed_characters:
                embed.add_field(
                    name="❌ 등록 실패",
                    value="\n".join([f"• {name}" for name in failed_characters]),
                    inline=False
                )
            
            await interaction.edit_original_response(embed=embed, view=None)
            
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}",
                view=None
            )

class ExpeditionSelectView(discord.ui.View):
    """원정대 일괄 등록용 뷰"""
    def __init__(self, user_id: str, characters: List[Dict[str, Any]]):
        super().__init__(timeout=300)
        self.add_item(ExpeditionCharacterSelect(user_id, characters))

class RegisteredCharacterSelect(discord.ui.Select):
    """등록된 캐릭터 선택 및 삭제"""
    def __init__(self, user_id: str, characters: List[Dict[str, Any]]):
        options = []
        for char in characters[:25]:
            option_label = char['char_name']
            
            options.append(discord.SelectOption(
                label=option_label[:100],
                description=f"{char.get('class_name', '직업정보없음')} | 레벨 {char.get('item_lvl', 0)} | 전투력 {char.get('combat_power', 0)}",
                value=str(char['char_id']),
                emoji="🗑️"
            ))
        
        super().__init__(
            placeholder="삭제할 캐릭터를 선택하세요...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.user_id = user_id
    
    async def callback(self, interaction: discord.Interaction):
        character_id = int(self.values[0])
        
        try:
            await interaction.response.defer(ephemeral=True)
            # 삭제 요청
            response = await http_client.delete(f"/siblings/{self.user_id}/{character_id}")
            
            if response.status_code == 200:
                # 성공적으로 삭제됨, 업데이트된 리스트 다시 불러오기
                updated_response = await http_client.get(f"/siblings/{self.user_id}")
                
                if updated_response.status_code == 200:
                    updated_data = updated_response.json()
                    characters = updated_data.get('characters', [])
                    
                    embed = discord.Embed(
                        title="✅ 캐릭터 삭제 완료",
                        description=f"캐릭터가 원정대에서 삭제되었습니다.\n현재 등록된 캐릭터: **{len(characters)}개**",
                        color=discord.Color.green()
                    )
                    
                    if characters:
                        # 새로운 뷰로 업데이트
                        new_view = RegisteredCharacterView(self.user_id, characters)
                        
                        # 캐릭터 목록을 더 깔끔하게 표시
                        char_list = []
                        for char in characters[:10]:
                            char_list.append(
                                f"**{char['char_name']}** ({char.get('class_name', 'N/A')}) - 레벨 {char.get('item_lvl', 0)}"
                            )
                        
                        embed.add_field(
                            name="📋 등록된 캐릭터",
                            value="\n".join(char_list) + 
                                  (f"\n... 외 {len(characters) - 10}개" if len(characters) > 10 else ""),
                            inline=False
                        )
                        await interaction.edit_original_response(embed=embed, view=new_view)
                    else:
                        # 모든 캐릭터가 삭제됨
                        embed.add_field(
                            name="📋 등록된 캐릭터",
                            value="등록된 캐릭터가 없습니다.",
                            inline=False
                        )
                        await interaction.edit_original_response(embed=embed, view=None)
                else:
                    await interaction.edit_original_response(
                        content="❌ 캐릭터는 삭제되었지만 목록을 다시 불러오는데 실패했습니다.",
                        view=None
                    )
            elif response.status_code == 404:
                embed = discord.Embed(
                    title="❌ 캐릭터를 찾을 수 없음",
                    description="삭제하려는 캐릭터를 찾을 수 없습니다.",
                    color=discord.Color.red()
                )
                await interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.edit_original_response(
                    content=f"❌ 캐릭터 삭제에 실패했습니다. (상태 코드: {response.status_code})",
                    view=None
                )
        
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}",
                view=None
            )

class RegisteredCharacterView(discord.ui.View):
    """등록된 캐릭터 관리 뷰"""
    def __init__(self, user_id: str, characters: List[Dict[str, Any]]):
        super().__init__(timeout=300)
        if characters:
            self.add_item(RegisteredCharacterSelect(user_id, characters))

class ExpeditionManageView(discord.ui.View):
    """원정대 관리 메인 뷰"""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="캐릭터 등록", style=discord.ButtonStyle.primary, emoji="➕")
    async def register_character(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = CharacterRegisterModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="원정대 일괄 등록", style=discord.ButtonStyle.secondary, emoji="📋")
    async def register_expedition(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = ExpeditionRegisterModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="원정대 일괄 갱신", style=discord.ButtonStyle.success, emoji="🔄")
    async def bulk_update_expedition(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            resp = await http_client.get(f"/siblings/{interaction.user.id}")
            if resp.status_code != 200:
                await interaction.edit_original_response(
                    content=f"❌ 등록된 캐릭터 목록을 불러오지 못했습니다. (상태 코드: {resp.status_code})",
                    view=None
                )
                return
            
            data = resp.json()
            characters = data.get("characters", [])

            if not characters:
                embed = discord.Embed(
                    title="📋 등록된 캐릭터 없음",
                    description="등록된 캐릭터가 없습니다.\n먼저 캐릭터를 등록해주세요.",
                    color=discord.Color.orange()
                )
                await interaction.edit_original_response(embed=embed, view=None)
                return

            preview = []
            for c in characters[:5]:
                preview.append(f"• **{c['char_name']}** ({c.get('class_name','N/A')}) | 레벨 {c.get('item_lvl',0)}")
            preview_more = f"\n... 외 {len(characters)-5}개" if len(characters) > 5 else ""

            embed = discord.Embed(
                title="🔄 원정대 일괄 갱신",
                description="갱신할 캐릭터들을 선택하세요. 선택 즉시 갱신을 시작합니다.",
                color=discord.Color.blurple()
            )
            embed.add_field(name="📋 현재 등록된 캐릭터 (일부 미리보기)", value="\n".join(preview)+preview_more, inline=False)

            view = ExpeditionBulkUpdateView(str(interaction.user.id), characters)
            await interaction.edit_original_response(embed=embed, view=view)

        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}",
                view=None
            )
            
    @discord.ui.button(label="등록된 캐릭터 삭제", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_characters(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            response = await http_client.get(f"/siblings/{interaction.user.id}")
            
            if response.status_code == 200:
                data = response.json()
                characters = data.get('characters', [])
                
                if characters:
                    embed = discord.Embed(
                        title="🗑️ 캐릭터 삭제",
                        description="삭제할 캐릭터를 선택해주세요.",
                        color=discord.Color.red()
                    )
                    
                    # 현재 등록된 캐릭터 미리보기 추가
                    char_preview = []
                    for char in characters[:5]:
                        char_preview.append(
                            f"**{char['char_name']}** ({char.get('class_name', 'N/A')}) - 레벨 {char.get('item_lvl', 0)}"
                        )
                    
                    embed.add_field(
                        name="📋 현재 등록된 캐릭터",
                        value="\n".join(char_preview) + 
                              (f"\n... 외 {len(characters) - 5}개" if len(characters) > 5 else ""),
                        inline=False
                    )
                    
                    view = RegisteredCharacterView(str(interaction.user.id), characters)
                    await interaction.edit_original_response(embed=embed, view=view)
                else:
                    embed = discord.Embed(
                        title="📋 등록된 캐릭터 없음",
                        description="등록된 캐릭터가 없습니다.\n먼저 캐릭터를 등록해주세요.",
                        color=discord.Color.orange()
                    )
                    await interaction.edit_original_response(embed=embed, view=None)
            
            elif response.status_code == 404:
                embed = discord.Embed(
                    title="📋 등록된 캐릭터 없음",
                    description="등록된 캐릭터가 없습니다.\n먼저 캐릭터를 등록해주세요.",
                    color=discord.Color.orange()
                )
                await interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.edit_original_response(
                    content=f"❌ 캐릭터 목록 조회에 실패했습니다. (상태 코드: {response.status_code})"
                )
        
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}"
            )

    @discord.ui.button(label="원정대 초기화", style=discord.ButtonStyle.danger, emoji="🧹")
    async def reset_expedition(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            resp = await http_client.delete(f"/siblings/reset/{interaction.user.id}")
            if resp.status_code == 200:
                data = resp.json()
                deleted = int(data.get("deleted_count", 0))
                embed = discord.Embed(
                    title="🧹 원정대 초기화 완료",
                    description=f"{deleted}개 캐릭터를 삭제했습니다.",
                    color=discord.Color.red()
                )
                await interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.edit_original_response(
                    content=f"❌ 초기화에 실패했습니다. (상태 코드: {resp.status_code})",
                    view=None
                )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}",
                view=None
            )

class ExpeditionBulkUpdateSelect(discord.ui.Select):
    """원정대 일괄 갱신용 캐릭터 선택"""
    def __init__(self, user_id: str, characters: List[Dict[str, Any]]):
        options = []
        for char in characters[:25]:  # Discord Select 최대 25개
            label = char["char_name"]
            desc  = f"{char.get('class_name','직업정보없음')} | 아이템 레벨 {char.get('item_lvl',0)}"
            options.append(discord.SelectOption(
                label=label[:100],
                description=desc[:100],
                value=char["char_name"],
                emoji="🔄"
            ))
        super().__init__(
            placeholder="갱신할 캐릭터들을 선택하세요...",
            options=options,
            min_values=1,
            max_values=min(len(options), 25)
        )
        self.user_id = user_id
        self.characters = characters

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            selected_names = list(self.values)  # char_name 리스트
            total_requests = len(selected_names)
            success_names: List[str] = []
            failed_names: List[str]  = []

            # 변경 요약(최대 10개 표시)
            change_lines: List[str] = []

            # Discord 업데이트 집계
            agg = {"total_characters": 0, "total_parties": 0, "total_success": 0, "total_failed": 0}

            # 순차 호출 (속도 필요 시 gather + 세마포어로 병렬화 가능)
            for name in selected_names:
                try:
                    resp = await http_client.patch("/character/update", json={
                        "char_name": name,
                        "update_discord": True
                    })
                    if resp.status_code != 200:
                        failed_names.append(name)
                        continue

                    data = resp.json()
                    success_names.append(name)

                    # 변경사항 파싱
                    for ch in data.get("characters", []):
                        changes   = ch.get("changes", {}) or {}
                        old_data  = ch.get("old_data", {}) or {}
                        curr_name = ch.get("char_name") or name

                        # 변경 라인 구성
                        per_char_lines = []
                        if changes.get("item_lvl_changed"):
                            per_char_lines.append(f"아이템 레벨 {old_data.get('item_lvl')} → {ch.get('item_lvl')}")
                        if changes.get("combat_power_changed"):
                            per_char_lines.append(f"전투력 {old_data.get('combat_power')} → {ch.get('combat_power')}")
                        if changes.get("class_changed"):
                            per_char_lines.append(f"직업 {old_data.get('class_name')} → {ch.get('class_name')}")

                        if per_char_lines:
                            change_lines.append(f"• **{curr_name}** — " + "; ".join(per_char_lines))
                        else:
                            change_lines.append(f"• **{curr_name}** — 변경사항 없음")

                    # Discord 업데이트 요약 합산
                    summary = (data.get("discord_update") or {}).get("summary", {}) or {}
                    agg["total_characters"] += int(summary.get("total_characters") or 0)
                    agg["total_parties"]    += int(summary.get("total_parties") or 0)
                    agg["total_success"]    += int(summary.get("total_success") or 0)
                    agg["total_failed"]     += int(summary.get("total_failed") or 0)

                except Exception:
                    failed_names.append(name)

            # 임베드 구성
            ok_count = len(success_names)
            fail_count = len(failed_names)

            if ok_count > 0:
                embed = discord.Embed(
                    title="✅ 원정대 일괄 갱신 완료",
                    description="선택한 캐릭터의 정보를 갱신했습니다.",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="❌ 원정대 일괄 갱신 실패",
                    description="모든 요청이 실패했거나 처리되지 않았습니다.",
                    color=discord.Color.red()
                )

            # 요약
            embed.add_field(
                name="📊 처리 요약",
                value=f"**요청:** {total_requests}개\n**성공:** {ok_count}개\n**실패:** {fail_count}개",
                inline=False
            )

            # 변경사항(최대 10개만 표시)
            if change_lines:
                max_show = 10
                shown = "\n".join(change_lines[:max_show])
                more  = f"\n... 외 {len(change_lines)-max_show}개" if len(change_lines) > max_show else ""
                embed.add_field(
                    name="📈 변경사항",
                    value=shown + more,
                    inline=False
                )

            # 성공/실패 이름 목록(너무 길면 생략)
            if success_names:
                s_list = ", ".join(success_names[:15]) + (", ..." if len(success_names) > 15 else "")
                embed.add_field(name="✅ 성공한 캐릭터", value=s_list, inline=False)
            if failed_names:
                f_list = ", ".join(failed_names[:15]) + (", ..." if len(failed_names) > 15 else "")
                embed.add_field(name="❌ 실패한 캐릭터", value=f_list, inline=False)


            await interaction.edit_original_response(embed=embed, view=None)

        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ 오류가 발생했습니다: {str(e)}",
                view=None
            )


class ExpeditionBulkUpdateView(discord.ui.View):
    """원정대 일괄 갱신용 뷰"""
    def __init__(self, user_id: str, characters: List[Dict[str, Any]]):
        super().__init__(timeout=300)
        self.add_item(ExpeditionBulkUpdateSelect(user_id, characters))
