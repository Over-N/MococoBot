import discord
from discord import option
from discord.ext import commands
from core.tts_channels import tts_channel_manager
from core.http_client import http_client
from handler.tts import (
    _ffmpeg_from_bytes,
    add_guild_custom_sound,
    get_guild_custom_sounds,
    remove_guild_custom_sound,
    join_voice_channel,
    disconnect_from_guild,
    AUDIO_BASE_PATH,
    force_reset_guild_tts
)
import os
import re

try:
    from core.tts_engine_manager import tts_engine_manager  # type: ignore
except Exception:
    tts_engine_manager = None  # type: ignore

ENGINE_LABELS = {
    "engine1": "여성 목소리 - 1 (Default)",
    "engine2": "여성 목소리 - 2 (SH)",
    "engine3": "남성 목소리 - 1 (TT)",
    "engine4": "여성 목소리 - 3 (TT)",
    "engine5": "남성 목소리 - 3 (TT)",
    "engine7": "남성 목소리 - 2 (HM)",
    "engine9": "남성 목소리 - 4 (IJ)",
}
ENGINE_CHOICES = [
    discord.OptionChoice(name=f"{k} - {v}", value=k)
    for k, v in ENGINE_LABELS.items()
]

THUMB_URL = "https://i.namu.wiki/i/u253q5zv58zJ1twYkeea-czVz8SQsvX-a1jVZ8oYsTVDH_TRC8-bpcVa4aKYQs5lI55B9srLYF9JJFUPbkI8MA.webp"


class TTSCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _engine_desc(self, engine_id: str) -> str:
        return ENGINE_LABELS.get(engine_id, engine_id)

    def _parse_engine_id(self, data) -> str | None:
        if not isinstance(data, dict):
            return None
        for key in ("engine_id", "engine"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
        v = data.get("data")
        if isinstance(v, dict):
            vv = v.get("engine_id") or v.get("engine")
            if isinstance(vv, str) and vv:
                return vv
        return None

    def _embed_to_text(self, embed: discord.Embed) -> str:
        parts = []
        if embed.title:
            parts.append(str(embed.title))
        if embed.description:
            parts.append(str(embed.description))
        for f in getattr(embed, "fields", []) or []:
            name = getattr(f, "name", "")
            value = getattr(f, "value", "")
            if name or value:
                parts.append(f"{name}\n{value}".strip())
        return "\n".join([p for p in parts if p]).strip() or "요청을 처리할 수 없습니다."

    async def _safe_respond(self, ctx: discord.ApplicationContext, *, content: str | None = None, embed: discord.Embed | None = None, ephemeral: bool = True):
        try:
            return await ctx.respond(content=content, embed=embed, ephemeral=ephemeral)
        except Exception:
            try:
                return await ctx.followup.send(content=content, embed=embed, ephemeral=ephemeral)
            except discord.Forbidden:
                if embed and not content:
                    try:
                        return await ctx.followup.send(content=self._embed_to_text(embed), ephemeral=ephemeral)
                    except Exception:
                        return None
                return None
            except Exception:
                return None

    async def _safe_edit_original(self, ctx: discord.ApplicationContext, *, content: str | None = None, embed: discord.Embed | None = None, ephemeral_fallback: bool = True):
        try:
            return await ctx.interaction.edit_original_response(content=content, embed=embed)
        except discord.Forbidden:
            if embed and not content:
                return await self._safe_respond(ctx, content=self._embed_to_text(embed), ephemeral=ephemeral_fallback)
            return await self._safe_respond(ctx, content=content, embed=embed, ephemeral=ephemeral_fallback)
        except Exception:
            return await self._safe_respond(ctx, content=content, embed=embed, ephemeral=ephemeral_fallback)

    def _make_embed(self, title: str, description: str, color: int) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_thumbnail(url=THUMB_URL)
        return embed

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        me = getattr(guild, "me", None)
        if me:
            return me
        try:
            return guild.get_member(self.bot.user.id) if self.bot.user else None
        except Exception:
            return None

    def _missing_perms_text(self, missing: list[str]) -> str:
        if not missing:
            return "권한이 부족합니다."
        return "봇 권한이 부족합니다: " + ", ".join(missing)

    async def _handle_perm_error(self, ctx: discord.ApplicationContext, error: Exception):
        if isinstance(error, commands.MissingPermissions):
            await self._safe_respond(ctx, content="권한이 없어 해당 명령어 사용이 불가능해요!", ephemeral=True)
            return
        if isinstance(error, commands.BotMissingPermissions):
            miss = getattr(error, "missing_permissions", None) or []
            txt = "봇 권한이 부족합니다: " + (", ".join(miss) if miss else "필수 권한")
            await self._safe_respond(ctx, content=txt, ephemeral=True)
            return
        if isinstance(error, commands.CheckFailure):
            await self._safe_respond(ctx, content="권한이 없어 해당 명령어 사용이 불가능해요!", ephemeral=True)
            return
        await self._safe_respond(ctx, content="처리 중 오류가 발생했습니다.", ephemeral=True)

    @discord.slash_command(name="엔진", description="사용자별 TTS 목소리 엔진을 설정하거나 조회합니다.")
    @option(
        "voice",
        description="설정할 엔진을 선택하세요. 지정하지 않으면 현재 설정을 보여줍니다.",
        required=False,
        choices=ENGINE_CHOICES,
    )
    async def tts_engine(self, ctx: discord.ApplicationContext, voice: str = None):
        await ctx.response.defer(ephemeral=True)
        user_id = ctx.author.id

        if not voice:
            engine_id = None
            if tts_engine_manager is not None:
                try:
                    engine_id = tts_engine_manager.get_engine(user_id)
                except Exception:
                    engine_id = None

            if not engine_id:
                try:
                    response = await http_client.get(f"/tts/engine/{user_id}")
                except Exception as e:
                    await ctx.followup.send(f"엔진 조회 중 오류가 발생했습니다: {e}", ephemeral=True)
                    return

                if response.status_code == 200:
                    try:
                        data = response.json() or {}
                    except Exception:
                        data = {}
                    engine_id = self._parse_engine_id(data)
                    if engine_id and tts_engine_manager is not None:
                        try:
                            tts_engine_manager.set_engine(user_id, engine_id)
                        except Exception:
                            pass
                elif response.status_code == 404:
                    await ctx.followup.send("현재 엔진이 설정되지 않았습니다. 기본 엔진은 engine1 입니다.", ephemeral=True)
                    return
                else:
                    await ctx.followup.send(f"엔진 조회 실패: {response.status_code} {response.text}", ephemeral=True)
                    return

            if not engine_id:
                engine_id = "engine1"
            await ctx.followup.send(f"현재 TTS 엔진은 **{engine_id}** ({self._engine_desc(engine_id)}) 입니다.", ephemeral=True)
            return

        engine_id = voice
        try:
            response = await http_client.post(f"/tts/engine/{user_id}/{engine_id}")
        except Exception as e:
            await ctx.followup.send(f"엔진 설정 중 오류가 발생했습니다: {e}", ephemeral=True)
            return

        if response.status_code in (200, 201):
            if tts_engine_manager is not None:
                try:
                    tts_engine_manager.set_engine(user_id, engine_id)
                except Exception:
                    pass
            await ctx.followup.send(f"✅ 엔진이 **{engine_id}** ({self._engine_desc(engine_id)}) 으로 설정되었습니다.", ephemeral=True)
            return

        await ctx.followup.send(f"엔진 설정 실패: {response.status_code} {response.text}", ephemeral=True)

    @discord.slash_command(name="tts", description="Mococo Bot TTS 채널을 지정해요.")
    @option("채널", description="TTS를 사용할 채널을 선택하세요.", type=discord.TextChannel)
    @commands.has_permissions(administrator=True)
    async def tts_set(self, ctx: discord.ApplicationContext, 채널: discord.TextChannel):
        await ctx.response.defer(ephemeral=True)
        guild = ctx.guild
        if guild is None:
            await self._safe_edit_original(ctx, content="이 명령어는 서버에서만 사용할 수 있습니다.")
            return

        me = self._bot_member(guild)
        missing = []
        if me is not None:
            perms = 채널.permissions_for(me)
            if not perms.view_channel:
                missing.append("채널 보기(View Channel)")
            if not perms.send_messages:
                missing.append("메시지 보내기(Send Messages)")
            if not perms.embed_links:
                missing.append("임베드 링크(Embed Links)")
        if missing:
            embed = self._make_embed("❌ TTS 채널 설정 실패", self._missing_perms_text(missing), 0xFF6B6B)
            await self._safe_edit_original(ctx, embed=embed)
            return

        try:
            success = await tts_channel_manager.set_channel(guild.id, 채널.id)
        except Exception as e:
            embed = self._make_embed("❌ TTS 채널 설정 실패", f"오류가 발생했습니다: {e}", 0xFF6B6B)
            await self._safe_edit_original(ctx, embed=embed)
            return

        if success:
            embed = self._make_embed(
                "✅ TTS 채널 설정 완료",
                f"{채널.mention} 채널이 TTS 채널로 설정되었습니다.\n해당 채널에서 음성 채널에 있는 사용자의 메시지를 읽어드려요!",
                0x2ECC71,
            )
            await self._safe_edit_original(ctx, embed=embed)
            return

        embed = self._make_embed("❌ TTS 채널 설정 실패", "TTS 채널 설정 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", 0xFF6B6B)
        await self._safe_edit_original(ctx, embed=embed)

    @discord.slash_command(name="join", description="현재 들어가 있는 음성채널로 봇을 불러오고, 이 텍스트 채널을 임시 TTS 채널로 지정합니다.")
    async def tts_join(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        guild = ctx.guild
        if guild is None:
            await ctx.followup.send("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.followup.send("먼저 음성 채널에 들어가 주세요.", ephemeral=True)
            return

        voice_ch: discord.VoiceChannel = ctx.author.voice.channel
        me = self._bot_member(guild)
        if me is not None:
            vperms = voice_ch.permissions_for(me)
            missing = []
            if not vperms.connect:
                missing.append("연결(Connect)")
            if not vperms.speak:
                missing.append("말하기(Speak)")
            if missing:
                await ctx.followup.send("봇 권한이 부족합니다: " + ", ".join(missing), ephemeral=True)
                return

        try:
            tts_channel_manager.set_override(guild.id, ctx.channel.id, by_user_id=ctx.author.id)
        except Exception:
            pass

        try:
            vc, join_embed = await join_voice_channel(voice_ch, guild.id)
        except discord.Forbidden:
            await ctx.followup.send("음성 채널에 연결할 수 없습니다. 봇 권한(연결/말하기)을 확인해 주세요.", ephemeral=True)
            return
        except Exception:
            await ctx.followup.send("음성 채널에 연결할 수 없습니다. 권한(연결/말하기) 또는 FFmpeg를 확인해 주세요.", ephemeral=True)
            return

        if not vc:
            await ctx.followup.send("음성 채널에 연결할 수 없습니다. 권한(연결/말하기) 또는 FFmpeg를 확인해 주세요.", ephemeral=True)
            return

        try:
            if join_embed:
                await ctx.channel.send(embed=join_embed)
        except discord.Forbidden:
            pass
        except Exception:
            pass

        await ctx.followup.send("이 채널에서 보내는 메시지를 읽을게요. (임시 TTS 채널, 설정보다 우선)", ephemeral=True)

    @discord.slash_command(name="leave", description="TTS를 종료하고 음성채널에서 나갑니다. (임시 TTS 채널 해제)")
    async def tts_leave(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        guild = ctx.guild
        if guild is None:
            await ctx.followup.send("이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        try:
            tts_channel_manager.clear_override(guild.id)
        except Exception:
            pass
        try:
            await disconnect_from_guild(guild.id)
        except Exception:
            pass
        await ctx.followup.send("음성채널에서 나갔어요. (임시 TTS 채널 해제됨)", ephemeral=True)

    @discord.slash_command(name="tts해제", description="TTS 기능을 해제합니다.")
    @commands.has_permissions(administrator=True)
    async def tts_remove(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        guild = ctx.guild
        if guild is None:
            await self._safe_edit_original(ctx, content="이 명령어는 서버에서만 사용할 수 있습니다.")
            return

        try:
            current_channel_id = tts_channel_manager.get_channel(guild.id)
        except Exception:
            current_channel_id = None

        if not current_channel_id:
            embed = self._make_embed("⚠️ TTS 채널 없음", "현재 설정된 TTS 채널이 없습니다.", 0xFFA726)
            await self._safe_edit_original(ctx, embed=embed)
            return

        try:
            success = await tts_channel_manager.remove_channel(guild.id)
        except Exception as e:
            embed = self._make_embed("❌ TTS 채널 해제 실패", f"오류가 발생했습니다: {e}", 0xFF6B6B)
            await self._safe_edit_original(ctx, embed=embed)
            return

        if success:
            embed = self._make_embed("✅ TTS 채널 해제 완료", "TTS 채널 설정이 해제되었습니다.", 0x2ECC71)
            await self._safe_edit_original(ctx, embed=embed)
            return

        embed = self._make_embed("❌ TTS 채널 해제 실패", "TTS 채널 해제 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", 0xFF6B6B)
        await self._safe_edit_original(ctx, embed=embed)

    @discord.slash_command(name="tts정보", description="현재 TTS 채널 설정을 확인합니다.")
    async def tts_info(self, ctx: discord.ApplicationContext):
        guild = ctx.guild
        if guild is None:
            await self._safe_respond(ctx, content="이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        try:
            channel_id = tts_channel_manager.get_channel(guild.id)
        except Exception:
            channel_id = None

        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                embed = self._make_embed(
                    "🎤 TTS 채널 정보",
                    f"현재 TTS 채널: {channel.mention}\n해당 채널에서 음성 채널에 있는 사용자의 메시지를 읽어드려요!",
                    0x2ECC71,
                )
            else:
                embed = self._make_embed(
                    "⚠️ TTS 채널 오류",
                    "설정된 TTS 채널을 찾을 수 없습니다. 채널이 삭제되었거나 봇이 접근할 수 없습니다.",
                    0xFFA726,
                )
        else:
            embed = self._make_embed(
                "📢 TTS 채널 없음",
                "현재 설정된 TTS 채널이 없습니다.\n`/tts` 명령어로 TTS 채널을 설정해주세요.",
                0x95A5A6,
            )

        await self._safe_respond(ctx, embed=embed, ephemeral=True)

    @discord.slash_command(name="tts해결", description="TTS 사용 중 문제가 발생했을 때 상태를 초기화합니다.")
    async def tts_fix(self, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        guild = ctx.guild
        if guild is None:
            await ctx.followup.send("이 명령어는 길드(서버) 내에서만 사용할 수 있습니다.", ephemeral=True)
            return
        try:
            await force_reset_guild_tts(guild=guild, reason=f"manual:{ctx.author.id}")
        except Exception:
            pass
        try:
            tts_channel_manager.clear_override(guild.id)
        except Exception:
            pass
        embed = self._make_embed("🔧 TTS 상태 초기화", "TTS 관련 상태를 초기화했습니다. 다시 메시지를 보내 TTS를 사용해 보세요.", 0x3498DB)
        await ctx.followup.send(embed=embed, ephemeral=True)

    @discord.slash_command(name="tts커스텀", description="서버별 커스텀 TTS를 추가/삭제하거나 현재 상태를 조회합니다.")
    @option(
        "옵션",
        description="추가 또는 삭제를 선택하세요. 지정하지 않으면 현재 커스텀 목록을 보여줍니다.",
        required=False,
        choices=[
            discord.OptionChoice(name="추가", value="add"),
            discord.OptionChoice(name="삭제", value="remove"),
        ],
    )
    @option("별명", description="추가하거나 삭제할 커스텀 사운드의 별명을 입력하세요.", required=False)
    @option("파일", description="추가할 MP3 파일 (최대 25MB, 10초 이하)", type=discord.Attachment, required=False)
    @commands.has_permissions(administrator=True)
    async def tts_custom(self, ctx: discord.ApplicationContext, 옵션: str = None, 별명: str = None, 파일: discord.Attachment = None):
        guild = ctx.guild
        if guild is None:
            await self._safe_respond(ctx, content="이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        gid = guild.id
        await ctx.response.defer(ephemeral=True)

        if not 옵션:
            items = get_guild_custom_sounds(gid)
            if items:
                lines = []
                for item in items:
                    aliases = ", ".join(str(a) for a in item.get("aliases", []) if a)
                    lines.append(f"• **{item['key']}** (별명: {aliases})")
                desc = "\n".join(lines)
            else:
                desc = "등록된 커스텀 사운드가 없습니다.\n`옵션`을 \"추가\"로 지정하여 새 음성파일을 등록할 수 있습니다."
            warn = "각 서버는 최대 **8개**의 커스텀 사운드를 등록할 수 있으며, 각 음성 파일은 **10초 이하**여야 해요."
            embed = discord.Embed(title="🎵 현재 커스텀 TTS 목록", description=desc, color=0x3498DB)
            embed.set_thumbnail(url=THUMB_URL)
            embed.add_field(name="⚠️ 제한 사항", value=warn, inline=False)
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        if 옵션 == "add":
            if not 별명 or not 파일:
                await ctx.followup.send("별명과 파일을 모두 제공해야 합니다.", ephemeral=True)
                return

            alias_str = str(별명).strip()
            if alias_str.startswith("[") and alias_str.endswith("]"):
                alias_str = alias_str[1:-1].strip()
            if not alias_str:
                await ctx.followup.send("별명이 유효하지 않습니다.", ephemeral=True)
                return

            existing = get_guild_custom_sounds(gid)
            if any(item.get("key") == alias_str for item in existing):
                await ctx.followup.send(f"이미 `{alias_str}` 별명이 등록되어 있습니다.", ephemeral=True)
                return
            if len(existing) >= 8:
                await ctx.followup.send("커스텀 사운드는 최대 8개까지 등록할 수 있습니다.", ephemeral=True)
                return

            try:
                if 파일.size > 25 * 1024 * 1024:
                    await ctx.followup.send("파일 크기가 25MB를 초과합니다.", ephemeral=True)
                    return
            except Exception:
                pass

            file_ext = (파일.filename or "").rsplit(".", 1)[-1].lower()
            if file_ext != "mp3":
                await ctx.followup.send("MP3 파일만 지원합니다.", ephemeral=True)
                return

            try:
                mp3_bytes = await 파일.read()
            except Exception as e:
                await ctx.followup.send(f"파일을 읽을 수 없습니다: {e}", ephemeral=True)
                return

            try:
                audio_data, _ = await _ffmpeg_from_bytes(mp3_bytes, volume=1.0, trim_tail=False)
                if not audio_data:
                    raise RuntimeError("오디오 변환 실패")
                length_sec = len(audio_data) / (48000 * 2 * 2)
                if length_sec > 10.0:
                    await ctx.followup.send("오디오 길이가 10초를 초과합니다.", ephemeral=True)
                    return
            except Exception:
                await ctx.followup.send("오디오 파일을 분석하는 중 오류가 발생했습니다. 다른 파일을 사용해 주세요.", ephemeral=True)
                return

            base_dir = AUDIO_BASE_PATH
            guild_dir = os.path.join(base_dir, str(gid))
            try:
                os.makedirs(guild_dir, exist_ok=True)
            except Exception as e:
                await ctx.followup.send(f"디렉토리 생성 중 오류가 발생했습니다: {e}", ephemeral=True)
                return

            safe_name = re.sub(r"[^\w가-힣_-]", "_", alias_str)
            file_name = f"{safe_name}.mp3"
            file_path = os.path.join(guild_dir, file_name)

            try:
                with open(file_path, "wb") as f:
                    f.write(mp3_bytes)
            except Exception as e:
                await ctx.followup.send(f"파일 저장 중 오류가 발생했습니다: {e}", ephemeral=True)
                return

            aliases = [alias_str, f"[{alias_str}]"]
            try:
                add_guild_custom_sound(gid, alias_str, file_name, aliases)
            except Exception as e:
                await ctx.followup.send(f"커스텀 사운드 등록 중 오류가 발생했습니다: {e}", ephemeral=True)
                return

            embed = discord.Embed(
                title="✅ 커스텀 사운드 추가 완료",
                description=f"`{alias_str}` 별명의 커스텀 사운드가 등록되었습니다!\nTTS 채팅에서 `[ {alias_str} ]` 라고 입력하면 이 음성이 재생됩니다.",
                color=0x2ECC71,
            )
            embed.set_thumbnail(url=THUMB_URL)
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        if 옵션 == "remove":
            if not 별명:
                await ctx.followup.send("삭제할 별명을 입력해주세요.", ephemeral=True)
                return

            alias_str = str(별명).strip()
            if alias_str.startswith("[") and alias_str.endswith("]"):
                alias_str = alias_str[1:-1].strip()

            existing = get_guild_custom_sounds(gid)
            if not any(item.get("key") == alias_str for item in existing):
                await ctx.followup.send("해당 별명의 커스텀 사운드가 존재하지 않습니다.", ephemeral=True)
                return

            try:
                remove_guild_custom_sound(gid, alias_str)
            except Exception as e:
                await ctx.followup.send(f"커스텀 사운드 삭제 중 오류가 발생했습니다: {e}", ephemeral=True)
                return

            embed = discord.Embed(title="🗑️ 커스텀 사운드 삭제 완료", description=f"`{alias_str}` 별명의 커스텀 사운드가 삭제되었습니다.", color=0xFF6B6B)
            embed.set_thumbnail(url=THUMB_URL)
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        await ctx.followup.send("알 수 없는 옵션입니다. 옵션을 생략하거나 \"추가\", \"삭제\" 중 하나를 선택하세요.", ephemeral=True)

    @tts_set.error
    async def tts_set_error(self, ctx: discord.ApplicationContext, error):
        await self._handle_perm_error(ctx, error)

    @tts_remove.error
    async def tts_remove_error(self, ctx: discord.ApplicationContext, error):
        await self._handle_perm_error(ctx, error)

    @tts_custom.error
    async def tts_custom_error(self, ctx: discord.ApplicationContext, error):
        await self._handle_perm_error(ctx, error)


def setup(bot):
    bot.add_cog(TTSCog(bot))
