"""POE2DB 通用查询器 — Python 复刻 sergeyklay/poe2-mcp-server 的 poe2db.ts 逻辑。

从 poe2db.tw 抓取任意页面 HTML，解析 card-header 分区，返回结构化数据。
用作 Agent 的 Tool Use 工具，让 LLM 在推理链中查询装备、天赋、怪物等任意 POE2 数据。

核心功能：
  - fetch_poe2db_page(term, lang) → 抓取 HTML（带限速 15req/60s）
  - parse_poe2db_html(html) → 解析为分区字典
  - lookup(term, lang) → 一站式查询，返回结构化 JSON
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

BASE_URL = "https://poe2db.tw"

# ── 限速器：15 requests / 60 seconds ──────────────────────


class RateLimiter:
    """异步滑动窗口限速器。"""

    def __init__(self, max_requests: int = 15, window: float = 60.0):
        self.max_requests = max_requests
        self.window = window
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # 清理过期时间戳
            cutoff = now - self.window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self.max_requests:
                sleep_time = self._timestamps[0] - cutoff + 0.1
                logger.debug(f"Rate limit: sleeping {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
                # 递归重试
                await self.wait()
                return
            self._timestamps.append(now)


# 全局限速器实例
_limiter = RateLimiter()

# ── 页面缓存 ──────────────────────────────────────────────

# TTL 缓存：避免同一页面重复抓取（5 分钟过期）
_cache: dict[str, tuple[float, str]] = {}
_cache_ttl: float = 300.0  # 5 分钟


def _cache_key(term: str, lang: str) -> str:
    return f"{lang}:{_normalize_slug(term)}"


def _cache_get(term: str, lang: str) -> str | None:
    key = _cache_key(term, lang)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, html = entry
    if time.monotonic() - ts > _cache_ttl:
        del _cache[key]
        return None
    return html


def _cache_set(term: str, lang: str, html: str) -> None:
    _cache[_cache_key(term, lang)] = (time.monotonic(), html)


def cache_clear() -> int:
    """清空缓存，返回清除的条目数。"""
    count = len(_cache)
    _cache.clear()
    return count


# ── URL 规范化 ────────────────────────────────────────────

_ARABIC_TO_ROMAN = {
    "1": "I", "2": "II", "3": "III", "4": "IV", "5": "V",
    "6": "VI", "7": "VII", "8": "VIII", "9": "IX", "10": "X",
}


def _normalize_slug(term: str) -> str:
    """规范化 POE2DB URL slug。

    POE2DB 把尾部的阿拉伯数字转为罗马数字（如 "Herald of Ash 1" → "Herald_of_Ash_I"）。
    参考 MCP 服务器的 normalizeTrailingArabicToRoman 逻辑。
    """
    slug = term.replace(" ", "_")
    # 匹配尾部 "_数字" 或 " 数字"
    m = re.search(r"[_ ](\d+)$", slug)
    if m:
        num = m.group(1)
        if num in _ARABIC_TO_ROMAN:
            slug = slug[: m.start()] + "_" + _ARABIC_TO_ROMAN[num]
    return slug


# ── 页面抓取 ──────────────────────────────────────────────


async def fetch_poe2db_page(term: str, lang: str = "cn") -> str | None:
    """从 poe2db.tw 抓取页面 HTML。带 TTL 缓存。

    Args:
        term: 页面名称（如 "Herald of Ash"）
        lang: 语言代码（cn=中文, us=英文）

    Returns:
        HTML 字符串，404 时返回 None
    """
    cached = _cache_get(term, lang)
    if cached is not None:
        return cached

    await _limiter.wait()

    slug = _normalize_slug(term)
    url = f"{BASE_URL}/{lang}/{slug}"

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "POE2BD-Agent/1.0 (contact: dev@example.com)",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        r = await client.get(url)
        if r.status_code == 404:
            # 回退：用原始 slug（不转罗马数字）
            orig_slug = term.replace(" ", "_")
            if orig_slug != slug:
                await _limiter.wait()
                r2 = await client.get(f"{BASE_URL}/{lang}/{orig_slug}")
                if r2.status_code == 404:
                    return None
                return r2.text
            return None
        r.raise_for_status()
        _cache_set(term, lang, r.text)
        return r.text


# ── 批量查询 ──────────────────────────────────────────────


async def batch_lookup(
    terms: list[str],
    lang: str = "cn",
    concurrency: int = 5,
) -> list[dict[str, Any]]:
    """批量查询多个 POE2DB 页面（并发控制 + 缓存）。

    Args:
        terms: 查询词列表
        lang: 语言代码
        concurrency: 并发数

    Returns:
        与输入顺序对应的结果列表
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def lookup_one(term: str) -> dict[str, Any]:
        async with semaphore:
            return await lookup(term, lang)

    return await asyncio.gather(*[lookup_one(t) for t in terms])


# ── HTML 解析 ─────────────────────────────────────────────


