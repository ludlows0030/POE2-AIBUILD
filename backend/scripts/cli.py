#!/usr/bin/env python3
"""POE2 BD Agent — 命令行交互工具。

使用方式:
    python scripts/cli.py "我想玩一个冰系暴击武僧，低预算打王"
    python scripts/cli.py --format markdown "Lightning arrow deadeye for mapping"
    python scripts/cli.py --validate '{"skill_gems": {...}}'
    python scripts/cli.py --list                    # 列出已生成的 BD
    python scripts/cli.py --interactive             # 交互模式

需要:
    - Docker 服务运行中 (PostgreSQL + Neo4j)
    - .env 中配置 ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── 命令实现 ───────────────────────────────────────────


async def cmd_generate(user_request: str, output_format: str = "summary") -> str:
    """生成 BD 并返回格式化结果。"""
    from app.database import async_session_factory
    from app.agents.build_agent import build_agent
    from app.validation.formatter import build_formatter

    print(f"> 分析需求: {user_request}")
    print(f"> 正在查询参考 BD...")

    async with async_session_factory() as db:
        result = await build_agent.generate(db, user_request)

    if output_format == "markdown":
        return build_formatter.to_markdown(result)
    elif output_format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2)
    elif output_format == "summary":
        summary = build_formatter.to_summary(result)
        concept = result.get("core_concept", "")
        if concept:
            summary += f"\n\n{concept}"
        # 追加装备简要信息
        equipment = result.get("equipment", {})
        if equipment:
            lines = ["\n关键装备:"]
            for slot, item in list(equipment.items())[:6]:
                lines.append(f"  {slot}: {item}")
            summary += "\n".join(lines)
        return summary
    return str(result)


async def cmd_validate(build_json: str) -> str:
    """验证 BD 草案。"""
    from app.validation.rules import build_validator

    build = json.loads(build_json)
    result = build_validator.validate(build)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def cmd_list(limit: int = 10) -> str:
    """列出已生成的 BD。"""
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.models.base import GeneratedBuild

    async with async_session_factory() as db:
        result = await db.execute(
            select(GeneratedBuild)
            .order_by(GeneratedBuild.created_at.desc())
            .limit(limit)
        )
        builds = result.scalars().all()

    if not builds:
        return "暂无已生成的 BD。使用 'generate' 命令创建一个。"

    lines = [f"最近 {len(builds)} 个 BD:"]
    for b in builds:
        date = b.created_at.strftime("%Y-%m-%d") if b.created_at else "?"
        lines.append(f"  [{date}] {b.build_name} — {b.core_skill} (confidence: {b.confidence:.0%})")
    return "\n".join(lines)


async def cmd_interactive() -> None:
    """交互式对话模式。"""
    from app.database import async_session_factory
    from app.agents.build_agent import build_agent
    from app.validation.formatter import build_formatter

    print("POE2 BD Agent — 交互模式")
    print("输入 BD 需求开始生成，输入 /list 查看历史，输入 /quit 退出\n")

    async with async_session_factory() as db:
        while True:
            try:
                user_input = input("Build > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见!")
                break

            if not user_input:
                continue

            if user_input.lower() in ("/quit", "/q", "exit"):
                print("再见!")
                break

            if user_input.lower() in ("/list", "/l"):
                print(await cmd_list())
                continue

            print("思考中...\n")
            try:
                result = await build_agent.generate(db, user_input)
                print(build_formatter.to_summary(result))
                print()

                concept = result.get("core_concept", "")
                if concept:
                    print(concept)
                    print()

                # 技能总览
                skills = result.get("skill_gems", {}).get("active", [])
                if skills:
                    print("技能配置:")
                    for s in skills:
                        name = s.get("name", "?")
                        supports = " → ".join(s.get("support_gems", []))
                        print(f"  {name}: {supports}")
                    print()

                # 验证状态
                validation = result.get("validation", {})
                if validation.get("errors"):
                    print(f"[!] 验证问题: {', '.join(validation['errors'])}")

                print(f"置信度: {result.get('confidence', 0):.0%} | 预算: {result.get('estimated_budget_divines', '?')}d")
                print("-" * 40)

            except Exception as e:
                print(f"[错误] {e}")


# ── 入口 ───────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="POE2 BD Agent CLI")
    sub = parser.add_subparsers(dest="command", help="命令")

    # generate
    gen = sub.add_parser("generate", help="生成 BD")
    gen.add_argument("request", nargs="+", help="自然语言需求描述")
    gen.add_argument("--format", "-f", choices=["summary", "markdown", "json"],
                     default="summary", help="输出格式 (默认: summary)")

    # validate
    val = sub.add_parser("validate", help="验证 BD 草案")
    val.add_argument("build", help="BD JSON 字符串或 @文件路径")

    # list
    sub.add_parser("list", help="列出已生成的 BD")

    # interactive
    sub.add_parser("interactive", aliases=["i"], help="交互模式")

    args = parser.parse_args()

    if not args.command:
        # 如果直接传了字符串，默认是 generate
        if len(sys.argv) > 1:
            user_req = " ".join(sys.argv[1:])
            print(asyncio.run(cmd_generate(user_req)))
        else:
            parser.print_help()
        return

    if args.command == "generate":
        user_req = " ".join(args.request)
        print(asyncio.run(cmd_generate(user_req, args.format)))

    elif args.command == "validate":
        build_str = args.build
        if build_str.startswith("@"):
            build_str = Path(build_str[1:]).read_text(encoding="utf-8")
        print(asyncio.run(cmd_validate(build_str)))

    elif args.command == "list":
        print(asyncio.run(cmd_list()))

    elif args.command in ("interactive", "i"):
        asyncio.run(cmd_interactive())


if __name__ == "__main__":
    main()
