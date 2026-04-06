import discord
import pytz
from datetime import datetime
from typing import Optional

from core.http_client import http_client
from core.raid_data import raid_list, raid_difficulty_map
from handler.party import auto_join_party_by_nickname

class RaidCommands:
    def __init__(self):
        self.client = None

    async def setup(self):
        self.client = http_client

    async def api_post(self, endpoint: str, data: dict):
        if not self.client:
            await self.setup()
        resp = await self.client.post(endpoint, json=data)
        resp.raise_for_status()
        return resp.json()

    async def api_get(self, endpoint: str):
        if not self.client:
            await self.setup()
        resp = await self.client.get(endpoint)
        resp.raise_for_status()
        return resp.json()

    async def api_patch(self, endpoint: str, data: dict):
        if not self.client:
            await self.setup()
        resp = await self.client.patch(endpoint, json=data)
        resp.raise_for_status()
        return resp.json()

    async def setup_raid_system(self, ctx: discord.ApplicationContext, raid_list):
        """레이드 시스템 설정 (/setups)"""
        await ctx.defer()
        guild_id = ctx.guild.id

        category = await ctx.guild.create_category("[🎮] 〰 𝑅𝑎𝑖𝑑")
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(
                create_public_threads=False,
                send_messages=False,
                send_messages_in_threads=False,
            )
        }

        try:
            forum_channel = await ctx.guild.create_forum_channel(
                name="│🎮┊레이드공지",
                category=category,
                overwrites=overwrites,
                reason="레이드 일정 관리를 위한 포럼 채널",
            )

            # 태그 최대 20개
            tags = [(name, "⚪") for name in raid_list[:20]]
            await forum_channel.edit(
                reason="레이드 태그 추가",
                available_tags=[
                    discord.ForumTag(name=name, emoji=emoji) for name, emoji in tags
                ],
            )

            raid_chat_channel = await ctx.guild.create_text_channel(
                "│🎮┊레이드채팅방", category=category
            )

            data = {
                "forum_channel_id": forum_channel.id,
                "chat_channel_id": raid_chat_channel.id,
            }
            save_result = await self.api_post(f"/discord/server/{guild_id}", data)

            if not save_result:
                await ctx.followup.send("⚠️ 서버 설정 저장 중 오류가 발생했습니다.")
                return

            embed = discord.Embed(
                title="✅ 레이드일정 시스템 설정 완료!",
                description=(
                    "/레이드 명령어를 사용하여 레이드 일정을 관리할 수 있습니다.\n\n"
                    f"**일정 채널:** {forum_channel.mention}\n"
                    f"**채팅 채널:** {raid_chat_channel.mention}"
                ),
                color=discord.Color.green(),
            )
            await ctx.followup.send(embed=embed)

        except Exception as e:
            await ctx.followup.send(f"포럼 채널 생성 중 오류가 발생했습니다: {e}")
            try:
                await category.delete(reason="포럼 채널 생성 오류로 인한 삭제")
            except Exception:
                pass

    async def party_create(self, interaction: discord.Interaction, raid_date: Optional[str] = None, hour: Optional[str] = None, minute: Optional[str] = None, raid_name: Optional[str] = None, difficulty: Optional[str] = None, messages: Optional[str] = None, auto_join_nickname: Optional[str] = None):
        try:
            guild_id = interaction.guild.id
            user_id = interaction.user.id

            # 진행 메시지
            progress = await interaction.followup.send(
                content="서버 설정을 확인하고 있습니다...", ephemeral=True
            )

            # 시간 검증
            if not (raid_date and hour and minute):
                start_date_str = None
            else:
                start_date_str = self._validate_datetime(raid_date, hour, minute)
                if start_date_str is False:
                    await interaction.followup.edit_message(
                        progress.id, content="선택하신 날짜와 시간으로는 공지 등록이 불가능합니다."
                    )
                    return
            
            if not self._validate_boss_diff_local(raid_name, difficulty):
                await interaction.followup.edit_message(
                    progress.id,
                    content=f"선택한 보스 '{raid_name}'에 난이도 '{difficulty}'는 존재하지 않아요. 다시 선택하세요."
                )
                return
                
            # 서버 설정 확인
            server_config = await self.api_get(f"/discord/server/{guild_id}")
            if not server_config:
                await interaction.followup.edit_message(
                    progress.id,
                    content="당신의 서버는 아직 기초 설정이 되지 않았습니다. 관리자에게 문의하세요.",
                )
                return

            # 파티 제목
            title = self._generate_party_title(
                raid_name, start_date_str, difficulty, messages
            )

            # 파티 생성
            await interaction.followup.edit_message(progress.id, content="파티를 생성하고 있습니다...")
            party_result = await self.api_post(
                f"/party/{guild_id}/create",
                data={
                    "guild_id": str(guild_id),
                    "title": title,
                    "raid_name": raid_name,
                    "difficulty": difficulty,
                    "start_date": start_date_str,
                    "owner_id": user_id,
                    "message": messages or "",
                },
            )

            if not party_result:
                await interaction.followup.edit_message(
                    progress.id, content="파티 생성 중 오류가 발생했습니다. 다시 시도해주세요."
                )
                return

            join_ok: Optional[bool] = None
            join_msg: Optional[str] = None
            auto_nick = (auto_join_nickname or "").strip() if auto_join_nickname else ""

            if auto_nick:
                party_id_raw = party_result.get("party_id")
                try:
                    party_id = int(party_id_raw)
                except Exception:
                    party_id = None

                if party_id:
                    join_ok, join_msg = await auto_join_party_by_nickname(party_id, user_id, auto_nick)
                else:
                    join_ok, join_msg = False, "생성된 일정 ID를 찾을 수 없어 자동 참가에 실패했어요."

            await self._send_completion_message(interaction, progress.id, party_result, raid_name, difficulty, user_id)

            if join_ok is not None:
                if join_ok:
                    msg = f"✅ '{auto_nick}' 캐릭터로 방금 만든 일정에 자동 참가했어요."
                    if join_msg:
                        msg += f"\n{join_msg}"
                else:
                    msg = "⚠️ 일정은 생성됐지만 자동 참가에는 실패했어요."
                    if join_msg:
                        msg += f"\n{join_msg}"
                await interaction.followup.send(msg, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(content=f"오류가 발생했습니다: {str(e)}", ephemeral=True)

    async def party_edit(self, interaction: discord.Interaction, raid_data: dict, raid_date: Optional[str] = None, hour: Optional[str] = None, minute: Optional[str] = None, messages: Optional[str] = None):
        try:
            guild_id = interaction.guild.id
            user_id = interaction.user.id

            progress = await interaction.followup.send(
                content="서버 설정을 확인하고 있습니다...", ephemeral=True, wait=True
            )

            # 시간 검증
            if not (raid_date and hour and minute):
                start_date_str = None
            else:
                start_date_str = self._validate_datetime(raid_date, hour, minute)
                if start_date_str is False:
                    await interaction.followup.edit_message(
                        progress.id, content="선택하신 날짜와 시간으로는 공지 수정이 불가능합니다."
                    )
                    return
            # 서버 설정 확인
            server_config = await self.api_get(f"/discord/server/{guild_id}")
            if not server_config:
                await interaction.followup.edit_message(
                    progress.id,
                    content="당신의 서버는 아직 기초 설정이 되지 않았습니다. 관리자에게 문의하세요.",
                )
                return

            raid_name = raid_data.get("raid_name")
            difficulty = raid_data.get("difficulty")
            party_id = raid_data.get("id")

            # 파티 제목
            title = self._generate_party_title(
                raid_name, start_date_str, difficulty, messages
            )

            data = {
                "title": title,
                "guild_id": str(guild_id),
                "raid_name": raid_name,
                "difficulty": difficulty,
                "start_date": start_date_str,
                "owner_id": user_id,
                "message": messages or "",
            }

            await interaction.followup.edit_message(progress.id, content="파티를 수정하고 있습니다...")
            await self.api_patch(f"/party/{party_id}/edit", data=data)

            await interaction.followup.edit_message(progress.id, content="파티 수정이 완료되었어요!")

        except Exception as e:
            await interaction.followup.send(content=f"오류가 발생했습니다: {str(e)}", ephemeral=True)

    # ---------- 내부 유틸 ----------

    def _format_datetime_with_weekday(self, date_str: str, hour: str, minute: str) -> str:
        """'%y.%m.%d(요일) %H:%M' 포맷으로 반환 (입력 예: '25.09.01(월)')"""
        try:
            base_date, weekday = date_str.split("(")
            base_date = base_date.strip()
            weekday = weekday.replace(")", "").strip()
            # 유효성 체크
            datetime.strptime(base_date, "%y.%m.%d")
            return f"{base_date}({weekday}) {hour}:{minute}"
        except Exception:
            # 포맷이 달라도 최대한 살려서 반환
            return f"{date_str} {hour}:{minute}"
    
    def _validate_boss_diff_local(self, raid_name: Optional[str], difficulty: Optional[str]) -> bool:
        if not raid_name or not difficulty:
            return True

        diffs = raid_difficulty_map.get(raid_name)
        if not diffs:
            return False
        return difficulty in diffs
        
    def _validate_datetime(self, raid_date: str, hour: str, minute: str) -> str | bool | None:
        if not (raid_date and hour and minute):
            return None
        try:
            formatted = self._format_datetime_with_weekday(raid_date, hour, minute)
            base_date = raid_date.split("(")[0].strip()
            dt = datetime.strptime(base_date, "%y.%m.%d")
            dt = dt.replace(hour=int(hour), minute=int(minute))
            seoul = pytz.timezone("Asia/Seoul")
            dt = seoul.localize(dt)
            now = datetime.now(seoul)
            if dt < now:
                return False
            return formatted
        except Exception:
            return False

    def _generate_party_title(self, raid_name: Optional[str], start_date_str: Optional[str], difficulty: Optional[str], messages: Optional[str]) -> str:
        raid = (raid_name or "").strip()
        diff = (difficulty or "").strip()
        msg = (messages or "").strip()

        if raid and diff:
            head = f"[{raid} : {diff}]"
        elif raid:
            head = f"[{raid}]"
        elif diff:
            head = f"[{diff}]"
        else:
            head = "[레이드]"

        title = head
        if start_date_str:
            title += f" {start_date_str}"
        if msg:
            title += f" : {msg}"
        return title

    async def _send_completion_message(self, interaction: discord.Interaction, progress_message_id: int, party_result: dict, raid_name: Optional[str], difficulty: Optional[str], user_id: int):
        embed = discord.Embed(
            title="✅ 레이드 일정 생성 완료!",
            description=f"**제목:** {party_result.get('title', '')}",
            color=discord.Color.green(),
        )

        if party_result.get("start_date"):
            embed.add_field(name="시작 날짜", value=party_result["start_date"], inline=True)

        if raid_name or difficulty:
            embed.add_field(
                name="레이드",
                value=f"{raid_name or ''} {difficulty or ''}".strip(),
                inline=True,
            )

        embed.add_field(name="공대장", value=f"<@{user_id}>", inline=True)

        await interaction.followup.edit_message(progress_message_id, content="", embed=embed)