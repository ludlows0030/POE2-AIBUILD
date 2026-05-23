"""POE2 真实 BD 数据采集 — 从 poe.ninja protobuf API 解码。

poe.ninja 的 POE2 builds 数据以 protobuf 二进制格式通过 API 返回：
  /poe2/api/builds/{snapshotId}/search?overview={league}

本模块提供 protobuf 解码和结构化提取。
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Protobuf 线格式解码器 ──────────────────────────────


@dataclass
class ProtoField:
    """解码后的 protobuf 字段。"""
    number: int
    wire_type: int
    value: Any  # int | float | str | bytes | list[ProtoField] | dict


def decode_protobuf(data: bytes, offset: int = 0) -> tuple[dict[int, list[ProtoField]], int]:
    """无 schema 的 protobuf 线格式解码器。

    返回 (fields_dict, end_offset)，其中 fields_dict 按 field_number 分组。
    每个 field_number 对应一个 ProtoField 列表（repeated 字段）。
    """
    fields: dict[int, list[ProtoField]] = {}
    pos = offset

    while pos < len(data):
        # 读取 varint tag
        tag, varint_len = _read_varint(data, pos)
        if varint_len == 0:
            break
        pos += varint_len

        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # Varint
            value, vlen = _read_varint(data, pos)
            pos += vlen
            _add_field(fields, field_number, ProtoField(field_number, wire_type, value))

        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data):
                break
            value = struct.unpack("<d", data[pos:pos + 8])[0]
            pos += 8
            _add_field(fields, field_number, ProtoField(field_number, wire_type, value))

        elif wire_type == 2:  # Length-delimited
            length, llen = _read_varint(data, pos)
            pos += llen
            if pos + length > len(data):
                break
            chunk = data[pos:pos + length]
            pos += length

            # 尝试作为字符串解码
            try:
                text = chunk.decode("utf-8")
                if all(c.isprintable() or c in "\n\r\t" for c in text):
                    _add_field(fields, field_number, ProtoField(field_number, wire_type, text))
                    continue
            except UnicodeDecodeError:
                pass

            # 尝试作为嵌套 message 解码
            nested, _ = decode_protobuf(chunk, 0)
            if nested:
                nested_list = []
                for nf in nested.values():
                    nested_list.extend(nf)
                _add_field(fields, field_number, ProtoField(field_number, wire_type, nested_list))
            else:
                _add_field(fields, field_number, ProtoField(field_number, wire_type, chunk))

        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(data):
                break
            value = struct.unpack("<f", data[pos:pos + 4])[0]
            pos += 4
            _add_field(fields, field_number, ProtoField(field_number, wire_type, value))

        else:
            break  # wire_type 3/4 已废弃

    return fields, pos


def _add_field(fields: dict[int, list[ProtoField]], num: int, f: ProtoField) -> None:
    if num not in fields:
        fields[num] = []
    fields[num].append(f)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """读取 protobuf varint。返回 (value, bytes_read)。"""
    result = 0
    shift = 0
    pos = offset
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if (byte & 0x80) == 0:
            return result, pos - offset
        shift += 7
        if shift >= 64:
            return 0, 0
    return 0, 0


# ── 结构化提取 ──────────────────────────────────────────


@dataclass
class PoeNinjaBuild:
    """从 poe.ninja protobuf 提取的单条 BD 记录。"""
    name: str = ""
    level: int = 0
    ascendancy: str = ""
    main_skill: str = ""
    weapon_mode: str = ""
    # 核心属性
    life: int = 0
    energy_shield: int = 0
    mana: int = 0
    spirit: int = 0
    ehp: int = 0
    # 防御
    armour: int = 0
    evasion: int = 0
    deflect: int = 0
    block: int = 0
    # 抗性
    fire_res: int = 0
    cold_res: int = 0
    lightning_res: int = 0
    chaos_res: int = 0
    # 属性
    strength: int = 0
    dexterity: int = 0
    intelligence: int = 0
    # 其他
    movement_speed: int = 0
    life_regen: int = 0
    item_rarity: int = 0
    # 技能列表
    skills: list[dict[str, Any]] = field(default_factory=list)
    # 核心天赋
    keystones: list[str] = field(default_factory=list)
    anointed: str = ""
    # 装备
    items: list[dict[str, Any]] = field(default_factory=list)
    # 原始数据
    raw: dict[str, Any] = field(default_factory=dict)


def parse_builds_from_protobuf(filepath: Path) -> tuple[dict[str, Any], list[PoeNinjaBuild]]:
    """从 protobuf 文件解析 BD 数据和字典映射。

    Returns:
        (dictionaries, builds) — 字典映射和 BD 列表
    """
    with open(filepath, "rb") as f:
        data = f.read()

    logger.info(f"Decoding protobuf: {len(data)} bytes")
    fields, _ = decode_protobuf(data)

    logger.info(f"Top-level fields: {len(fields)} field numbers")
    for num, flist in fields.items():
        total = sum(
            len(f.value) if isinstance(f.value, (list, dict)) else 1
            for f in flist
        )
        sample = _describe_field(flist[0]) if flist else "empty"
        logger.info(f"  field #{num}: {len(flist)} occurrences, total_items={total}, sample={sample[:120]}")

    # 分离字典和 BD 数据
    dictionaries = _extract_dictionaries(fields)
    builds = _extract_builds(fields, dictionaries)

    return dictionaries, builds


def _describe_field(f: ProtoField) -> str:
    """描述字段内容（调试用）。"""
    if f.wire_type == 0:
        return f"varint={f.value}"
    elif f.wire_type in (1, 5):
        return f"float={f.value}"
    elif f.wire_type == 2:
        if isinstance(f.value, list):
            sub = []
            for sf in f.value:
                if isinstance(sf, ProtoField):
                    sub.append(f"#{sf.number}:{_describe_field(sf)}")
            return f"nested[{len(f.value)}]: " + "; ".join(sub[:3])
        elif isinstance(f.value, str):
            return f'str="{f.value[:80]}"'
        else:
            return f"bytes[{len(f.value)}]"
    return f"unknown_wire={f.wire_type}"


def _extract_dictionaries(fields: dict[int, list[ProtoField]]) -> dict[str, Any]:
    """提取字典映射表（class IDs, skill IDs, weapon modes, passives）。"""
    dictionaries: dict[str, Any] = {}

    for num, flist in fields.items():
        for f in flist:
            if isinstance(f.value, list):
                # 检查是否是字典结构（string → varint 映射）
                mapping: dict[str, int] = {}
                str_key = None
                for sub_f in f.value:
                    if isinstance(sub_f, ProtoField):
                        if sub_f.wire_type == 2 and isinstance(sub_f.value, str):
                            str_key = sub_f.value
                        elif sub_f.wire_type == 0 and str_key is not None:
                            mapping[str_key] = sub_f.value
                            str_key = None

                if mapping:
                    dict_name = f"dict_{num}"
                    dictionaries[dict_name] = mapping
                    logger.info(f"  Dictionary {dict_name}: {len(mapping)} entries, keys={list(mapping.keys())[:5]}...")

    return dictionaries


def _extract_builds(
    fields: dict[int, list[ProtoField]],
    dictionaries: dict[str, Any],
) -> list[PoeNinjaBuild]:
    """从 protobuf fields 中提取 BD 数据列表。"""
    builds: list[PoeNinjaBuild] = []

    # 找到包含 per-build 数据的嵌套结构
    # poe.ninja protobuf 结构通常是: field 1 = dictionary block, field 2..N = build data
    # 每个 build 是嵌套的 message，包含 level, life, ES, skills 等

    for num, flist in fields.items():
        for f in flist:
            if isinstance(f.value, list) and len(f.value) > 10:
                # 这可能是 BD 列表
                for item in f.value:
                    if isinstance(item, ProtoField) and isinstance(item.value, list):
                        build = _parse_build_message(item.value, dictionaries)
                        if build and build.level > 0:
                            builds.append(build)

    # 如果没找到，尝试不同的解析路径
    if not builds:
        logger.warning("No builds found via direct parsing, trying alternative...")
        builds = _parse_builds_alternative(fields, dictionaries)

    logger.info(f"Extracted {len(builds)} builds")
    return builds


def _parse_build_message(
    nested_fields: list[ProtoField],
    dictionaries: dict[str, Any],
) -> PoeNinjaBuild | None:
    """解析单个 build 的嵌套 message。"""
    build = PoeNinjaBuild()
    attr_map: dict[int, str] = {}

    for f in nested_fields:
        if not isinstance(f, ProtoField):
            continue

        # 先收集 string 字段作为属性名
        if f.wire_type == 2 and isinstance(f.value, str):
            attr_map[f.number] = f.value

    # 再解析值
    str_vals: dict[int, str] = {}
    int_vals: dict[int, int] = {}

    for f in nested_fields:
        if not isinstance(f, ProtoField):
            continue
        if f.wire_type == 0 and isinstance(f.value, int):
            int_vals[f.number] = f.value
        elif f.wire_type == 2 and isinstance(f.value, str):
            str_vals[f.number] = f.value

    # 用 field number 映射到已知属性
    # (具体的 field number 映射需要通过调试确定)
    known_attrs = {
        2: "level", 3: "life", 4: "energy_shield", 5: "ehp",
        6: "mana", 7: "spirit",
    }

    for fnum, attr_name in known_attrs.items():
        if fnum in int_vals:
            setattr(build, attr_name, int_vals[fnum])

    build.raw = {"ints": int_vals, "strings": str_vals}
    return build


def _parse_builds_alternative(
    fields: dict[int, list[ProtoField]],
    dictionaries: dict[str, Any],
) -> list[PoeNinjaBuild]:
    """备选解析方案：遍历所有嵌套结构寻找 build 数据。"""
    builds: list[PoeNinjaBuild] = []

    # 在 protobuf 数据中搜索所有可读字符串和数字的组合
    all_text = _collect_all_strings(fields)
    logger.info(f"All readable strings: {len(all_text)}")

    return builds


def _collect_all_strings(fields: dict[int, list[ProtoField]], depth: int = 0) -> list[str]:
    """递归收集所有字符串字段。"""
    result: list[str] = []
    for flist in fields.values():
        for f in flist:
            if isinstance(f.value, str):
                result.append(f.value)
            elif isinstance(f.value, list):
                for item in f.value:
                    if isinstance(item, ProtoField):
                        if isinstance(item.value, str):
                            result.append(item.value)
                        elif isinstance(item.value, list):
                            sub_fields: dict[int, list[ProtoField]] = {}
                            for sub in item.value:
                                if isinstance(sub, ProtoField):
                                    if sub.number not in sub_fields:
                                        sub_fields[sub.number] = []
                                    sub_fields[sub.number].append(sub)
                            result.extend(_collect_all_strings(sub_fields, depth + 1))
    return result


# ── 调试入口 ─────────────────────────────────────────────


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pb_path = Path("/tmp/poeninja_builds.pb")
    if len(sys.argv) > 1:
        pb_path = Path(sys.argv[1])

    if not pb_path.exists():
        print(f"File not found: {pb_path}")
        sys.exit(1)

    dicts, builds = parse_builds_from_protobuf(pb_path)

    print(f"\nDictionaries: {list(dicts.keys())}")
    print(f"Builds: {len(builds)}")

    if builds:
        for b in builds[:5]:
            print(f"  Lv{b.level} {b.ascendancy} - Life:{b.life} ES:{b.energy_shield} EHP:{b.ehp}")