def _card_sections(soup: BeautifulSoup) -> dict[str, str]:
    """按 card-header 分区解析页面主体内容。

    POE2DB 页面结构：多个 .card 区块，每个有 .card-header 标题 + .card-body 内容。
    返回 {section_name: body_html} 字典。
    """
    sections: dict[str, str] = {}

    # 找到主内容区
    main = soup.find("main") or soup.find("div", class_="container") or soup
    cards = main.find_all("div", class_="card") if main else []

    for card in cards:
        header = card.find(["div", "h2", "h3", "h4"], class_="card-header")
        body = card.find("div", class_="card-body")
        if header and body:
            name = header.get_text(strip=True).lower().replace(" ", "_")
            sections[name] = str(body)

    return sections


def _element_text(el: Tag | None) -> str:
    """安全获取元素文本。"""
    return el.get_text(" ", strip=True) if el else ""


def _table_to_dicts(table: Tag) -> list[dict[str, str]]:
    """HTML table → list[dict]，第一行作为表头。"""
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    result = []
    for row in rows[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
        result.append({headers[i]: cells[i] for i in range(min(len(headers), len(cells)))})
    return result


def parse_poe2db_html(html: str) -> dict[str, Any]:
    """解析 POE2DB 页面 HTML 为结构化数据。

    提取：标题、描述、属性表格、分区内容、链接列表。
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, Any] = {
        "found": True,
        "title": "",
        "description": "",
        "sections": {},
        "tables": [],
        "links": [],
    }

    # ── 标题 ──
    name_div = soup.find("div", class_="itemName")
    if name_div:
        lc = name_div.find("span", class_="lc")
        if lc:
            result["title"] = lc.get_text(strip=True)
    if not result["title"]:
        title_tag = soup.find("h1") or soup.find("title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

    # ── 描述 ──
    desc_div = soup.find("div", class_="secDescrText")
    if desc_div:
        result["description"] = _element_text(desc_div)

    # ── card 分区 ──
    result["sections"] = _card_sections(soup)

    # ── 表格 ──
    for table in soup.find_all("table"):
        t = _table_to_dicts(table)
        if t:
            result["tables"].append(t)

    # ── 链接 ──
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if text and href.startswith("/"):
            result["links"].append({
                "text": text,
                "href": f"{BASE_URL}{href}",
                "slug": href.split("/")[-1] if "/" in href else href,
            })

    return result


# ── Markdown 格式化（供 LLM 消费） ───────────────────────


def format_as_markdown(parsed: dict[str, Any], term: str, lang: str = "cn") -> str:
    """将解析结果格式化为 LLM 友好的 Markdown。

    参考 MCP 服务器输出格式：分区标题 + 结构化内容。
    """
    lines = [f"# {parsed.get('title', term)}", ""]

    if parsed.get("description"):
        lines.append(parsed["description"])
        lines.append("")

    # 表格
    for i, table in enumerate(parsed.get("tables", [])):
        if table:
            headers = list(table[0].keys())
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in table:
                lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
            lines.append("")

    # 分区内容（解析 HTML → 纯文本）
    for section_name, body_html in parsed.get("sections", {}).items():
        lines.append(f"## {section_name}")
        body_soup = BeautifulSoup(body_html, "lxml")
        # 提取表格
        for table in body_soup.find_all("table"):
            t = _table_to_dicts(table)
            if t:
                headers = list(t[0].keys())
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for row in t:
                    lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
                lines.append("")
        # 提取文本
        text = body_soup.get_text("\n", strip=True)
        if text:
            lines.append(text)
            lines.append("")

    # 相关链接
    if parsed.get("links"):
        lines.append("## Related Links")
        for link in parsed["links"][:20]:
            lines.append(f"- [{link['text']}]({link['href']})")
        lines.append("")

    return "\n".join(lines)


# ── 主查询接口 ─────────────────────────────────────────────


async def lookup(
    term: str,
    lang: str = "cn",
    format: str = "json",  # noqa: A002
) -> dict[str, Any]:
    """查询 POE2DB 任意页面，返回结构化数据。

    Args:
        term: 查询词（英文名，如 "Headhunter", "Passive Skill Tree"）
        lang: 语言（cn=中文, us=英文）
        format: 输出格式（json / markdown）

    Returns:
        {"found": True/False, "data": ...}
    """
    html = await fetch_poe2db_page(term, lang)
    if html is None:
        return {
            "found": False,
            "term": term,
            "lang": lang,
            "hint": f"POE2DB 上未找到 '{term}'，请检查名称拼写或尝试英文名",
        }

    parsed = parse_poe2db_html(html)

    if format == "markdown":
        md = format_as_markdown(parsed, term, lang)
        return {
            "found": True,
            "term": term,
            "lang": lang,
            "format": "markdown",
            "content": md,
        }

    # JSON 格式：返回精简结构
    return {
        "found": True,
        "term": term,
        "lang": lang,
        "title": parsed["title"],
        "description": parsed["description"],
        "sections": list(parsed["sections"].keys()),
        "table_count": len(parsed["tables"]),
        "tables": parsed["tables"],
        "related_links": [
            {"name": l["text"], "slug": l["slug"]}
            for l in parsed["links"][:20]
        ],
    }
