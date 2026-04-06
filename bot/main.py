import discord
import httpx
import asyncio
import contextlib
import time
from typing import Iterable, List, Awaitable, Any
from collections.abc import Coroutine
from core.config import BOT_TOKEN, API_KEY
from core.raid_data import load_raids
from core.tts_channels import tts_channel_manager
from handler.party import (
    handle_party_join,
    handle_party_leave,
    handle_party_force_cancel,
    handle_party_mention,
    handle_party_delete,
    handle_party_public,
    handle_party_image,
    handle_party_waitlist
)
from core.http_client import http_client
from handler.raid_role import handle_raid_role_manage
from handler.tts import handle_voice_state_update, cleanup_all_connections, handle_tts_message, cleanup_guild_state
from handler.sticker import initialize_sticker, handle_sticker_message
from handler.siblings import handle_expedition_register_button
from handler.verify import handle_verify_button
from handler.quiz import open_quiz_answer_modal
from handler.friends import (
    open_profile_modal, open_match_candidate, handle_like, handle_pass,
    open_my_profile, start_profile_delete, confirm_profile_delete
)

_background_tasks: set[asyncio.Task] = set()
HOTPATH_SKIP_PREFIXES: tuple[str, ...] = ("/",)
LOOP_LAG_WARN_SEC = 1.5
LOOP_LAG_CHECK_INTERVAL_SEC = 10.0
GATEWAY_RECOVERY_WARN_SEC = 45.0


def _bg_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"[BG Task] result check failed: {e}")
        return
    if exc is not None:
        task_name = getattr(task, "get_name", lambda: "")() or "unnamed"
        print(f"[BG Task Error] {task_name}: {exc!r}")


def create_bg_task(coro: Awaitable[Any], *, name: str | None = None) -> asyncio.Task:
    t = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_bg_task_done)
    return t

async def cancel_all_bg_tasks():
    if not _background_tasks:
        return
    for t in list(_background_tasks):
        t.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

async def safe_http_post(url: str, *, json=None, headers=None, timeout: float = 5.0):
    try:
        return await asyncio.wait_for(http_client.post(url, json=json, headers=headers), timeout=timeout)
    except Exception as e:
        print(f"[POST skipped] {url} -> {e}")

async def run_periodic(interval_sec: float, coro_fn, *args, immediate: bool = False, **kwargs):
    if immediate:
        with contextlib.suppress(Exception):
            await coro_fn(*args, **kwargs)
    while True:
        try:
            await asyncio.sleep(interval_sec)
            await coro_fn(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Periodic] {coro_fn.__name__} error: {e}")


async def monitor_event_loop_lag(interval_sec: float = LOOP_LAG_CHECK_INTERVAL_SEC, warn_sec: float = LOOP_LAG_WARN_SEC):
    loop = asyncio.get_running_loop()
    next_tick = loop.time() + interval_sec
    while True:
        await asyncio.sleep(interval_sec)
        now = loop.time()
        lag = now - next_tick
        if lag > warn_sec:
            print(f"[Loop Lag] {lag:.2f}s (threshold={warn_sec:.2f}s)")
        next_tick = now + interval_sec

async def chunked(iterable: Iterable, n: int):
    batch: List = []
    for x in iterable:
        batch.append(x)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch

class MococoBot(discord.AutoShardedBot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.voice_states = True
        if hasattr(intents, "moderation"):
            intents.moderation = True
        elif hasattr(intents, "bans"):
            intents.bans = True
        super().__init__(
            intents=intents,
            chunk_guilds_at_startup=False,
        )
        self.start_time_ts: float | None = None
        self._bg_init_lock = asyncio.Lock()
        self._bg_init_started = False
        self._bg_init_done = False
        self._shutting_down = False
        self._gw_watchdog_task: asyncio.Task | None = None
        self._gw_watchdog_token = 0

    async def get_or_create_unverified_role(self, guild: discord.Guild) -> discord.Role | None:
        try:
            role = discord.utils.get(guild.roles, name="미인증")
            if role:
                return role
            role = await guild.create_role(name="미인증", reason="인증 시스템: 미인증 기본 역할 생성")
            return role
        except discord.Forbidden:
            print(f"[미인증] 역할 생성 실패(권한 부족): {guild.name}({guild.id})")
            return None
        except Exception as e:
            print(f"[미인증] 역할 생성 오류: {e}")
            return None

    async def update_status(self):
        try:
            server_count = len(self.guilds)
            activity = discord.Activity(
                type=discord.ActivityType.playing,
                name=f"{server_count}개 서버에서 활동"
            )
            await self.change_presence(activity=activity, status=discord.Status.online)
        except Exception as e:
            print(f"[Presence] 업데이트 실패: {e}")

    async def on_ready(self):
        print(f"[Gateway] on_ready fired user={self.user!s}")
        print(f"[Gateway] guild_count={len(self.guilds)}")
        self._mark_gateway_recovered("on_ready")
        await self._ensure_bg_init_once()

    async def on_connect(self):
        print("[Gateway] on_connect")

    async def on_disconnect(self):
        print("[Gateway] on_disconnect")
        self._arm_gateway_watchdog("on_disconnect")

    async def on_shard_connect(self, shard_id: int):
        print(f"[Gateway] shard_connect shard={shard_id}")

    async def on_shard_ready(self, shard_id: int):
        print(f"[Gateway] shard_ready shard={shard_id}")
        self._mark_gateway_recovered(f"on_shard_ready:{shard_id}")

    async def on_shard_disconnect(self, shard_id: int):
        print(f"[Gateway] shard_disconnect shard={shard_id}")
        self._arm_gateway_watchdog(f"on_shard_disconnect:{shard_id}")

    async def on_resumed(self):
        print("[Gateway] on_resumed")
        self._mark_gateway_recovered("on_resumed")

    def _arm_gateway_watchdog(self, reason: str):
        if self._shutting_down:
            return
        self._gw_watchdog_token += 1
        token = self._gw_watchdog_token
        if self._gw_watchdog_task and not self._gw_watchdog_task.done():
            self._gw_watchdog_task.cancel()
        self._gw_watchdog_task = create_bg_task(
            self._gateway_recovery_watchdog(token, reason),
            name="gateway_recovery_watchdog"
        )

    def _mark_gateway_recovered(self, reason: str):
        if self._gw_watchdog_task and not self._gw_watchdog_task.done():
            self._gw_watchdog_task.cancel()
            self._gw_watchdog_task = None
            print(f"[Gateway Watchdog] recovered via {reason}")

    async def _gateway_recovery_watchdog(self, token: int, reason: str):
        try:
            await asyncio.sleep(GATEWAY_RECOVERY_WARN_SEC)
            if self._shutting_down or token != self._gw_watchdog_token:
                return
            if self.is_closed():
                return
            print(
                f"[Gateway Watchdog] no recovery signal for {GATEWAY_RECOVERY_WARN_SEC:.0f}s "
                f"after {reason} (ready={self.is_ready()} guilds={len(self.guilds)} latency={self.latency:.3f}s)"
            )
        except asyncio.CancelledError:
            raise
        finally:
            if self._gw_watchdog_task is asyncio.current_task():
                self._gw_watchdog_task = None

    async def _ensure_bg_init_once(self):
        if self._bg_init_done:
            return
        async with self._bg_init_lock:
            if self._bg_init_done or self._bg_init_started:
                return
            self._bg_init_started = True
            create_bg_task(self._run_bg_init_once(), name="bg_init_once")

    async def _run_bg_init_once(self):
        try:
            await self.register_commands(force=True, delete_existing=True)
            print("[Startup] application commands force-registered")
        except Exception as e:
            print(f"[Startup] application commands sync failed: {e}")

        try:
            await self._bg_init_tasks_once()
        finally:
            self._bg_init_done = True
            self._bg_init_started = False

    async def _bg_init_tasks_once(self):
        try:
            from handler.tts import cleanup_all_connections
            await cleanup_all_connections()
        except Exception:
            pass
        try:
            await tts_channel_manager.load_all_channels()
        except Exception as e:
            print(f"[TTS] 채널 로드 실패: {e}")

        try:
            from core.tts_engine_manager import tts_engine_manager
        except Exception:
            tts_engine_manager = None
        if tts_engine_manager is not None:
            with contextlib.suppress(Exception):
                await tts_engine_manager.load_all()

        with contextlib.suppress(Exception):
            await initialize_sticker()

        await self.update_status()

        try:
            await self._bulk_sync_guilds()
        except Exception as e:
            print(f"[botsync] 초기 동기화 실패: {e}")

        if not any(t for t in _background_tasks if not t.cancelled() and "presence" in ((getattr(t, "get_name", lambda: "")() or "").lower())):
            create_bg_task(run_periodic(300.0, self.update_status, immediate=False), name="presence_update")

        if not any(t for t in _background_tasks if not t.cancelled() and "loop_lag" in ((getattr(t, "get_name", lambda: "")() or "").lower())):
            create_bg_task(monitor_event_loop_lag(), name="loop_lag_monitor")

        self.start_time_ts = __import__("time").time()
        print("봇 초기화 완료")

    async def _bulk_sync_guilds(self):
        payload_iter = (
            {
                "id": g.id,
                "name": g.name,
                "icon": (g.icon.url if g.icon else None),
                "owner_id": g.owner_id
            }
            for g in self.guilds
        )
        async for batch in chunked(payload_iter, 200):
            await safe_http_post(
                "/botsync/botsync/guilds/bulk_upsert",
                json=batch,
                headers={"X-API-Key": API_KEY},
                timeout=5.0
            )
            await asyncio.sleep(0)


    async def on_guild_join(self, guild: discord.Guild):
        create_bg_task(safe_http_post(
            "/botsync/botsync/guilds/bulk_upsert",
            json=[{
                "id": guild.id,
                "name": guild.name,
                "icon": (guild.icon.url if guild.icon else None),
                "owner_id": guild.owner_id
            }],
            headers={"X-API-Key": API_KEY},
            timeout=5.0
        ), name="guild_join_sync")
        create_bg_task(self._send_welcome(guild), name="guild_join_welcome")
        await self.update_status()

    async def _send_welcome(self, guild: discord.Guild):
        target_channel = None
        try:
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                target_channel = guild.system_channel
            elif guild.rules_channel and guild.rules_channel.permissions_for(guild.me).send_messages:
                target_channel = guild.rules_channel
            else:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        target_channel = channel
                        break
            if not target_channel:
                print(f"서버 입장 가이드: {guild.name} - 메시지를 보낼 수 있는 채널이 없습니다.")
                return
            embed = discord.Embed(
                title="🎉 모코코 봇이 서버에 참가했어요!",
                description="안녕하세요! 로스트아크 일정관리 봇 **Mococo**에요.\n다양한 기능으로 디스코드 서버 운영에 도움을 드릴게요!",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="🚀 시작하기",
                value=("1️⃣ `/setups` 으로 레이드 채널들을 생성해주세요.\n"
                       "2️⃣ `/서버설정` 으로 기본 설정을 해주세요.\n"
                       "3️⃣ `/레이드` 로 첫 번째 일정을 만들어보세요!\n\n"
                       "자세한 내용은 https://mococobot.kr/start 참고 해주세요."),
                inline=False
            )
            if self.user and self.user.display_avatar:
                embed.set_footer(
                    text="💡 관리자 권한이 필요한 명령어들이 있어요. 설정은 관리자가 진행해주세요!",
                    icon_url=self.user.display_avatar.url
                )
                embed.set_thumbnail(url=self.user.display_avatar.url)
            await target_channel.send(embed=embed)
        except Exception as e:
            print(f"서버 입장 가이드 전송 실패: {guild.name if guild else 'Unknown'} - {e}")

    async def on_guild_remove(self, guild: discord.Guild):
        create_bg_task(safe_http_post(
            f"/discord/server/{guild.id}".replace("/discord/server", "/botsync/botsync/guilds"),
            json=None,
            headers={"X-API-Key": API_KEY},
            timeout=5.0
        ), name="guild_remove_sync")
        print(f"서버 퇴장: {guild.name} ({guild.id})")

        async def _cleanup_party_and_configs():
            with contextlib.suppress(Exception):
                r = await http_client.get(f"/party/list?guild_id={guild.id}")
                if getattr(r, "status_code", 0) == 200:
                    data = r.json()
                    parties = data.get("data", []) if isinstance(data.get("data"), list) else []
                    for p in parties:
                        pid = p.get("id")
                        if pid:
                            with contextlib.suppress(Exception):
                                await http_client.delete(f"/party/{pid}/delete")
            for path in [f"/discord/server/{guild.id}", f"/verify/{guild.id}/config"]:
                with contextlib.suppress(Exception):
                    await http_client.delete(path)

        create_bg_task(_cleanup_party_and_configs(), name="guild_remove_cleanup")
        await self.update_status()

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        try:
            r = await http_client.get(f"/verify/{member.guild.id}/config")
            if getattr(r, "status_code", 0) != 200:
                return
            role = await self.get_or_create_unverified_role(member.guild)
            if role and role not in member.roles:
                with contextlib.suppress(Exception):
                    await member.add_roles(role, reason="서버 입장: 인증 전 상태")
        except Exception as e:
            print(f"[on_member_join] 처리 오류: {e}")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if isinstance(message.channel, discord.DMChannel):
            try:
                atts = [{"filename": a.filename, "size": a.size, "content_type": a.content_type}
                        for a in (message.attachments or [])]
                r = await http_client.post(
                    "/friends/relay",
                    json={"user_id": message.author.id, "content": message.content or "", "attachments": atts or None}
                )
                data = r.json() if getattr(r, "status_code", 0) == 200 else {}
                if data.get("ok"):
                    partner = await self.fetch_user(int(data["partner_id"]))
                    if message.content:
                        await partner.send(message.content)
                    for a in message.attachments:
                        await partner.send(file=await a.to_file())
                else:
                    await message.channel.send("현재 활성 매칭이 없어요. 매칭후 사용해 주세요.\n-# `/친구` 명령어로 매칭을 시작할 수 있어요.")
            except Exception as e:
                print(f"[DM relay] failed user={message.author.id}: {e}")
            return

        guild = message.guild
        if guild is None:
            return

        channel = message.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        content = message.content or ""
        if content and content.startswith(HOTPATH_SKIP_PREFIXES):
            return

        gid = guild.id
        channel_id = channel.id
        has_bracket_token = "[" in content and "]" in content
        is_tts_channel = tts_channel_manager.is_tts_channel(gid, channel_id)

        if not has_bracket_token and not is_tts_channel:
            return

        sticker_sent = False
        if has_bracket_token:
            sticker_sent = await handle_sticker_message(message)
        if not sticker_sent and is_tts_channel:
            await handle_tts_message(message)
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        try:
            await http_client.delete(f"/party/guilds/{member.guild.id}/participants/{member.id}")
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPError) as e:
            print(f"party delete failed guild={member.guild.id} member={member.id} err={e!r}")
        
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if user.bot:
            return
        try:
            await asyncio.wait_for(
                http_client.delete(f"/party/guilds/{guild.id}/participants/{user.id}"),
                timeout=5.0
            )
        except Exception as e:
            print(f"party ban delete failed guild={guild.id} user={user.id} err={e!r}")
        
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        await handle_voice_state_update(member, before, after)
        try:
            if self.user and member.id == self.user.id and after.channel is None:
                await cleanup_guild_state(member.guild.id)
        except Exception:
            pass

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            await self.process_application_commands(interaction)

        try:
            data = getattr(interaction, "data", None) or {}
            custom_id = data.get("custom_id", "")
            if not custom_id:
                return

            if custom_id.startswith("party_join_"):
                await handle_party_join(interaction, int(custom_id.replace("party_join_", "")))
            elif custom_id.startswith("party_leave_"):
                await handle_party_leave(interaction, int(custom_id.replace("party_leave_", "")))
            elif custom_id.startswith("party_force_cancel_"):
                await handle_party_force_cancel(interaction, int(custom_id.replace("party_force_cancel_", "")))
            elif custom_id.startswith("party_mention_"):
                await handle_party_mention(interaction, int(custom_id.replace("party_mention_", "")))
            elif custom_id.startswith("party_public_"):
                await handle_party_public(interaction, int(custom_id.replace("party_public_", "")))
            elif custom_id.startswith("party_waitlist_"):
                await handle_party_waitlist(interaction, int(custom_id.rsplit("_", 1)[-1]))
            elif custom_id.startswith("party_image_"):
                await handle_party_image(interaction, int(custom_id.replace("party_image_", "")))
            elif custom_id.startswith("party_delete_"):
                await handle_party_delete(interaction, int(custom_id.replace("party_delete_", "")))
            elif custom_id.startswith("expedition_register_button"):
                await handle_expedition_register_button(interaction)
            elif custom_id == "raid_role_manage":
                await handle_raid_role_manage(interaction)
            elif custom_id == "verify_button":
                await handle_verify_button(interaction)
            elif custom_id.startswith("quiz_answer:"):
                await open_quiz_answer_modal(interaction)
            elif custom_id == "ff_profile":
                await open_profile_modal(interaction)
            elif custom_id == "ff_match":
                await open_match_candidate(interaction)
            elif custom_id == "ff_myprofile":
                await open_my_profile(interaction)
            elif custom_id == "ff_profile_delete":
                await start_profile_delete(interaction)
            elif custom_id == "ff_profile_delete_confirm":
                await confirm_profile_delete(interaction)
            elif custom_id == "ff_profile_delete_cancel":
                with contextlib.suppress(Exception):
                    await interaction.response.edit_message(content="취소했어요.", embed=None, view=None)
            elif custom_id.startswith("ff_like:"):
                await handle_like(interaction, int(custom_id.split(":")[1]))
            elif custom_id.startswith("ff_pass:"):
                await handle_pass(interaction, int(custom_id.split(":")[1]))
        except Exception as e:
            print(f"[Interaction Error] {e}")

    async def on_thread_delete(self, thread: discord.Thread):
        with contextlib.suppress(Exception):
            r = await http_client.get(f"/party/{thread.id}/thread")
            if getattr(r, "status_code", 0) == 200:
                party_id = (r.json() or {}).get("id")
                if party_id:
                    await http_client.delete(f"/party/{party_id}/delete")

    async def close(self):
        self._shutting_down = True
        if self._gw_watchdog_task and not self._gw_watchdog_task.done():
            self._gw_watchdog_task.cancel()
        await cancel_all_bg_tasks()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(cleanup_all_connections(), timeout=12.0)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(http_client.aclose(), timeout=5.0)
        await super().close()

