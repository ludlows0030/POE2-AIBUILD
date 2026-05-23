"""POE2 poe.ninja Protobuf 解析器 — 提取真实玩家 BD 数据。

用法:
    cd backend && python scripts/parse_poeninja.py
    cd backend && python scripts/parse_poeninja.py --league vaal --limit 100 -o data/builds/vaal.json
"""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

LEAGUES = {
    "vaal": "fate-of-the-vaal",
    "vaalhc": "fate-of-the-vaal-hc",
    "vaalssf": "fate-of-the-vaal-ssf",
    "vaalhcssf": "fate-of-the-vaal-hc-ssf",
    "standard": "standard",
}


# ── Protobuf 基础工具 ─────────────────────────────────────

def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """读取 protobuf varint，返回 (值, 新位置)。"""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if (byte & 0x80) == 0:
            return result, pos
        shift += 7
        if shift >= 64:
            return 0, pos
    return 0, pos


def parse_protobuf_fields(data: bytes) -> dict[int, list[tuple[str, int, bytes | None]]]:
    """解析 protobuf 字节为 {field_number: [(wire_type, value_or_size, chunk_or_None)]}。"""
    fields: dict[int, list[tuple[str, int, bytes | None]]] = {}
    pos = 0
    while pos < len(data):
        tag = data[pos]
        fn = tag >> 3
        wt = tag & 0x07
        pos += 1
        if wt == 0:
            val, pos = read_varint(data, pos)
            if fn not in fields:
                fields[fn] = []
            fields[fn].append(('varint', val, None))
        elif wt == 2:
            length, pos = read_varint(data, pos)
            if pos + length > len(data):
                break
            chunk = data[pos:pos + length]
            pos += length
            if fn not in fields:
                fields[fn] = []
            fields[fn].append(('bytes', len(chunk), chunk))
        elif wt == 1:
            pos += 8
        elif wt == 5:
            pos += 4
        else:
            break
    return fields


def decode_str(chunk: bytes) -> str | None:
    """尝试解码字节为 UTF-8 字符串。"""
    try:
        return chunk.decode('utf-8')
    except UnicodeDecodeError:
        return None


# ── 字典加载 ──────────────────────────────────────────────

# 从 build 数据 field 6 中提取的字典哈希值
DICT_HASHES = {
    "class": "e198d88dc5417779bb6f556f66ad24ef525024c1",
    "gem": "b52d0885e529d358542ac5909386aee200cb64b6",
    "keypassive": "b239765917bccf3943d954b7c70c2048de049958",
    "weaponmode": "591bbe3bae28a64ea0ebf2d3f85cc9c9bddbcf14",
    "item": "ec77cc690489d14f455b7f1e815c09822c718b30",
    "skillmode": "0ff147d2358ef2ab12a1142d970ca61050f13110",
    "anointed": "2185928e3412622ef94a0acbe8cdbc394605a479",
}

_CACHED_DICTS: dict[str, dict[int, str]] = {}


def _load_dict(dict_type: str) -> dict[int, str]:
    """从 poe.ninja dictionary API 加载 ID→名称 映射。

    字典 API 返回格式:
      message {
        string type_name = 1;           // "class", "gem", "weaponmode", ...
        repeated string names = 2;      // 名字按顺序排列，ID = 位置 (1-indexed)
      }
    """
    if dict_type in _CACHED_DICTS and _CACHED_DICTS[dict_type]:
        return _CACHED_DICTS[dict_type]

    import subprocess

    hash_key = DICT_HASHES.get(dict_type)
    if not hash_key:
        logger.warning(f"Unknown dictionary type: {dict_type}")
        _CACHED_DICTS[dict_type] = {}
        return {}

    url = f"https://poe.ninja/poe2/api/builds/dictionary/{hash_key}"
    r = subprocess.run(
        ["curl", "-s", url, "--max-time", "10"],
        capture_output=True,
    )

    if r.returncode != 0 or not r.stdout:
        logger.warning(f"Failed to load dictionary '{dict_type}'")
        _CACHED_DICTS[dict_type] = {}
        return {}

    data = r.stdout
    fields = parse_protobuf_fields(data)

    # Field 2 = repeated string names, ID = position (1-indexed)
    mapping: dict[int, str] = {}
    idx = 1
    for item in fields.get(2, []):
        name = decode_str(item[2])
        if name:
            mapping[idx] = name
            idx += 1

    _CACHED_DICTS[dict_type] = mapping
    logger.info(f"  Dictionary '{dict_type}': {len(mapping)} entries")
    return mapping


