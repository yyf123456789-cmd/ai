#!/usr/bin/env python3
"""对比两个物件 CSV，输出店铺 CD、店铺名、新物件番号、旧物件番号。

匹配键:
  店铺名   第一个 CSV 的 T03_TENPO_NAME
           第二个 CSV 的 TEN_NM
           去掉首尾空白、全角空格，并做全角半角统一后必须一致。

  楼名     T03_TATEMONO_NAME  对  TATEYA_NM
  房间号   T03_HEYA_BANGOU    对  HEYA_NM
  地址     T03_JYUUSHO_TEXT + T03_BANCHI
           对  M.ADDR_NM||NVL(T.TATEYA_BANCHI,T.TATEYA_CHIBAN)

楼名、房间号、地址在比较前会整形，吸收这些差异:
  多空格 / 少空格、全角 / 半角、汉字数字 / 阿拉伯数字、
  各种横线（ー － - −）、番地 / 号 / 丁目 的写法、末尾的「号室」。
  楼名里的片假名长音会保留，所以「プラーナ」不会变成「プラナ」。

输出列:
  店铺CD      第一个 CSV 的 T03_TENPO_CD
  店铺名      第一个 CSV 的 T03_TENPO_NAME
  新物件番号  第二个 CSV 的 BUKKEN_NO
  旧物件番号  第一个 CSV 的 T03_ORIGINAL_HEYA_CD01

用法:
  python match_bukken.py 旧.csv 新.csv -o 匹配结果.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path


csv.field_size_limit(10 * 1024 * 1024)

OUTPUT_HEADER = ["店铺CD", "店铺名", "新物件番号", "旧物件番号"]

OLD_COLUMNS = {
    "store_cd": "T03_TENPO_CD",
    "store_name": "T03_TENPO_NAME",
    "old_no": "T03_ORIGINAL_HEYA_CD01",
    "building": "T03_TATEMONO_NAME",
    "room": "T03_HEYA_BANGOU",
}
OLD_ADDRESS_TEXT = "T03_JYUUSHO_TEXT"
OLD_BANCHI = "T03_BANCHI"
OLD_CHOUMOKU = "T03_CHOUMOKU"
OLD_ADDRESS_PARTS = (
    "T03_TODOUFUKEN",
    "T03_SHIKUCHOUSON",
    "T03_CHOUMEI",
    "T03_CHOUMOKU",
)

NEW_COLUMNS = {
    "store_cd": "WEB_RENKEI_TEN_CD",
    "new_no": "BUKKEN_NO",
    "store_name": "TEN_NM",
    "building": "TATEYA_NM",
    "room": "HEYA_NM",
}
NEW_ADDRESS_CANDIDATES = (
    "M.ADDR_NM||NVL(T.TATEYA_BANCHI,T.TATEYA_CHIBAN)",
    "ADDR_NM",
    "ADDRESS",
)

ENCODINGS = ("utf-8-sig", "cp932")

_KANJI_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_KANJI_UNITS = {"十": 10, "百": 100, "千": 1000}
_KANJI_NUM_CHARS = set(_KANJI_DIGITS) | set(_KANJI_UNITS)
_DASHES = "ー‐−–—~～ｰ"
_DASH_RE = re.compile(f"[{re.escape(_DASHES)}]")
_SPACE_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"\([^)]*\)")
_ROMAN_RE = re.compile(
    r"(?<![A-Za-z])(?:XIII|XII|XI|IX|VIII|VII|VI|IV|III|II|X|V|I)(?![A-Za-z])",
    re.IGNORECASE,
)
_ROMAN_VALUES = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
}
_NUMBER_SUFFIXES = ("丁目", "番地", "号室", "番", "号", "条", "棟", "階", "部", "丁")


def fold_width(value: str) -> str:
    """全角转半角，并去掉所有空白。"""
    text = unicodedata.normalize("NFKC", value or "")
    text = _SPACE_RE.sub("", text)
    return text.replace("ヶ", "ケ").replace("ヵ", "カ")


def kanji_to_int(token: str) -> int | None:
    """把汉字数字转成整数。支持「二十三」和「二〇二」。"""
    if not token or any(ch not in _KANJI_NUM_CHARS for ch in token):
        return None
    if all(ch in _KANJI_DIGITS for ch in token):
        number = 0
        for ch in token:
            number = number * 10 + _KANJI_DIGITS[ch]
        return number

    total = 0
    current = 0
    for ch in token:
        if ch in _KANJI_DIGITS:
            current = _KANJI_DIGITS[ch]
            continue
        unit = _KANJI_UNITS[ch]
        if current == 0:
            current = 1
        total += current * unit
        current = 0
    return total + current


def _kanji_number_in_context(text: str, start: int, end: int) -> bool:
    """只改写真正表示数量的汉字，避免把「千代」「八幡」「三田」拆开。"""
    after = text[end:]
    if not after:
        return True
    if after[0].isdigit() or after[0] in _DASHES or after[0] == "-":
        return True
    if after.startswith(_NUMBER_SUFFIXES):
        return True
    before = text[:start]
    if before and (before[-1].isdigit() or before[-1] in _DASHES or before[-1] == "-"):
        return True
    return False


def replace_kanji_numbers(text: str, *, aggressive: bool = False) -> str:
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] not in _KANJI_NUM_CHARS:
            out.append(text[index])
            index += 1
            continue
        end = index + 1
        while end < length and text[end] in _KANJI_NUM_CHARS:
            end += 1
        token = text[index:end]
        if aggressive or _kanji_number_in_context(text, index, end):
            value = kanji_to_int(token)
            out.append(str(value) if value is not None else token)
        else:
            out.append(token)
        index = end
    return "".join(out)


def replace_roman_numerals(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return str(_ROMAN_VALUES[match.group(0).upper()])

    return _ROMAN_RE.sub(repl, text)


def normalize_store(value: str) -> str:
    return fold_width(value).casefold()


def normalize_building(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("ヶ", "ケ").replace("ヵ", "カ")
    # 先按词边界把 II / Ⅱ 收成数字，再删掉空格和横线，避免 TYPE-II 黏成 TYPEII。
    text = replace_roman_numerals(text)
    text = _SPACE_RE.sub("", text)
    text = text.replace("・", "").replace("·", "").replace("･", "")
    # 「ー」在片假名里是长音，只去掉夹在英数字中间的那个（ＤーＲＯＯＭ → DROOM）。
    text = re.sub(r"(?<=[A-Za-z0-9])[ーｰ](?=[A-Za-z0-9])", "", text)
    text = re.sub(r"[-‐−–—~～]", "", text)
    text = replace_kanji_numbers(text)
    return text.casefold()


def normalize_room(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("ヶ", "ケ").replace("ヵ", "カ")
    text = replace_roman_numerals(text)
    text = _SPACE_RE.sub("", text)
    text = text.replace("号室", "").replace("部屋", "")
    text = re.sub(r"号$", "", text)
    text = _DASH_RE.sub("", text).replace("-", "")
    text = replace_kanji_numbers(text, aggressive=True)
    if re.fullmatch(r"\d+", text):
        text = text.lstrip("0") or "0"
    return text.casefold()


def normalize_address(value: str) -> str:
    text = fold_width(value)
    text = _PAREN_RE.sub("", text)
    text = text.replace("以下未定", "")
    text = replace_kanji_numbers(text)
    text = _DASH_RE.sub("-", text)
    text = text.replace("丁目", "-")
    text = re.sub(r"(?<=\d)丁", "-", text)
    text = text.replace("番地", "-")
    text = text.replace("番", "-")
    text = text.replace("号", "")
    text = re.sub(r"(?<=\d)[のノ](?=\d)", "-", text)
    text = re.sub(r"(?<=\d)[のノ]", "", text)
    text = re.sub(r"[,、，]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text.casefold()


def make_key(store: str, building: str, room: str, address: str) -> tuple[str, str, str, str]:
    return (
        normalize_store(store),
        normalize_building(building),
        normalize_room(room),
        normalize_address(address),
    )


def _cell(row: dict[str, str], column: str) -> str:
    return (row.get(column) or "").strip()


def old_address(row: dict[str, str]) -> str:
    jyuusho = _cell(row, OLD_ADDRESS_TEXT)
    banchi = _cell(row, OLD_BANCHI)
    choumoku = _cell(row, OLD_CHOUMOKU)
    if not jyuusho:
        jyuusho = "".join(_cell(row, column) for column in OLD_ADDRESS_PARTS)
        choumoku = ""
    if choumoku and fold_width(choumoku).casefold() not in fold_width(jyuusho).casefold():
        jyuusho += choumoku
    return f"{jyuusho}{banchi}"


def _clean_fieldnames(fieldnames: list[str] | None) -> list[str]:
    return [(name or "").strip().lstrip("\ufeff") for name in (fieldnames or [])]


def _remap_row(row: dict[str, str | None]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for raw_name, value in row.items():
        name = (raw_name or "").strip().lstrip("\ufeff")
        if not name:
            continue
        cleaned[name] = "" if value is None else value
    return cleaned


def _resolve_new_address(fieldnames: list[str]) -> str:
    for candidate in NEW_ADDRESS_CANDIDATES:
        if candidate in fieldnames:
            return candidate
    for name in fieldnames:
        if "ADDR" in name.upper():
            return name
    raise SystemExit(
        "第二个 CSV 找不到地址列。需要 "
        "M.ADDR_NM||NVL(T.TATEYA_BANCHI,T.TATEYA_CHIBAN)，"
        f"实际表头: {fieldnames}"
    )


def _require_columns(path: Path, fieldnames: list[str], required: dict[str, str]) -> None:
    missing = [column for column in required.values() if column not in fieldnames]
    if missing:
        raise SystemExit(f"{path} 缺少列: {', '.join(missing)}")


def read_csv(
    path: Path,
    encoding: str | None = None,
    select_columns: Callable[[list[str]], set[str]] | None = None,
) -> tuple[list[str], list[dict[str, str]], str]:
    """读入 CSV。select_columns 在表头读出后决定保留哪些列，宽表不会整行留在内存里。"""
    encodings = (encoding,) if encoding else ENCODINGS
    errors: list[str] = []
    for enc in encodings:
        try:
            with path.open("r", encoding=enc, newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = [name for name in _clean_fieldnames(reader.fieldnames) if name]
                if not fieldnames:
                    raise SystemExit(f"{path} 没有表头")
                columns = select_columns(fieldnames) if select_columns else None
                rows: list[dict[str, str]] = []
                for count, row in enumerate(reader, start=1):
                    cleaned = _remap_row(row)
                    if not any(cleaned.values()):
                        continue
                    if columns is not None:
                        cleaned = {column: cleaned.get(column, "") for column in columns}
                        if not any(cleaned.values()):
                            continue
                    rows.append(cleaned)
                    if count % 100000 == 0:
                        print(f"  {path.name}: 已读取 {count} 行", file=sys.stderr)
            return fieldnames, rows, enc
        except UnicodeDecodeError as exc:
            errors.append(f"{enc}: {exc}")
    raise SystemExit(f"无法读取 {path}。尝试过的编码: {'; '.join(errors)}")


def _has_old_address(fieldnames: list[str]) -> bool:
    if OLD_ADDRESS_TEXT in fieldnames or OLD_BANCHI in fieldnames:
        return True
    return any(column in fieldnames for column in OLD_ADDRESS_PARTS)


def load_old(path: Path, encoding: str | None = None) -> tuple[list[dict], str]:
    def select(fieldnames: list[str]) -> set[str]:
        _require_columns(path, fieldnames, OLD_COLUMNS)
        if not _has_old_address(fieldnames):
            raise SystemExit(
                f"{path} 缺少地址列。需要 {OLD_ADDRESS_TEXT} 和 {OLD_BANCHI}，"
                "或都道府県/市区町村/町名。"
            )
        columns = set(OLD_COLUMNS.values()) | {OLD_ADDRESS_TEXT, OLD_BANCHI, OLD_CHOUMOKU}
        columns.update(OLD_ADDRESS_PARTS)
        return columns

    _, rows, used = read_csv(path, encoding, select)
    records: list[dict] = []
    for row in rows:
        store_name = _cell(row, OLD_COLUMNS["store_name"])
        building = _cell(row, OLD_COLUMNS["building"])
        room = _cell(row, OLD_COLUMNS["room"])
        address = old_address(row)
        records.append(
            {
                "store_cd": _cell(row, OLD_COLUMNS["store_cd"]),
                "store_name": store_name,
                "old_no": _cell(row, OLD_COLUMNS["old_no"]),
                "key": make_key(store_name, building, room, address),
            }
        )
    return records, used


def load_new(path: Path, encoding: str | None = None) -> tuple[list[dict], str]:
    chosen: dict[str, str] = {}

    def select(fieldnames: list[str]) -> set[str]:
        _require_columns(path, fieldnames, NEW_COLUMNS)
        chosen["address"] = _resolve_new_address(fieldnames)
        return set(NEW_COLUMNS.values()) | {chosen["address"]}

    _, rows, used = read_csv(path, encoding, select)
    address_column = chosen["address"]
    records: list[dict[str, str]] = []
    for row in rows:
        store_name = _cell(row, NEW_COLUMNS["store_name"])
        building = _cell(row, NEW_COLUMNS["building"])
        room = _cell(row, NEW_COLUMNS["room"])
        address = _cell(row, address_column)
        record = {
            "store_cd": _cell(row, NEW_COLUMNS["store_cd"]),
            "store_name": store_name,
            "new_no": _cell(row, NEW_COLUMNS["new_no"]),
            "key": make_key(store_name, building, room, address),
        }
        records.append(record)
    return records, used


def match_records(
    old_records: list[dict],
    new_records: list[dict],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """按整形后的键做内连接。同一对番号只输出一次。"""
    index: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    usable_old = 0
    for record in old_records:
        store, building, room, address = record["key"]
        if not store or not record["old_no"]:
            continue
        if not building and not room and not address:
            continue
        usable_old += 1
        index[record["key"]].append(record)

    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    matched_old_keys: set[tuple[str, str, str, str]] = set()
    new_hit = 0
    new_usable = 0
    ambiguous = 0

    for record in new_records:
        store, building, room, address = record["key"]
        if not store or not record["new_no"]:
            continue
        if not building and not room and not address:
            continue
        new_usable += 1
        hits = index.get(record["key"], [])
        distinct_old = {hit["old_no"] for hit in hits}
        if len(distinct_old) > 1:
            ambiguous += 1
        if not hits:
            continue
        new_hit += 1
        matched_old_keys.add(record["key"])
        for hit in hits:
            identity = (hit["store_cd"], hit["store_name"], record["new_no"], hit["old_no"])
            if identity in seen:
                continue
            seen.add(identity)
            matched.append(
                {
                    "店铺CD": hit["store_cd"],
                    "店铺名": hit["store_name"],
                    "新物件番号": record["new_no"],
                    "旧物件番号": hit["old_no"],
                }
            )

    old_hit = sum(1 for record in old_records if record["key"] in matched_old_keys and record["old_no"])
    stats = {
        "old_rows": len(old_records),
        "new_rows": len(new_records),
        "old_usable": usable_old,
        "new_usable": new_usable,
        "matched_rows": len(matched),
        "new_matched": new_hit,
        "new_unmatched": new_usable - new_hit,
        "old_matched": old_hit,
        "old_unmatched": usable_old - old_hit,
        "ambiguous": ambiguous,
    }
    return matched, stats


def write_output(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按店铺名、楼名、房间号、地址对比两个物件 CSV，输出新旧物件番号。"
    )
    parser.add_argument("old_csv", type=Path, help="第一个 CSV（含 T03_ORIGINAL_HEYA_CD01）")
    parser.add_argument("new_csv", type=Path, help="第二个 CSV（含 BUKKEN_NO）")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("匹配结果.csv"),
        help="输出 CSV，默认 匹配结果.csv。编码为 UTF-8 BOM，Excel 可直接打开。",
    )
    parser.add_argument("--old-encoding", help="强制指定第一个 CSV 的编码，例如 cp932 或 utf-8-sig")
    parser.add_argument("--new-encoding", help="强制指定第二个 CSV 的编码")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.old_csv.is_file():
        print(f"找不到第一个 CSV: {args.old_csv}", file=sys.stderr)
        return 1
    if not args.new_csv.is_file():
        print(f"找不到第二个 CSV: {args.new_csv}", file=sys.stderr)
        return 1

    old_records, old_encoding = load_old(args.old_csv, args.old_encoding)
    new_records, new_encoding = load_new(args.new_csv, args.new_encoding)
    matched, stats = match_records(old_records, new_records)
    write_output(args.output, matched)

    print(f"第一个 CSV: {stats['old_rows']} 行 ({old_encoding})")
    print(f"第二个 CSV: {stats['new_rows']} 行 ({new_encoding})")
    print(f"匹配输出: {stats['matched_rows']} 行")
    print(f"第二个 CSV 未匹配: {stats['new_unmatched']} 行")
    print(f"第一个 CSV 未匹配: {stats['old_unmatched']} 行")
    if stats["ambiguous"]:
        print(f"一个新物件对应多个旧物件番号: {stats['ambiguous']} 条")
    print(f"已写入: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
