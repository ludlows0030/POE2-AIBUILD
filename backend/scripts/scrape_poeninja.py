"""POE2 BD 数据采集 — 从 poe.ninja 抓取真实玩家 BD 数据。

数据源: https://poe.ninja/poe2/builds/{league}
数据格式: protobuf API + 渲染后的 DOM

用法:
    cd backend && python scripts/scrape_poeninja.py
    cd backend && python scripts/scrape_poeninja.py --league vaal --limit 50
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "builds"

# poe.ninja POE2 已知赛季列表
KNOWN_LEAGUES = [
    "vaal",          # Fate of the Vaal (0.4)
    "vaalhc",        # HC Fate of the Vaal
    "vaalssf",       # SSF Fate of the Vaal
    "vaalhcssf",     # HC SSF Fate of the Vaal
    "standard",      # Standard
]


async def _wait_for_build_data(page, max_wait: int = 30) -> bool:
    """等待 React app 加载完成，返回是否成功。

    页面加载后 React 会注入角色名称、升华职业等文本。
    检测到 DPS / Level 等关键文本即认为加载成功。
    """
    for _ in range(max_wait):
        await asyncio.sleep(1)
        try:
            text = await page.inner_text("body")
            if any(kw in text for kw in ["Level", "DPS", "Pathfinder", "Titan",
                                           "Stormweaver", "Deadeye", "Invoker"]):
                return True
        except Exception:
            pass
    return False


async def scrape_league_builds(
    league: str = "vaal",
    limit: int = 0,
    max_retries: int = 5,
) -> list[dict[str, Any]]:
    """抓取指定赛季的全部 BD 数据。

    Args:
        league: 赛季 URL slug (vaal, standard 等)
        limit: 0 = 全部，>0 = 限制条数
        max_retries: 最大重试次数（React 加载不稳定）

    Returns:
        BD 数据列表 [{name, class, ascendancy, level, dps, ...}, ...]
    """
    from playwright.async_api import async_playwright

    url = f"https://poe.ninja/poe2/builds/{league}"

    for attempt in range(max_retries):
        logger.info(f"Loading {url} (attempt {attempt+1}/{max_retries})")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                try:
                    await page.goto(url, wait_until="load", timeout=45000)
                except Exception:
                    pass  # 广告脚本超时不阻塞

                loaded = await _wait_for_build_data(page, max_wait=40)
                if not loaded:
                    logger.warning(f"Build data not loaded, retrying...")
                    await browser.close()
                    continue

                # ── 提取页面数据 ──
                builds = await _extract_builds_from_page(page, limit)
                logger.info(f"Extracted {len(builds)} builds from {league}")
                await browser.close()
                return builds

            except Exception as e:
                logger.warning(f"Attempt {attempt+1} failed: {e}")
                await browser.close()

    logger.error(f"All {max_retries} attempts failed for {league}")
    return []


async def _extract_builds_from_page(page, limit: int = 0) -> list[dict[str, Any]]:
    """从渲染后的 DOM 提取 BD 数据。

    poe.ninja 使用 React 虚拟列表渲染，DOM 中每个 build 以
    特定的 CSS 类或 data 属性标记。通过 eval 直接从 React 内部状态提取。
    """
    # 方法1: 尝试从 window 全局状态提取（如果 React 暴露了）
    raw_data = await page.evaluate("""() => {
        // 尝试从 React fiber 或全局状态获取数据
        const text = document.body.innerText;
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
        return { method: 'innerText', lines: lines.slice(0, 500) };
    }""")

    lines = raw_data.get("lines", [])
    builds = _parse_build_lines(lines, limit)
    return builds


def _parse_build_lines(lines: list[str], limit: int = 0) -> list[dict[str, Any]]:
    """从页面文本行中解析 BD 条目。

    poe.ninja 的 BD 表格渲染后，每行包含：
    角色名、等级、升华职业、主技能、DPS、EHP 等。

    启发式解析策略：
    1. 找到表头行（含 Level/DPS/EHP 等）
    2. 后续行按列解析
    """
    builds: list[dict[str, Any]] = []

    # 查找表头
    header_idx = -1
    header_cols: list[str] = []
    for i, line in enumerate(lines):
        if "Level" in line and "EHP" in line:
            header_idx = i
            # 表头可能被 tab 或空格分隔
            header_cols = _split_table_row(line)
            break

    if header_idx < 0:
        # 回退：手动解析能找到的 build 信息
        logger.warning("No table header found, using heuristic extraction")
        return _extract_heuristic(lines, limit)

    logger.info(f"Found header at line {header_idx}: {header_cols}")

    # 解析数据行
    for i in range(header_idx + 1, len(lines)):
        if limit and len(builds) >= limit:
            break
        row = _split_table_row(lines[i])
        if len(row) >= 3:
            build = _row_to_build(row, header_cols)
            if build:
                builds.append(build)

    return builds


def _split_table_row(line: str) -> list[str]:
    """将表格行按多空格/tab 分割为列。"""
    # poe.ninja 表格列用多空格对齐
    parts = re.split(r'\t+|\s{2,}', line)
    return [p.strip() for p in parts if p.strip()]


def _row_to_build(row: list[str], headers: list[str]) -> dict[str, Any] | None:
    """将一行数据映射为 build dict。"""
    build: dict[str, Any] = {}

    # 尝试匹配已知列名
    col_map: dict[str, list[str]] = {
        "name": ["Name", "Character", "name"],
        "level": ["Level", "Lvl"],
        "ascendancy": ["Class", "Ascendancy", "Asc"],
        "main_skill": ["Skill", "Main Skill", "Primary"],
        "dps": ["DPS", "Damage", "Total DPS"],
        "ehp": ["EHP", "Effective HP"],
        "life": ["Life", "HP"],
        "energy_shield": ["ES", "Energy Shield"],
        "mana": ["Mana", "MP"],
        "spirit": ["Spirit"],
    }

    for i, col_name in enumerate(headers):
        for field, aliases in col_map.items():
            if col_name in aliases and i < len(row):
                build[field] = row[i]

    if not build.get("name"):
        return None

    return build


def _extract_heuristic(lines: list[str], limit: int = 0) -> list[dict[str, Any]]:
    """无表头时的启发式提取。

    在 poe.ninja 页面中，BD 数据行通常包含模式：
    - 角色名 后跟 等级数字
    - 升华职业名单独成行或跟在角色名后
    """
    builds: list[dict[str, Any]] = []

    # 已知升华职业列表（从 dictionary API）
    ascendancies = {
        "Titan", "Amazon", "Smith of Kitava", "Pathfinder", "Ritualist",
        "Stormweaver", "Shaman", "Blood Mage", "Oracle",
        "Disciple of Varashta", "Deadeye", "Tactician", "Witchhunter",
        "Warbringer", "Lich", "Invoker", "Chronomancer",
        "Gemling Legionnaire", "Acolyte of Chayula", "Abyssal Lich",
        "Infernalist", "Mercenary", "Druid", "Huntress",
        "Sorceress", "Warrior", "Ranger", "Monk", "Witch",
    }

    i = 0
    while i < len(lines):
        if limit and len(builds) >= limit:
            break

        line = lines[i]

        # 检测数字（等级行）
        if re.match(r'^\d{1,3}$', line.strip()):
            level = int(line.strip())
            # 向前查找角色名（通常在等级之前 1-2 行）
            name = None
            ascendancy = None
            for j in range(max(0, i - 5), i):
                candidate = lines[j].strip()
                if candidate in ascendancies:
                    ascendancy = candidate
                elif candidate and not candidate.isdigit() and len(candidate) > 2:
                    if candidate not in ascendancies and "Level" not in candidate:
                        name = candidate

            if name:
                build = {
                    "name": name,
                    "level": level,
                    "ascendancy": ascendancy,
                    "league_snapshot": "vaal",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
                builds.append(build)

        i += 1

    return builds


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape POE2 builds from poe.ninja")
    parser.add_argument("--league", default="vaal", help="League slug (vaal, standard, etc.)")
    parser.add_argument("--limit", type=int, default=100, help="Max builds to fetch (0=all)")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    args = parser.parse_args()

    builds = await scrape_league_builds(league=args.league, limit=args.limit)

    if not builds:
        logger.error("No builds extracted")
        return

    # 保存结果
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = args.output or str(OUTPUT_DIR / f"poeninja_{args.league}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(builds, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(builds)} builds to {output_path}")

    # 打印摘要
    ascendancy_counts: dict[str, int] = {}
    for b in builds:
        asc = b.get("ascendancy", "Unknown")
        ascendancy_counts[asc] = ascendancy_counts.get(asc, 0) + 1

    print(f"\n=== {args.league} — {len(builds)} builds ===")
    print("Ascendancy distribution:")
    for asc, count in sorted(ascendancy_counts.items(), key=lambda x: -x[1]):
        print(f"  {asc:30s} {count:4d}")


if __name__ == "__main__":
    asyncio.run(main())