async def main():
    loop = asyncio.get_running_loop()

    def _loop_exception_handler(loop, context):
        msg = context.get("message", "asyncio loop exception")
        exc = context.get("exception")
        if exc is not None:
            print(f"[Asyncio] {msg}: {exc!r}")
        else:
            print(f"[Asyncio] {msg}")

    loop.set_exception_handler(_loop_exception_handler)

    print("[Startup] begin")
    try:
        t0 = time.perf_counter()
        await asyncio.wait_for(load_raids(), timeout=12.0)
        print(f"[Startup] load_raids ok ({time.perf_counter() - t0:.2f}s)")
    except asyncio.TimeoutError:
        print("[Startup] load_raids timeout (12s) - continue")
    except Exception as e:
        print(f"[Startup] load_raids error - continue: {e!r}")

    bot = MococoBot()
    print("[Startup] bot instance created")

    for ext in [
        'cogs.raid', 'cogs.tts', 'cogs.siblings', 'cogs.verify', 'cogs.friends', 'cogs.fixedraid',
        'cogs.etc', 'cogs.cal', 'cogs.subscription', 'cogs.search', 'cogs.quiz', 'cogs.enhance', 'cogs.stone', 'cogs.lucky', 'cogs.posts'
    ]:
        try:
            t0 = time.perf_counter()
            bot.load_extension(ext)
            print(f"[Startup] extension ok: {ext} ({time.perf_counter() - t0:.2f}s)")
        except Exception as e:
            print(f"[EXT] load failed: {ext} -> {e}")

    print("[Startup] calling bot.start")
    await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
