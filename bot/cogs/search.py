import asyncio
import re
import time
import urllib.parse
from typing import List, Optional, Tuple

import httpx
import discord
from discord.ext import commands
from lxml import html as LH

BASE_URL = "https://www.inven.co.kr/board/lostark/5355"
INVEN_ORIGIN = "https://www.inven.co.kr"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

HTTP_TIMEOUT = httpx.Timeout(connect=3.0, read=6.0, write=6.0, pool=6.0)
HTTP_LIMITS = httpx.Limits(max_connections=24, max_keepalive_connections=12, keepalive_expiry=30.0)

CONCURRENCY = 8
TOTAL_STEPS = 10
STEP_COUNT = TOTAL_STEPS - 1
MAX_RESULTS = 25

STERM_RX = re.compile(r"sterm=(\d+)")


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _progress_bar(step: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "▒" * width
    filled = int(step * width / total)
    filled = max(0, min(width, filled))
    return ("█" * filled) + ("▒" * (width - filled))


def _update_progress_embed(nick: str, current_step: int) -> discord.Embed:
    bar = _progress_bar(current_step, TOTAL_STEPS)
    desc = (
        f"`{nick}` 키워드로 검색중이에요! 잠시만 기다려 주세요.\n\n"
        f"최근 10만개의 게시글을 읽고있어요. 오래 걸릴 수 있으니 잠시만 기다려 주세요.\n\n"
        f"검색 중... {bar}"
    )
    e = discord.Embed(title="검색중", description=desc, color=0x7289DA)
    e.set_footer(text="Create by 조교병(카제로스)")
    return e


def _parse_list_and_sterm(html_text: str) -> Tuple[List[Tuple[str, str]], Optional[int]]:
    doc = LH.fromstring(html_text)
    items: List[Tuple[str, str]] = []

    rows = doc.xpath('//*[@id="new-board"]//table/tbody/tr[not(contains(@class,"notice"))]')
    for tr in rows:
        a = tr.xpath('.//td[contains(@class,"tit")]//a[1]')
        if not a:
            continue
        a = a[0]
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = urllib.parse.urljoin(INVEN_ORIGIN, href)

        title = "".join(a.xpath('.//text()[not(ancestor::span[contains(@class,"category")])]')).strip()
        if not title:
            continue
        cat = a.xpath('.//span[contains(@class,"category")]/text()')
        cat_text = cat[0].strip() if cat else ""
        display = (f"{cat_text} " if cat_text else "") + f"[{title}]({url})"
        items.append((display, url))

    next_sterm: Optional[int] = None
    for oc in doc.xpath('//*[@id="new-board"]//button[contains(@onclick,"sterm=")]/@onclick'):
        m = STERM_RX.search(oc or "")
        if m:
            next_sterm = int(m.group(1))
            break

    if next_sterm is None:
        for href in doc.xpath('//*[@id="new-board"]//a[contains(@href,"sterm=")]/@href'):
            m = STERM_RX.search(href or "")
            if m:
                next_sterm = int(m.group(1))
                break

    return items, next_sterm


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _render_plain_text(nick: str, page_url: str, results: List[str], flour_message: Optional[str]) -> str:
    if not results:
        return f"검색 결과 없음\n최근 10만개의 게시글을 검색했지만 결과가 없어요.\n\n상세 보기: {page_url}"
    lines = [f"상세 보기(Inven) - {nick}", f"상세 보기: {page_url}", ""]
    if flour_message:
        lines.append(flour_message)
        lines.append("")
    lines.extend(results[:MAX_RESULTS])
    out = "\n".join(lines)
    return _truncate(out, 1900)


async def _try_edit_original(ctx: discord.ApplicationContext, *, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> bool:
    try:
        await ctx.interaction.edit_original_response(content=content, embed=embed)
        return True
    except Exception:
        return False


async def _try_followup(ctx: discord.ApplicationContext, *, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> bool:
    try:
        await ctx.followup.send(content=content, embed=embed, ephemeral=True)
        return True
    except Exception:
        return False


async def _try_respond(ctx: discord.ApplicationContext, *, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> bool:
    if await _try_edit_original(ctx, content=content, embed=embed):
        return True
    return await _try_followup(ctx, content=content, embed=embed)


async def _try_dm(ctx: discord.ApplicationContext, content: str) -> bool:
    try:
        await ctx.author.send(_truncate(content, 1900))
        return True
    except Exception:
        return False


class SearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="검색", description="사사게 정보를 검색합니다.")
    @discord.option("닉네임", description="닉네임을 입력하세요.")
    async def accidents(self, ctx: discord.ApplicationContext, 닉네임: str):
        keyword_raw = (닉네임 or "").strip()
        if not keyword_raw:
            try:
                await ctx.respond("닉네임을 입력해주세요.", ephemeral=True)
            except Exception:
                pass
            return

        try:
            await ctx.response.defer(ephemeral=True)
        except Exception:
            pass

        can_embed = True
        try:
            if ctx.guild and ctx.channel and hasattr(ctx.channel, "permissions_for"):
                me = ctx.guild.me or ctx.guild.get_member(self.bot.user.id)
                if me:
                    perms = ctx.channel.permissions_for(me)
                    can_embed = bool(getattr(perms, "embed_links", True))
        except Exception:
            can_embed = True

        if can_embed:
            await _try_respond(ctx, embed=_update_progress_embed(keyword_raw, 1))
        else:
            await _try_respond(ctx, content=f"`{keyword_raw}` 키워드로 검색중이에요! 잠시만 기다려 주세요.")

        q = urllib.parse.quote_plus(keyword_raw)
        page_url = f"{BASE_URL}?name=subjcont&keyword={q}"

        results: List[str] = []
        flour_message: Optional[str] = None
        seen_urls: set[str] = set()
        lock = asyncio.Lock()
        stop_event = asyncio.Event()

        async def add_items(items: List[Tuple[str, str]]):
            nonlocal flour_message
            async with lock:
                for display, url in items:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    results.append(_truncate(display, 1024))
                    if len(results) >= MAX_RESULTS:
                        flour_message = "최대 내용 25개를 초과하여 검색을 멈췄어요!"
                        stop_event.set()
                        break

        done_steps = 1
        last_update = 0.0

        async def bump_progress():
            nonlocal done_steps, last_update
            async with lock:
                done_steps += 1
                step = min(done_steps, TOTAL_STEPS)
            now = time.monotonic()
            if now - last_update < 0.8 and step < TOTAL_STEPS and not stop_event.is_set():
                return
            last_update = now
            if can_embed:
                await _try_respond(ctx, embed=_update_progress_embed(keyword_raw, step))
            else:
                await _try_respond(ctx, content=f"검색 중... ({step}/{TOTAL_STEPS})")

        async with httpx.AsyncClient(
            http2=True,
            headers=HEADERS,
            limits=HTTP_LIMITS,
            timeout=HTTP_TIMEOUT,
            trust_env=False,
        ) as client:
            try:
                first_html = await _fetch(client, page_url)
            except Exception as e:
                msg = f"`{keyword_raw}` 키워드로 검색중 오류가 발생했어요.\n\n{e}"
                if can_embed and await _try_respond(ctx, embed=discord.Embed(title="검색 오류", description=_truncate(msg, 4096), color=0xF1C40F)):
                    return
                if await _try_respond(ctx, content=_truncate(msg, 1900)):
                    return
                await _try_dm(ctx, msg)
                return

            page_items, next_sterm = _parse_list_and_sterm(first_html)
            await add_items(page_items)

            if stop_event.is_set() or next_sterm is None:
                embed = self._build_final_embed(keyword_raw, page_url, results, flour_message)
                if can_embed and await _try_respond(ctx, embed=embed):
                    return
                text = _render_plain_text(keyword_raw, page_url, results, flour_message)
                if await _try_respond(ctx, content=text):
                    return
                await _try_dm(ctx, text)
                return

            urls = [f"{BASE_URL}?name=subjcont&keyword={q}&sterm={next_sterm + i * 10000}" for i in range(STEP_COUNT)]
            sem = asyncio.Semaphore(CONCURRENCY)

            async def worker(u: str):
                if stop_event.is_set():
                    return
                async with sem:
                    if stop_event.is_set():
                        return
                    try:
                        h = await _fetch(client, u)
                    except Exception:
                        await bump_progress()
                        return
                    items, _ = _parse_list_and_sterm(h)
                    await add_items(items)
                    await bump_progress()

            tasks = [asyncio.create_task(worker(u)) for u in urls]
            for coro in asyncio.as_completed(tasks):
                await coro
                if stop_event.is_set():
                    break
            if stop_event.is_set():
                for t in tasks:
                    if not t.done():
                        t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        embed = self._build_final_embed(keyword_raw, page_url, results, flour_message)
        if can_embed and await _try_respond(ctx, embed=embed):
            return
        text = _render_plain_text(keyword_raw, page_url, results, flour_message)
        if await _try_respond(ctx, content=text):
            return
        await _try_dm(ctx, text)

    def _build_final_embed(self, 닉네임: str, page_url: str, results: List[str], flour_message: Optional[str]) -> discord.Embed:
        if not results:
            embed = discord.Embed(title="검색 결과 없음", description="최근 10만개의 게시글을 검색했지만 결과가 없어요.", color=0xFF0000)
        else:
            embed = discord.Embed(
                title=f"상세 보기(Inven) - {닉네임}",
                url=page_url,
                description="- 사사게 검색 정보 (최근 10만개 게시글을 가져왔어요!)",
                color=0x2ECC71,
            )
            for result in results[:MAX_RESULTS]:
                embed.add_field(name="\u200B", value=_truncate(result, 1024), inline=False)
        embed.set_author(name=f"사사게 키워드 '{닉네임}'으로 검색한 결과입니다.")
        footer = "Create by 조교병(카제로스)"
        if flour_message:
            footer = f"{flour_message}\n{footer}"
        embed.set_footer(text=_truncate(footer, 2048))
        return embed


def setup(bot: discord.AutoShardedBot):
    bot.add_cog(SearchCog(bot))