def get_all_dicts() -> dict[str, dict[int, str]]:
    """加载所有需要的字典。"""
    result = {}
    for name in ["class", "gem", "weaponmode", "keypassive"]:
        result[name] = _load_dict(name)
    return result


# ── Build 数据解析 ────────────────────────────────────────

def _parse_column_values(column_chunk: bytes) -> list:
    """解析列式数据中的值列表。

    每列格式: { string name = 1; repeated Value values = 2; }
    每个 Value 是: { string text = 1; } 或 { varint number = 2; } 或嵌套消息 { repeated packed_varints = 3; }。
    """
    fields = parse_protobuf_fields(column_chunk)
    values_field = fields.get(2, [])

    result = []
    for item in values_field:
        if item[1] == 0 or item[2] is None:
            result.append(None)
            continue

        chunk = item[2]
        if len(chunk) == 0:
            result.append(None)
            continue

        vf = parse_protobuf_fields(chunk)

        if 1 in vf:  # string value
            text = decode_str(vf[1][0][2]) if vf[1][0][2] else None
            result.append(text)
        elif 2 in vf:  # varint value
            result.append(vf[2][0][1])
        elif 3 in vf:  # packed repeated varints (skills, keystones)
            # field 3 contains packed varint bytes
            packed_bytes = vf[3][0][2]
            if packed_bytes:
                ids = _read_packed_varints(packed_bytes)
                result.append(ids)
            else:
                result.append([])
        else:
            result.append(None)

    return result


def _read_packed_varints(data: bytes) -> list[int]:
    """读取 packed varint 列表（protobuf packed repeated 编码）。"""
    result = []
    pos = 0
    while pos < len(data):
        val, pos = read_varint(data, pos)
        result.append(val)
    return result


def _parse_skills_list(skill_ids: list[int], gem_dict: dict[int, str]) -> list[dict]:
    """解析技能列表（packed varint IDs → gem 名称）。

    每个 ID 是 gem 字典中的索引。ID 0 表示空位/无效。
    列表结构: [主技能, 辅助宝石1, 辅助宝石2, ...] 但主技能可能是第一个非零 ID。
    """
    skills = []
    for gem_id in skill_ids:
        if gem_id == 0:
            continue
        name = gem_dict.get(gem_id, f'Gem_{gem_id}')
        skills.append({'id': gem_id, 'name': name})
    return skills


def _parse_keystones(keystone_ids: list[int], keypassive_dict: dict[int, str]) -> list[dict]:
    """解析关键天赋列表（packed varint IDs → 名称）。"""
    result = []
    for kid in keystone_ids:
        if kid == 0:
            continue
        result.append({
            'id': kid,
            'name': keypassive_dict.get(kid, f'Keystone_{kid}')
        })
    return result


def _parse_dps_data(dps_raw_value, dps_headers: list[dict]) -> dict:
    """解析 DPS 数据。

    每个 build 的 DPS 消息:
      field 1: total DPS display string ("783k", "27M")
      field 2: varint (number of skills with DPS data)
      field 3: packed bytes (per-skill DPS encoding, unclear format)

    返回: {"total": float, "display": str}
    """
    result = {"total": 0, "display": ""}
    if isinstance(dps_raw_value, str):
        # DPS 是字符串格式
        result['display'] = dps_raw_value
        result['total'] = _parse_short_number(dps_raw_value)
    elif isinstance(dps_raw_value, (int, float)):
        result['total'] = float(dps_raw_value)
    return result


