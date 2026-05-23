"""清洗 unique_item.explicit_mods 原始格式 → 可读格式。

将 'IncreasedLife: base maximum life40—60Global' 转为 '+(40-60) to maximum life'
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models.base import UniqueItem


def clean_mod(mod_str: str) -> str:
    """清洗单条词缀格式。"""
    if ":" not in mod_str:
        return mod_str

    _, rest = mod_str.split(":", 1)
    rest = re.sub(r"(Global|Local|Flag|PerLevel|Unique|LocalUnscalable\s*Value)$", "", rest).strip()

    # 匹配 "描述 数值—数值 [%]"
    m = re.search(r"^(.*?)(-?\d+\.?\d*)\s*[—\-–]\s*(-?\d+\.?\d*)\s*(%?)\s*$", rest)
    if m:
        desc = m.group(1).strip()
        vmin = m.group(2)
        vmax = m.group(3)
        suffix = m.group(4)

        # 简化常见前缀
        desc = re.sub(r"^base\s+", "", desc)
        desc = re.sub(r"\s+", " ", desc)

        if suffix == "%":
            return f"{vmin}% to {vmax}% {desc}".strip()
        else:
            return f"+({vmin}-{vmax}) to {desc}".strip()

    return rest.strip()


async def main():
    engine = create_async_engine(settings.postgres_url, echo=False)

    async with AsyncSession(engine) as db:
        # 查询所有需要清洗的传奇物品
        result = await db.execute(
            select(UniqueItem.id, UniqueItem.explicit_mods)
            .where(UniqueItem.explicit_mods != None)  # noqa: E711
        )
        rows = list(result)

        cleaned_count = 0
        for uid, mods in rows:
            if not mods or len(mods) == 0:
                continue
            cleaned = [clean_mod(m) for m in mods]
            if cleaned == mods:
                continue  # 没有变化
            # explicit_mods 是 TEXT[] 类型，直接传 Python list
            await db.execute(
                text("UPDATE unique_item SET explicit_mods = :mods WHERE id = :uid"),
                {"mods": cleaned, "uid": uid},
            )
            cleaned_count += 1

        await db.commit()
        print(f"Cleaned {cleaned_count}/{len(rows)} unique items")

        # 验证
        result = await db.execute(
            text("SELECT name_en, explicit_mods FROM unique_item WHERE explicit_mods IS NOT NULL LIMIT 5")
        )
        for row in result:
            print(f"\n{row[0]}:")
            for m in (row[1] or []):
                print(f"  - {m}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
