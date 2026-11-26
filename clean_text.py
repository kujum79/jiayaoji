#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""clean_text.py —— 传统食养文章 → 结构化 JSON

用法：
    python clean_text.py 文章.txt                  # 读取文件，追加写入同目录 foodTherapyDB.json
    python clean_text.py 文章.txt -o 数据库.json    # 指定数据库路径
    python clean_text.py --text "当归生姜羊肉汤 出处：《金匮要略》 ……"
    type 文章.txt | python clean_text.py           # 从标准输入读取

输出结构（追加到数据库数组末尾，id 自增 ft_001 / ft_002 …）：
    {
      "id": "ft_001",
      "name": "当归生姜羊肉汤",
      "source": "《金匮要略》",
      "suitable": ["阳虚质"],
      "ingredients": ["当归", "生姜", "羊肉"],
      "steps": ["羊肉切块焯水", "与当归生姜同煮"],
      "contraindications": "孕妇忌服，感冒发热期间停用"
    }
"""

import argparse
import json
import os
import re
import sys

# ---------- 体质词表（输出统一为「X质」） ----------
_CONSTITUTIONS = [
    ("气虚", "气虚质"), ("阳虚", "阳虚质"), ("阴虚", "阴虚质"),
    ("痰湿", "痰湿质"), ("湿热", "湿热质"), ("血瘀", "血瘀质"),
    ("气郁", "气郁质"), ("特禀", "特禀质"), ("平和", "平和质"),
]
# 兼容「血瘀质」以外的常见叫法
_CONSTITUTION_ALIASES = {
    "血淤": "血瘀", "淤血": "血瘀", "寒湿": "阳虚", "虚寒": "阳虚",
    "上火": "湿热", "湿热质": "湿热", "气虚体质": "气虚", "阳虚体质": "阳虚",
    "阴虚体质": "阴虚", "痰湿体质": "痰湿", "湿热体质": "湿热",
    "血瘀体质": "血瘀", "气郁体质": "气郁", "特禀体质": "特禀",
    "平和体质": "平和", "平和质": "平和",
}

# ---------- 章节标签 ----------
_LABELS = {
    "name": ["名称", "方名", "菜名", "品名", "膳食名称"],
    "source": ["出处", "来源", "源自", "引自", "文献出处"],
    "suitable": ["适宜体质", "适用体质", "适宜人群", "适用人群", "适合体质", "适合", "适宜"],
    "ingredients": ["核心食材", "食材", "用料", "配料", "原料", "主料", "材料", "配方"],
    "steps": ["做法", "制法", "制作方法", "烹饪方法", "烹饪", "步骤", "制作"],
    "contraindications": ["禁忌人群", "禁忌", "注意事项", "不宜人群", "忌用", "慎用"],
}
_LABEL_OWNER = {label: key for key, labels in _LABELS.items() for label in labels}
_ALL_LABELS = sorted(_LABEL_OWNER, key=len, reverse=True)

_LABEL_RE = re.compile(
    r"(?:【(%s)】|(%s)\s*[:：])" % ("|".join(_ALL_LABELS), "|".join(_ALL_LABELS))
)

# 做法里的“废话/装饰”前缀
_STEP_PREFIX_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十]+步|[一二三四五六七八九十]+[、.．]|[（(]?\d+[）)][、.．]?|\d+[、.．])\s*"
)
_FILLER_RE = re.compile(r"^(?:首先|先|然后|接着|随后|之后|最后|再|另|其次|另外)\s*")

# 做法动作词（用于剔除说明性废话）
_COOKING_WORDS = re.compile(
    "洗|切|焯|煮|炖|蒸|炒|煎|炸|焖|熬|煲|烧|泡|浸|放|加|入|调|撒|淋|捞|拌|"
    "揉|腌|烤|滤|冷却|装盘|控干|去皮|去骨|去核|打碎|研磨"
)

# 食材中的数量/单位
_QTY_RE = re.compile(
    r"[0-9０-９一二三四五六七八九十百半]+\s*"
    r"(?:克|g|G|千克|kg|两|斤|毫升|ml|ML|升|个|只|枚|片|根|段|块|颗|条|把|撮|杯|碗|勺|汤匙|茶匙|滴)"
)
_STOP_INGREDIENTS = {
    "适量", "少许", "若干", "各", "清水", "水", "盐", "食用油", "油", "料酒", "葱", "姜",
}


def _clean_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_sections(text):
    """按【标签】/ 标签：切分章节；未命中的部分丢弃，名称/出处再走兜底规则。"""
    sections, matches = {}, list(_LABEL_RE.finditer(text))
    for i, m in enumerate(matches):
        label = m.group(1) or m.group(2)
        key = _LABEL_OWNER.get(label)
        if not key or key in sections:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip(" \n：:，,。；;")
        if body:
            sections[key] = body
    return sections


def _normalize_constitutions(raw):
    found = []
    # 注意不能用「和/与/及」做分隔符：会拆坏「平和」这类体质词
    for token in re.split(r"[、，,；;/|\s]+", raw or ""):
        token = token.strip()
        if not token:
            continue
        core = _CONSTITUTION_ALIASES.get(token, token)
        core = re.sub(r"(?:体质|质)$", "", core)
        for keyword, normalized in _CONSTITUTIONS:
            if core == keyword or keyword in core:
                if normalized not in found:
                    found.append(normalized)
                break
    return found


def _extract_name(text, sections):
    if sections.get("name"):
        return re.sub(r"^[《【\s]+|[》】\s]+$", "", sections["name"]).strip()[:40]
    for line in text.split("\n"):
        line = line.strip(" \t#-—*·")
        if not line:
            continue
        # 首行常见形式：一、当归生姜羊肉汤 / 1. 当归生姜羊肉汤
        line = re.sub(r"^(?:第[一二三四五六七八九十]+[章节节]|[一二三四五六七八九十]+[、.．]|\d+[、.．])\s*", "", line)
        if not line:
            continue
        if len(line) <= 30 and not re.search(r"[：:，,。；;]", line):
            return line
        # 正文里找「XX汤/粥/羹/茶/饮…」
        m = re.search(r"([\u4e00-\u9fa5A-Za-z]{2,20}(?:汤|粥|羹|茶|饮|丸|膏|散|酒|糊|糕|饭|面))", line)
        if m:
            return m.group(1)
        if len(line) <= 30:
            return line
    return ""


def _extract_source(text, sections):
    if sections.get("source"):
        return sections["source"].split("\n")[0].strip()[:60]
    m = re.search(r"《[^》]{2,40}》", text)
    return m.group(0) if m else ""


def _extract_suitable(text, sections, contra_start):
    raw = sections.get("suitable", "")
    if raw:
        result = _normalize_constitutions(raw)
        if result:
            return result
    # 兜底：只在「禁忌」之前找体质词，避免把“阳虚忌用”当成适宜
    scope = text[:contra_start] if contra_start and contra_start > 0 else text
    return _normalize_constitutions(scope)


def _extract_ingredients(text, sections):
    raw = sections.get("ingredients", "")
    if not raw:
        # 兜底：找「当归、生姜、羊肉」这类顿号列表（取句子而不是整行，避免把做法一起吞进来）
        for sentence in re.split(r"[。；;\n]", text[: max(200, len(text) // 2)]):
            if sentence.count("、") >= 2:
                raw = sentence
                break
    raw = _QTY_RE.sub("", raw)
    raw = re.sub(r"[（(][^）)]*[）)]", "", raw)  # 去括号备注
    raw = re.sub(r"(?:和|与|及)", "、", raw)
    items = []
    for token in re.split(r"[、，,；;/\s]+", raw):
        token = re.sub(r"^(?:准备|备好|取|用|需要|把)\s*", "", token.strip(" 。.：:·-*"))
        token = re.sub(r"(?:适量|少许|若干|少量|各)$", "", token).strip()
        if not token or token in _STOP_INGREDIENTS:
            continue
        if len(token) > 12 or not re.search(r"[\u4e00-\u9fa5A-Za-z]", token):
            continue
        if token not in items:
            items.append(token)
    return items


def _clean_step(step):
    step = step.strip(" \n\t-—·*")
    while True:
        new = _STEP_PREFIX_RE.sub("", step)
        new = _FILLER_RE.sub("", new)
        if new == step:
            break
        step = new
    step = re.sub(r"^(?:将|把|再|并)\s*", "", step)
    step = step.strip(" 。.；;，,：:")
    return step


def _extract_steps(text, sections):
    raw = sections.get("steps", "")
    if not raw:
        for line in text.split("\n"):
            if re.search(r"(?:做法|制法|制作|烹饪|步骤)\s*[:：]", line):
                raw = re.split(r"(?:做法|制法|制作|烹饪|步骤)\s*[:：]", line, maxsplit=1)[-1]
                break
    if not raw:
        # 无「做法：」标签时，从全文挑含烹饪动作的句子（排除体质/禁忌/说明句）
        candidates = []
        for sentence in re.split(r"[\n。；;]+", text):
            s = sentence.strip(" \t-—·*")
            if len(s) < 4:
                continue
            if re.search(r"禁用|忌用|忌服|忌食|不宜|慎用|停用|忌口|适宜|适合|适用|体质|食养|食方", s):
                continue
            if _COOKING_WORDS.search(s):
                candidates.append(s)
        raw = "。".join(candidates)
    if not raw:
        return []
    steps = []
    for part in re.split(r"[\n。；;]+", raw):
        step = _clean_step(part)
        if len(step) < 3:
            continue
        if step not in steps:
            steps.append(step)
    # 过滤说明性废话（保留至少一条）
    if len(steps) > 1:
        steps = [s for s in steps if _COOKING_WORDS.search(s)] or steps[:1]
    return steps


def _extract_contraindications(text, sections):
    raw = sections.get("contraindications", "")
    if raw:
        return re.sub(r"^(?:注意|提示|说明)\s*[:：]\s*", "", raw.split("\n")[0].strip()).strip(" 。.；;，,：:")[:120]
    for part in re.split(r"[\n。；;]+", text):
        if re.search(r"禁用|忌用|忌服|忌食|不宜|慎用|停用|不可|忌口", part):
            return re.sub(r"^(?:注意|提示|说明)\s*[:：]\s*", "", part.strip()).strip(" 。.；;，,：:")[:120]
    return ""


def parse_article(text):
    """把一段纯文本解析为一条结构化记录（字段与 foodTherapyDB.json 一致）。"""
    text = _clean_text(text)
    sections = _split_sections(text)
    contra_start = text.find("禁忌")
    name = _extract_name(text, sections)
    if not name:
        raise ValueError("未能识别【名称】，请确认文本首行是否为汤/粥/羹等方名")
    return {
        "name": name,
        "source": _extract_source(text, sections),
        "suitable": _extract_suitable(text, sections, contra_start),
        "ingredients": _extract_ingredients(text, sections),
        "steps": _extract_steps(text, sections),
        "contraindications": _extract_contraindications(text, sections),
    }


def _load_db(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("items") or []
    if not isinstance(data, list):
        raise ValueError("%s 不是 JSON 数组，无法追加" % path)
    return data


def _next_id(records):
    max_no = 0
    for record in records:
        m = re.match(r"ft_(\d+)", str((record or {}).get("id", "")))
        if m:
            max_no = max(max_no, int(m.group(1)))
    return "ft_%03d" % (max_no + 1)


def append_to_db(record, db_path):
    records = _load_db(db_path)
    for old in records:
        if (old or {}).get("name") == record["name"]:
            return None, "已存在同名食方《%s》，跳过" % record["name"]
    record = dict({"id": _next_id(records)}, **record)
    records.append(record)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return record, ""


def main():
    parser = argparse.ArgumentParser(description="传统食养文章 → 结构化 JSON（追加 foodTherapyDB.json）")
    parser.add_argument("input", nargs="?", help="纯文本文件路径；省略时读取 --text 或标准输入")
    parser.add_argument("--text", help="直接传入文章文本")
    parser.add_argument("-o", "--db", default="foodTherapyDB.json", help="输出数据库路径（默认同目录 foodTherapyDB.json）")
    args = parser.parse_args()

    if args.text:
        raw = args.text
    elif args.input:
        with open(args.input, "r", encoding="utf-8-sig") as f:
            raw = f.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        parser.print_help()
        return 1
    if not raw.strip():
        print("输入为空", file=sys.stderr)
        return 1

    try:
        record = parse_article(raw)
    except ValueError as e:
        print("解析失败：%s" % e, file=sys.stderr)
        return 2

    record, message = append_to_db(record, args.db)
    if record is None:
        print(message, file=sys.stderr)
        return 0
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("\n已追加到 %s，id=%s" % (os.path.abspath(args.db), record["id"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