def parse_builds_from_bytes(data: bytes, dicts: dict[str, dict[int, str]]) -> list[dict]:
    """从 protobuf 字节数据解析 BD 列表。"""
    outer = parse_protobuf_fields(data)
    if 1 not in outer:
        logger.error("No field 1 in outer message")
        return []

    inner_data = outer[1][0][2]
    fields = parse_protobuf_fields(inner_data)

    # 总记录数
    total = 0
    if 1 in fields:
        total = fields[1][0][1]
    logger.info(f"Total builds in snapshot: {total}")

    # skill DPS headers (field 11)
    dps_headers: list[str] = []
    if 11 in fields:
        for item in fields[11]:
            if item[2]:
                text = decode_str(item[2])
                if text:
                    dps_headers.append(text)

    # ── 解析列式数据 (field 5) ──
    if 5 not in fields:
        logger.error("No field 5 (columnar data) found")
        return []

    columns = fields[5]
    col_values = []
    col_names = []
    for col_item in columns:
        chunk = col_item[2]
        cf = parse_protobuf_fields(chunk)
        name = None
        if 1 in cf:
            name = decode_str(cf[1][0][2])
        col_names.append(name)
        values = _parse_column_values(chunk)
        col_values.append(values)

    for i, (name, vals) in enumerate(zip(col_names, col_values)):
        logger.info(f"  Column[{i}] '{name}': {len(vals)} values")

    num_builds = max(len(cv) for cv in col_values) if col_values else 0

    ascendancy_dict = dicts.get('class', {})
    gem_dict = dicts.get('gem', {})
    keypassive_dict = dicts.get('keypassive', {})

    builds = []
    for i in range(num_builds):
        name = col_values[0][i] if len(col_values) > 0 and i < len(col_values[0]) else None
        if name is None:
            continue

        class_id = col_values[2][i] if len(col_values) > 2 and i < len(col_values[2]) else 0
        # Normalize: None/0 both mean "no ascendancy selected"
        if class_id is None:
            class_id = 0
        ascendancy = ascendancy_dict.get(class_id) if class_id > 0 else "Unascended"

        # Skills: packed varint list → gem names
        skill_ids = col_values[3][i] if len(col_values) > 3 and i < len(col_values[3]) else []
        skills = _parse_skills_list(skill_ids if isinstance(skill_ids, list) else [], gem_dict)

        # Keystones: packed varint list → names
        keystone_ids = col_values[4][i] if len(col_values) > 4 and i < len(col_values[4]) else []
        keystones = _parse_keystones(
            keystone_ids if isinstance(keystone_ids, list) else [],
            keypassive_dict
        )

        level = col_values[5][i] if len(col_values) > 5 and i < len(col_values[5]) else 0
        life = col_values[6][i] if len(col_values) > 6 and i < len(col_values[6]) else 0
        es = col_values[7][i] if len(col_values) > 7 and i < len(col_values[7]) else 0

        # EHP — 可能是缩写字符串 "29k" 或 varint
        ehp_val = col_values[8][i] if len(col_values) > 8 and i < len(col_values[8]) else 0
        if isinstance(ehp_val, str):
            ehp_val = _parse_short_number(ehp_val)

        # DPS — 列 9: { field1=display_string, field2=count, field3=packed_data }
        dps_raw = col_values[9][i] if len(col_values) > 9 and i < len(col_values[9]) else None
        dps_data = _parse_dps_data(dps_raw, [])
        total_dps = dps_data.get('total', 0)

        # 用列 10 的 ehp 数据补充
        if (ehp_val == 0 or isinstance(col_values[8][i], str)) and len(col_values) > 10:
            ehp2 = col_values[10][i]
            if isinstance(ehp2, (int, float)) and ehp2 > 0:
                ehp_val = ehp2
            elif isinstance(ehp2, str):
                parsed_ehp2 = _parse_short_number(ehp2)
                if parsed_ehp2 > 0:
                    ehp_val = parsed_ehp2

        # 主技能: skills 列表的第一个非 support gem
        main_skill = ""
        if skills:
            main_skill = skills[0].get('name', '')

        build = {
            "name": name,
            "account": col_values[1][i] if len(col_values) > 1 and i < len(col_values[1]) else '',
            "level": level or 0,
            "ascendancy": ascendancy,
            "class_id": class_id,
            "main_skill": main_skill,
            "life": life or 0,
            "energy_shield": es or 0,
            "ehp": ehp_val or 0,
            "dps": total_dps,
            "dps_display": dps_data.get('display', ''),
            "skills": skills,
            "keystones": keystones,
        }
        builds.append(build)

    logger.info(f"Parsed {len(builds)} builds")
    return builds


def _parse_short_number(text: str) -> float:
    """解析缩写数字: '29k' → 29000, '1.2M' → 1200000。"""
    text = text.strip().lower().replace(',', '')
    if not text:
        return 0
    multipliers = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}
    suffix = text[-1]
    if suffix in multipliers:
        try:
            return float(text[:-1]) * multipliers[suffix]
        except ValueError:
            return 0
    try:
        return float(text)
    except ValueError:
        return 0


# ── 数据获取 ──────────────────────────────────────────────

