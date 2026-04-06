import asyncio
import contextlib
import time
from datetime import datetime
from io import BytesIO

import discord
from discord.ext import commands

import httpx
from core.config import LOSTARK_API_SUB1_KEY
from core.http_client import http_client

ENGRAVINGS = [
    "원한", "아드레날린", "기습의 대가", "타격의 대가", "돌격대장",
    "예리한 둔기", "저주받은 인형", "결투의 대가", "각성", "마나의 흐름",
    "구슬동자", "전문의", "중갑 착용", "마나 효율 증가", "안정된 상태",
    "슈퍼 차지", "속전속결", "급소 타격", "정기 흡수", "질량 증가"
]

ALLOWED_USER_IDS = {
    1119944077861990420,
    435029048473944066,
    325868686667939840,
    308895278189117440,
    417217005960036362,
    921412034099314799
}

PROGRESS_STATUSES = [
    "모험가님의 정보를 수집중이에요\n완료가 될때까지 기다려주세요.",
    "이미지로 변환할 예정이에요 잠시만 기다려주세요.\n완료가 될때까지 기다려주세요.",
    "Mococo Bot이 정보를 배치하고 있어요.\n완료가 될때까지 기다려주세요.",
    "곧 완료될거에요 잠시만 더 기다려주세요.\n완료가 될때까지 기다려주세요.",
    "서버가 해외에 있어서 더욱 느릴 수 있어요 조금만 더 기다려주세요.\n완료가 될때까지 기다려주세요.",
]


def _is_response_done(resp) -> bool:
    fn = getattr(resp, "is_done", None)
    if callable(fn):
        with contextlib.suppress(Exception):
            return bool(fn())
    return bool(getattr(resp, "is_done", False))


async def _safe_interaction_send(interaction: discord.Interaction, *, content: str = None, embed=None, ephemeral: bool = True):
    try:
        if _is_response_done(interaction.response):
            return await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        return await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
    except Exception:
        with contextlib.suppress(Exception):
            return await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)


async def _safe_ctx_send(ctx: discord.ApplicationContext, *, content: str = None, embed=None, ephemeral: bool = True):
    try:
        if getattr(ctx, "interaction", None) and _is_response_done(ctx.interaction.response):
            return await ctx.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        return await ctx.respond(content=content, embed=embed, ephemeral=ephemeral)
    except Exception:
        with contextlib.suppress(Exception):
            return await ctx.followup.send(content=content, embed=embed, ephemeral=ephemeral)


class MessageModal(discord.ui.Modal):
    def __init__(self, channel: discord.abc.Messageable):
        super().__init__(title="메세지 보내기")
        self.channel = channel
        self.add_item(
            discord.ui.InputText(
                label="보낼 텍스트",
                style=discord.InputTextStyle.paragraph,
                max_length=2000,
                required=True,
                placeholder="여기에 전송할 내용을 입력하세요."
            )
        )

    async def callback(self, interaction: discord.Interaction):
        content = (self.children[0].value or "").strip()
        if not content:
            return await _safe_interaction_send(interaction, content="❌ 전송할 내용이 비어있어요.", ephemeral=True)

        if interaction.user.id not in ALLOWED_USER_IDS:
            return await _safe_interaction_send(interaction, content="❌ 이 명령을 사용할 권한이 없어요.", ephemeral=True)

        try:
            await self.channel.send(content)
            return await _safe_interaction_send(interaction, content="✅ 보냈어요.", ephemeral=True)
        except discord.Forbidden:
            return await _safe_interaction_send(interaction, content="❌ 봇이 이 채널에 메시지를 보낼 권한이 없어요.", ephemeral=True)
        except Exception as e:
            return await _safe_interaction_send(interaction, content=f"❌ 오류: {e}", ephemeral=True)


class ETCCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _start_progress(self, ctx: discord.ApplicationContext, nickname: str):
        title = f"캐릭터 : {nickname}"
        desc = (
            f"<:golden_mokoko:1404729935116898304> 현재 로스트아크에서 {nickname} 정보를 찾고 있어요.\n"
            "완료가 될때까지 기다려주세요."
        )
        embed = discord.Embed(title=title, description=desc, color=discord.Color.green())
        embed.set_footer(text="요청 시각")
        await _safe_ctx_send(ctx, embed=embed, ephemeral=False)
        with contextlib.suppress(Exception):
            return embed, await ctx.interaction.original_response()
        return embed, None

    async def _rotate_status(self, msg: discord.Message, embed: discord.Embed, task: asyncio.Task):
        i = 0
        try:
            while not task.done():
                await asyncio.sleep(10)
                if task.done():
                    break
                embed.description = f"<:golden_mokoko:1404729935116898304> {PROGRESS_STATUSES[i % len(PROGRESS_STATUSES)]}"
                embed.set_footer(text="진행 중…")
                with contextlib.suppress(Exception):
                    await msg.edit(embed=embed)
                i += 1
        except asyncio.CancelledError:
            pass

    async def _render_profile(self, ctx: discord.ApplicationContext, nickname: str, *, endpoint: str, params: dict, success_text: str):
        embed, status_msg = await self._start_progress(ctx, nickname)
        if status_msg is None:
            return

        async def fetch():
            timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0)
            return await http_client.get(endpoint, params=params, timeout=timeout)

        render_task = asyncio.create_task(fetch())
        rot_task = asyncio.create_task(self._rotate_status(status_msg, embed, render_task))

        try:
            resp = await render_task
        finally:
            rot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await rot_task

        try:
            if resp.status_code != 200:
                embed.color = discord.Color.red()
                embed.description = f"❌ `{nickname}` 해당 닉네임 조회가 되지않아요."
                embed.set_footer(text="오류")
                with contextlib.suppress(Exception):
                    await status_msg.edit(embed=embed)
                return

            ctype = (resp.headers.get("content-type", "") or "").lower()
            if "image" not in ctype:
                with contextlib.suppress(Exception):
                    detail = resp.json()
                if "detail" in locals():
                    detail_text = str(detail)[:400]
                else:
                    detail_text = (resp.text or "")[:400]
                embed.color = discord.Color.red()
                embed.description = f"❌ `{nickname}` 조회 중 오류가 발생했어요.\n```{detail_text}```"
                embed.set_footer(text="오류")
                with contextlib.suppress(Exception):
                    await status_msg.edit(embed=embed)
                return

            buf = BytesIO(resp.content)
            buf.seek(0)
            filename = f"{nickname}_profile.png"

            try:
                await status_msg.edit(content=success_text, embed=None, file=discord.File(buf, filename=filename))
            except discord.Forbidden:
                embed.color = discord.Color.red()
                embed.description = (
                    "❌ 이미지를 전송할 권한이 없어요.\n"
                    "봇 권한에서 `파일 첨부(Attach Files)`와 `임베드 링크(Embed Links)`를 확인해주세요."
                )
                embed.set_footer(text="권한 부족")
                with contextlib.suppress(Exception):
                    await status_msg.edit(embed=embed)
            except Exception as e:
                embed.color = discord.Color.red()
                embed.description = f"❌ 예기치 못한 오류가 발생했어요.\n```{e}```"
                embed.set_footer(text="오류")
                with contextlib.suppress(Exception):
                    await status_msg.edit(embed=embed)
        except Exception as e:
            embed.color = discord.Color.red()
            embed.description = f"❌ 예기치 못한 오류가 발생했어요.\n```{e}```"
            embed.set_footer(text="오류")
            with contextlib.suppress(Exception):
                await status_msg.edit(embed=embed)

    @discord.slash_command(name="메세지", description="Mococobot 개발진 전용 명령어 입니다.")
    async def send_plain_message(self, ctx: discord.ApplicationContext):
        if ctx.user.id not in ALLOWED_USER_IDS:
            return await _safe_ctx_send(ctx, content="❌ 이 명령을 사용할 권한이 없어요.", ephemeral=True)
        modal = MessageModal(channel=ctx.channel)
        with contextlib.suppress(Exception):
            return await ctx.send_modal(modal)
        await _safe_ctx_send(ctx, content="❌ 모달을 열 수 없어요. 잠시 후 다시 시도해주세요.", ephemeral=True)

    @discord.slash_command(name="정보", description="닉네임으로 캐릭터 이미지를 생성해 보여줘요.")
    @discord.option("닉네임", description="검색할 캐릭터 닉네임을 입력하세요.", type=str)
    async def character_info(self, ctx: discord.ApplicationContext, 닉네임: str):
        await self._render_profile(
            ctx,
            닉네임,
            endpoint="/render/profile",
            params={"nickname": 닉네임, "user_id": str(ctx.user.id)},
            success_text=f"**{닉네임}**님의 정보예요!"
        )

    @discord.slash_command(name="캐릭터카드", description="닉네임으로 캐릭터카드 이미지를 생성해 보여줘요.")
    @discord.option("닉네임", description="검색할 캐릭터 닉네임을 입력하세요.", type=str)
    async def character_info_mini_card(self, ctx: discord.ApplicationContext, 닉네임: str):
        await self._render_profile(
            ctx,
            닉네임,
            endpoint="/render/mini-profile",
            params={"nickname": 닉네임},
            success_text=f"**{닉네임}**님의 캐릭터 카드예요!"
        )

    @discord.slash_command(name="도움말", description="모코코 봇 사용법과 현재 상태(핑/업타임 등)를 보여줘요.")
    @discord.option("공개", description="모두에게 보일까요? (기본: 비공개)", type=bool, required=False, default=False)
    async def help_cmd(self, ctx: discord.ApplicationContext, 공개: bool = False):
        await ctx.defer(ephemeral=(not 공개))
        gw_ms = int(self.bot.latency * 1000)

        rest_ms = None
        try:
            t0 = time.perf_counter()
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get("https://discord.com/api/v10/gateway")
            rest_ms = int((time.perf_counter() - t0) * 1000)
        except Exception:
            pass

        shard_lines = []
        if getattr(self.bot, "shards", None):
            for sid, shard in sorted(self.bot.shards.items(), key=lambda kv: kv[0]):
                shard_lines.append(f"`#{sid}` {int(shard.latency * 1000)}ms")
        shard_text = " / ".join(shard_lines) if shard_lines else "단일 게이트웨이"

        guild_count = len(self.bot.guilds)

        now_ts = time.time()
        started_ts = getattr(self.bot, "start_time_ts", None)

        def fmt_uptime(sec: int) -> str:
            d, rem = divmod(sec, 86400)
            h, rem = divmod(rem, 3600)
            m, s = divmod(rem, 60)
            parts = []
            if d:
                parts.append(f"{d}일")
            if d or h:
                parts.append(f"{h}시간")
            if d or h or m:
                parts.append(f"{m}분")
            parts.append(f"{s}초")
            return " ".join(parts)

        uptime = fmt_uptime(int(now_ts - started_ts)) if started_ts else "알 수 없음"

        health = None
        try:
            timeout = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
            resp = await http_client.get("/health", timeout=timeout)
            if resp.status_code == 200:
                health = resp.json()
        except Exception:
            health = None

        desc = (
            "모코코 봇은 레이드 일정/모집, 인증, 스티커, TTS 등 서버 운영을 도와요.\n"
            "아래 주요 명령으로 바로 시작해 보세요!"
        )
        embed = discord.Embed(title="📖 모코코 도움말", description=desc, color=discord.Color.blurple())

        embed.add_field(
            name="🚀 시작하기",
            value=(
                "• `/setups` 레이드 모집용 채널 자동 생성\n"
                "• `/서버설정` 레이드 모집 관련 설정\n"
                "• `/레이드` 일정 생성 / `/모집` 모집글 생성\n"
                "• `/인증` 인증 시스템 설정\n"
                "• `/tts` TTS 채널 설정"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔗 웹 가이드",
            value=(
                "• [간단 사용 가이드](https://mococobot.kr/start)\n"
                "• [전체 명령어·설정 문서](https://mococobot.kr/docs)"
            ),
            inline=False,
        )

        status_lines = [
            f"• **Gateway**: `{gw_ms}ms`",
            f"• **REST**: `{rest_ms}ms`" if rest_ms is not None else "• **REST**: 측정 실패",
            f"• **Shards**: {len(getattr(self.bot, 'shards', []) or [0])} ({shard_text})",
            f"• **서버 수**: {guild_count:,}",
            f"• **업타임**: {uptime}",
        ]
        embed.add_field(name="📡 상태", value="\n".join(status_lines), inline=False)

        if health:
            hs = health
            h_uptime = fmt_uptime(int(hs.get("uptime_seconds", 0)))
            cpu_p = hs.get("cpu", {}).get("percent")
            mem_p = hs.get("memory", {}).get("percent")
            disk_p = hs.get("disk", {}).get("percent")
            render = hs.get("render_server", {}) or {}
            r_status = render.get("status")
            r_ping = render.get("ping_ms")

            render_line = None
            if r_ping is not None:
                if r_status == 404:
                    render_line = f"• 렌더: `{int(r_ping)}ms` (정상)"
                else:
                    render_line = f"• 렌더: `{int(r_ping)}ms` (HTTP {r_status})" if r_status else f"• 렌더: `{int(r_ping)}ms`"

            runtime = hs.get("runtime", {}) or {}
            proc = hs.get("process", {}) or {}

            backend_lines = [
                f"• 상태: `{hs.get('status', 'unknown')}`",
                f"• 업타임: {h_uptime}",
                f"• CPU: `{cpu_p}%` • 메모리: `{mem_p}%` • 디스크: `{disk_p}%`" if None not in (cpu_p, mem_p, disk_p) else None,
                f"• 프로세스: PID `{proc.get('pid','?')}` • 스레드 `{proc.get('num_threads','?')}` • asyncio `{runtime.get('asyncio_tasks','?')}`",
                render_line,
            ]
            backend_text = "\n".join([ln for ln in backend_lines if ln])
            embed.add_field(name="🖥️ 백엔드 서버", value=backend_text, inline=False)
        else:
            embed.add_field(name="🖥️ 백엔드 서버", value="• 상태 정보를 불러오지 못했어요.", inline=False)

        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="필요한 명령이 더 있다면 언제든 말해주세요!")

        await ctx.followup.send(embed=embed, ephemeral=(not 공개))

    @discord.slash_command(name="청소", description="현재 채널의 채팅을 삭제해요. (관리자 전용)")
    async def clean_thread(self, ctx: discord.ApplicationContext, count: discord.Option(int, description="삭제할 개수 | 비워두면 전체 삭제", required=False, min_value=1) = None):
        if not getattr(ctx, "guild", None): return await _safe_ctx_send(ctx, content="❌ 이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)

        channel = ctx.channel
        if isinstance(channel, discord.Thread): target_label = "일정 내 채팅"
        elif isinstance(channel, discord.TextChannel): target_label = "일반 채널 내 채팅"
        else: return await _safe_ctx_send(ctx, content="❌ 이 명령어는 일반 채널 또는 일정 채팅방(스레드)에서만 사용할 수 있어요.", ephemeral=True)

        if not ctx.author.guild_permissions.administrator: return await _safe_ctx_send(ctx, content="❌ 서버 관리자만 이 명령어를 사용할 수 있어요.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        if isinstance(channel, discord.Thread) and getattr(channel, "archived", False):
            with contextlib.suppress(discord.Forbidden, discord.HTTPException): await channel.edit(archived=False)

        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        targets = []
        recent_msgs = []
        old_msgs = []

        try:
            async for msg in channel.history(limit=None):
                if isinstance(channel, discord.Thread) and msg.id == channel.id: continue
                targets.append(msg)
                if msg.created_at >= cutoff: recent_msgs.append(msg)
                else: old_msgs.append(msg)
                if count is not None and len(targets) >= count: break
        except discord.Forbidden:
            return await ctx.followup.send("❌ 봇에 `메시지 관리` 또는 `메시지 기록 보기` 권한이 필요해요.", ephemeral=True)

        if not targets:
            if count is None: return await ctx.followup.send(f"🧹 삭제할 {target_label}이 없어요.", ephemeral=True)
            return await ctx.followup.send(f"🧹 삭제할 {target_label}이 없어요.", ephemeral=True)

        deleted = 0
        permission_blocked = False

        for i in range(0, len(recent_msgs), 100):
            chunk = recent_msgs[i:i + 100]
            if not chunk or permission_blocked: break
            try:
                await channel.delete_messages(chunk)
                deleted += len(chunk)
            except discord.Forbidden:
                permission_blocked = True
                break
            except discord.HTTPException:
                for msg in chunk:
                    try:
                        await msg.delete()
                        deleted += 1
                        await asyncio.sleep(0.2)
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        permission_blocked = True
                        break
                    except discord.HTTPException:
                        pass
            if i + 100 < len(recent_msgs) and not permission_blocked: await asyncio.sleep(0.7)

        if not permission_blocked:
            for idx, msg in enumerate(old_msgs, 1):
                try:
                    await msg.delete()
                    deleted += 1
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    permission_blocked = True
                    break
                except discord.HTTPException:
                    pass
                if idx % 10 == 0 and idx < len(old_msgs): await asyncio.sleep(1.0)

        if permission_blocked and deleted == 0:
            return await ctx.followup.send("❌ 봇에 `메시지 관리` 또는 `메시지 기록 보기` 권한이 필요해요.", ephemeral=True)

        if permission_blocked:
            return await ctx.followup.send(f"🧹 {deleted}개 삭제했어요. (중간에 권한 부족으로 중단)", ephemeral=True)

        if count is None:
            return await ctx.followup.send(f"🧹 {target_label} {deleted}개를 삭제했어요!", ephemeral=True)

        await ctx.followup.send(f"🧹 {target_label} 최근 채팅 {deleted}개를 삭제했어요!", ephemeral=True)

    @discord.slash_command(name="로펙", description="로펙 점수를 확인해요.")
    @discord.option("닉네임", description="검색할 닉네임을 입력해주세요.", type=str)
    async def lopec(self, ctx: discord.ApplicationContext, 닉네임: str):
        await ctx.defer(ephemeral=True)
        try:
            async with httpx.AsyncClient() as client:
                url = f"https://lopec-api.tassardar6-c0f.workers.dev/{닉네임}"
                response = await client.get(url, timeout=10.0)
            data = response.json()

            if "error" in data:
                return await ctx.followup.send(f"❌ `{닉네임}` 캐릭터의 로펙 정보를 찾을 수 없어요.", ephemeral=True)

            result = data["result"]
            embed = discord.Embed(
                title=f"{result['nickname']}님의 정보",
                description=(
                    f"**직업:** {result.get('Class', '알 수 없음')}\n"
                    f"[바로가기]({result['thumbnailUrl']})"
                ),
                color=discord.Color.gold()
            )
            embed.add_field(
                name="<:lopec:1407281473656193024> 로펙 점수",
                value=f"**{result['specPoint']:.2f}점**",
                inline=False
            )
            embed.set_footer(text="점수 갱신은 로펙 사이트에 방문해주세요.")

            await ctx.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await ctx.followup.send(f"❌ 오류가 발생했습니다: {str(e)}", ephemeral=True)

    @discord.slash_command(name="각인서", description="거래소에 등록된 각인서의 가격을 조회합니다.")
    @discord.option("이름", description="각인서 이름을 입력하세요.", type=str, choices=ENGRAVINGS)
    async def engraving_price(self, ctx: discord.ApplicationContext, 이름: str):
        await ctx.defer(ephemeral=True)

        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "accept": "application/json",
                    "authorization": f"bearer {LOSTARK_API_SUB1_KEY}",
                    "Content-Type": "application/json"
                }

                payload = {
                    "CategoryCode": 40000,
                    "ItemGrade": "유물",
                    "ItemName": 이름,
                    "SortCondition": "ASC"
                }

                response = await client.post(
                    "https://developer-lostark.game.onstove.com/markets/items",
                    headers=headers,
                    json=payload,
                    timeout=10.0
                )

            if response.status_code != 200:
                return await ctx.followup.send(f"❌ API 오류가 발생했습니다. (상태 코드: {response.status_code})", ephemeral=True)

            data = response.json()

            if not data.get("Items"):
                return await ctx.followup.send(f"❌ '{이름}' 각인서를 거래소에서 찾을 수 없어요.", ephemeral=True)

            item = data["Items"][0]
            embed = discord.Embed(
                title=f"📊 {item['Name']} 가격 정보",
                color=self._get_grade_color(item.get("Grade", "일반"))
            )

            if item.get("Icon"):
                embed.set_thumbnail(url=item["Icon"])

            embed.add_field(
                name="📦 기본 정보",
                value=f"**등급**: {item.get('Grade', 'N/A')}\n**묶음 개수**: {item.get('BundleCount', 1)}개",
                inline=True
            )

            current_price = item.get("CurrentMinPrice")
            recent_price = item.get("RecentPrice")
            avg_price = item.get("YDayAvgPrice")

            price_text = ""
            if current_price:
                price_text += f"**현재 최저가**: `{current_price:,}🪙`\n"
            if recent_price:
                price_text += f"**최근 거래가**: `{recent_price:,}🪙`\n"
            if avg_price:
                price_text += f"**어제 평균가**: `{avg_price:,.0f}🪙`"

            if price_text:
                embed.add_field(name="💰 가격 정보", value=price_text, inline=False)

            if current_price and avg_price:
                change_percent = ((current_price - avg_price) / avg_price) * 100
                if change_percent > 5:
                    trend = f"📈 평균 대비 **+{change_percent:.1f}%** (상승)"
                    embed.color = discord.Color.red()
                elif change_percent < -5:
                    trend = f"📉 평균 대비 **{change_percent:.1f}%** (하락)"
                    embed.color = discord.Color.green()
                else:
                    trend = f"📊 평균 대비 **{change_percent:+.1f}%** (보합)"

                embed.add_field(name="📈 가격 동향", value=trend, inline=False)

            embed.set_footer(
                text=f"아이템 ID: {item.get('Id', 'N/A')} • 조회 시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

            await ctx.followup.send(embed=embed, ephemeral=True)

        except httpx.TimeoutException:
            await ctx.followup.send("❌ 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
        except httpx.HTTPError as e:
            await ctx.followup.send(f"❌ 네트워크 오류가 발생했습니다: {str(e)}", ephemeral=True)
        except Exception as e:
            await ctx.followup.send(f"❌ 예상치 못한 오류가 발생했습니다: {str(e)}", ephemeral=True)

    def _get_grade_color(self, grade: str) -> discord.Color:
        grade_colors = {
            "일반": discord.Color.light_grey(),
            "고급": discord.Color.green(),
            "희귀": discord.Color.blue(),
            "영웅": discord.Color.purple(),
            "전설": discord.Color.orange(),
            "유물": discord.Color.red(),
            "고대": discord.Color.gold()
        }
        return grade_colors.get(grade, discord.Color.default())

    @discord.slash_command(name="경매", description="경매 입찰 적정가를 알려줘요.")
    @discord.option("금액", description="금액을 입력하세요.", type=int)
    async def auction_price(self, ctx: discord.ApplicationContext, 금액: int):
        AUCTION_FEE_RATE = 0.05
        MARKET_FEE_RATE = 0.05
        MIN_RAISE_FACTOR = 1.10

        PARTY_SIZES = {"4인": 4, "8인": 8, "16인": 16}

        def break_even_use(M: int, N: int) -> int:
            return int(M * (N - 1) / (N - AUCTION_FEE_RATE))

        def break_even_flip(M: int, N: int) -> int:
            return int((1 - MARKET_FEE_RATE) * M * (N - 1) / (N - AUCTION_FEE_RATE))

        def first_bid_reco(break_even: int) -> int:
            return max(1, int(break_even / MIN_RAISE_FACTOR))

        use_bes = {k: break_even_use(금액, N) for k, N in PARTY_SIZES.items()}
        flip_bes = {k: break_even_flip(금액, N) for k, N in PARTY_SIZES.items()}
        first_bids = {k: first_bid_reco(flip_bes[k]) for k in PARTY_SIZES.keys()}

        def fmt(n: int) -> str:
            return f"{n:,}🪙"

        embed = discord.Embed(
            title="💰 경매 입찰 최적가",
            description=f"### 기준 금액(시장가/가치): `{금액:,}🪙`",
            color=discord.Color.gold()
        )

        embed.add_field(
            name=":ballot_box_with_check: 손익분기점 — 직접 사용",
            value="\n".join([
                f"**4인**: `{fmt(use_bes['4인'])}`",
                f"**8인**: `{fmt(use_bes['8인'])}`",
                f"**16인**: `{fmt(use_bes['16인'])}`",
            ]),
            inline=False
        )

        embed.add_field(
            name=":ballot_box_with_check: 손익분기점 — 되팔이(거래소 5%)",
            value="\n".join([
                f"**4인**: `{fmt(flip_bes['4인'])}`",
                f"**8인**: `{fmt(flip_bes['8인'])}`",
                f"**16인**: `{fmt(flip_bes['16인'])}`",
            ]),
            inline=False
        )

        embed.add_field(
            name=":dart: 첫 입찰 추천가 — 되팔이 기준 (다음 입찰 +10% 가정)",
            value="\n".join([
                f"**4인**: `{fmt(first_bids['4인'])}`",
                f"**8인**: `{fmt(first_bids['8인'])}`",
                f"**16인**: `{fmt(first_bids['16인'])}`",
            ]),
            inline=False
        )

        embed.set_footer(text="참고: 전리품 경매는 최근 7일 평균 낙찰가의 500% 상한이 적용됩니다. 상한 도달 시 더 이상 상회입찰 불가.")
        await ctx.respond(embed=embed, ephemeral=True)

    @discord.slash_command(name="유저정보", description="Discord 유저 정보를 조회합니다", guild_ids=[1227993158596694139])
    @discord.option("유저아이디", description="조회할 유저의 ID를 입력하세요 (예: 123456789012345678)", type=str)
    async def user_info(self, ctx: discord.ApplicationContext, 유저아이디: str):
        await ctx.defer(ephemeral=True)

        try:
            if not 유저아이디.isdigit():
                return await ctx.followup.send("❌ 유효한 유저 ID가 아닙니다. 숫자로만 이루어진 ID를 입력해주세요.", ephemeral=True)

            user_id = int(유저아이디)

            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                return await ctx.followup.send(f"❌ ID: {user_id}인 유저를 찾을 수 없습니다.", ephemeral=True)
            except discord.HTTPException:
                return await ctx.followup.send("❌ Discord API 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)

            member = None
            if ctx.guild:
                with contextlib.suppress(discord.NotFound, discord.HTTPException, discord.Forbidden):
                    member = await ctx.guild.fetch_member(user_id)

            embed = discord.Embed(
                title=f"👤 유저 정보: {user.name}",
                description=f"**ID**: `{user.id}`",
                color=discord.Color.blue() if not member else member.color
            )

            created_at = int(user.created_at.timestamp())
            embed.add_field(
                name="📅 계정 생성일",
                value=f"<t:{created_at}:F>\n(<t:{created_at}:R>)",
                inline=False
            )

            embed.add_field(name="🤖 봇 여부", value="예" if user.bot else "아니오", inline=True)
            embed.add_field(name="🚫 시스템 계정", value="예" if user.system else "아니오", inline=True)

            if member:
                joined_at = int(member.joined_at.timestamp()) if member.joined_at else None
                if joined_at:
                    embed.add_field(
                        name="📥 서버 가입일",
                        value=f"<t:{joined_at}:F>\n(<t:{joined_at}:R>)",
                        inline=False
                    )

                if member.nick:
                    embed.add_field(name="📝 서버 닉네임", value=member.nick, inline=True)

                if member.premium_since:
                    boosting_since = int(member.premium_since.timestamp())
                    embed.add_field(
                        name="✨ 부스트 시작일",
                        value=f"<t:{boosting_since}:F>\n(<t:{boosting_since}:R>)",
                        inline=False
                    )

                if len(member.roles) > 1:
                    role_mentions = [role.mention for role in member.roles[1:]]
                    roles_str = ", ".join(role_mentions)
                    if len(roles_str) > 1024:
                        roles_str = ", ".join(role_mentions[:10]) + f" ... 외 {len(role_mentions) - 10}개"
                    embed.add_field(name=f"🏷️ 역할 ({len(member.roles) - 1})", value=roles_str, inline=False)

                status_emojis = {
                    discord.Status.online: "🟢",
                    discord.Status.idle: "🟡",
                    discord.Status.dnd: "🔴",
                    discord.Status.offline: "⚫"
                }
                status_text = {
                    discord.Status.online: "온라인",
                    discord.Status.idle: "자리비움",
                    discord.Status.dnd: "방해금지",
                    discord.Status.offline: "오프라인"
                }

                if hasattr(member, "status"):
                    emoji = status_emojis.get(member.status, "⚫")
                    text = status_text.get(member.status, "알 수 없음")
                    embed.add_field(name="🔵 상태", value=f"{emoji} {text}", inline=True)
            else:
                embed.add_field(name="📌 서버 멤버십", value="이 서버의 멤버가 아닙니다", inline=False)

            embed.set_thumbnail(url=user.display_avatar.url)
            if user.banner:
                embed.set_image(url=user.banner.url)

            await ctx.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await ctx.followup.send(f"❌ 오류가 발생했습니다: {str(e)}", ephemeral=True)


def setup(bot: discord.AutoShardedBot):
    bot.add_cog(ETCCog(bot))