def fetch_builds(league: str = "vaal", limit: int = 0) -> list[dict]:
    """从 poe.ninja API 获取并解析 BD 数据。"""
    import subprocess

    logger.info("Loading dictionaries...")
    dicts = get_all_dicts()

    # 获取 snapshot ID
    idx_url = "https://poe.ninja/poe2/api/data/index-state"
    idx_result = subprocess.run(
        ["curl", "-s", idx_url, "--max-time", "15", "-H", "User-Agent: Mozilla/5.0"],
        capture_output=True, text=True,
    )
    idx_data = json.loads(idx_result.stdout)
    snapshots = idx_data.get("snapshotVersions", [])

    snapshot_id = None
    overview = LEAGUES.get(league, league)
    for snap in snapshots:
        if snap["url"] == league:
            snapshot_id = snap["version"]
            break

    if not snapshot_id:
        logger.error(f"Snapshot not found for league '{league}'")
        return []

    logger.info(f"League: {league}, Snapshot: {snapshot_id}, Overview: {overview}")

    pb_url = f"https://poe.ninja/poe2/api/builds/{snapshot_id}/search?overview={overview}"
    pb_path = Path(tempfile.gettempdir()) / f"poeninja_{league}.pb"

    result = subprocess.run(
        ["curl", "-s", pb_url, "--max-time", "30", "-o", str(pb_path),
         "-H", "User-Agent: Mozilla/5.0"],
        capture_output=True,
    )

    if not pb_path.exists() or pb_path.stat().st_size == 0:
        logger.error("Failed to download protobuf data")
        return []

    logger.info(f"Downloaded {pb_path.stat().st_size} bytes")
    data = pb_path.read_bytes()
    builds = parse_builds_from_bytes(data, dicts)
    pb_path.unlink(missing_ok=True)

    if limit and limit > 0:
        builds = builds[:limit]

    return builds


# ── 主入口 ────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Parse poe.ninja POE2 build data")
    parser.add_argument("--league", default="vaal", help="League slug")
    parser.add_argument("--limit", type=int, default=100, help="Max builds (0=all)")
    parser.add_argument("-o", "--output", default=None, help="Output JSON path")
    parser.add_argument("--from-file", default=None, help="Parse from local .pb file")
    args = parser.parse_args()

    dicts = get_all_dicts()

    if args.from_file:
        pb_path = Path(args.from_file)
        if not pb_path.exists():
            logger.error(f"File not found: {pb_path}")
            sys.exit(1)
        data = pb_path.read_bytes()
        builds = parse_builds_from_bytes(data, dicts)
    else:
        builds = fetch_builds(args.league)

    if args.limit and args.limit > 0:
        builds = builds[:args.limit]

    # 补全默认值
    for b in builds:
        b.setdefault("level", 0)
        b.setdefault("ascendancy", "Unknown")
        b.setdefault("life", 0)
        b.setdefault("energy_shield", 0)
        b.setdefault("ehp", 0)
        b.setdefault("dps", 0)
        b.setdefault("league", args.league)

    logger.info(f"Total builds parsed: {len(builds)}")

    # 输出
    output_path = args.output or f"data/builds/poeninja_{args.league}.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(builds, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved to {output_path}")

    # 摘要
    if builds:
        asc_dist: dict[str, int] = {}
        for b in builds:
            asc = b.get("ascendancy", "Unknown")
            asc_dist[asc] = asc_dist.get(asc, 0) + 1

        print(f"\n{'='*60}")
        print(f"League: {args.league} | Total builds: {len(builds)}")
        print(f"{'='*60}")
        print("Top ascendancies:")
        for asc, cnt in sorted(asc_dist.items(), key=lambda x: -x[1])[:10]:
            print(f"  {asc:35s} {cnt:5d}")

        sample = builds[0]
        print(f"\nSample build:")
        for k in ["name", "level", "ascendancy", "life", "energy_shield",
                   "ehp", "dps"]:
            v = sample.get(k, "N/A")
            print(f"  {k}: {v}")
        if sample.get("skills"):
            print(f"  skills: {[s.get('name', '?') for s in sample['skills'][:5]]}")
        if sample.get("dps_breakdown"):
            dps_items = list(sample['dps_breakdown'].items())
            print(f"  dps_breakdown ({len(dps_items)} entries):")
            for sk, sv in dps_items[:8]:
                print(f"    {sk}: {sv:,.1f}")


if __name__ == "__main__":
    main()
