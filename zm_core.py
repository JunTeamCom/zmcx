#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zmcx 核心逻辑：与界面无关的纯 Python 部分。

GTK 4 界面（zm_gui_gtk.py）与 Tkinter 界面（zm_gui_tk.py）都从这里取数据，
两边的行为必须一致，所以取码、着色这些规则只写一份。

数据来源（等价于需求中的两条 shell 命令）:
    1) grep 羽 zm.txt                    -> 拆字方案
    2) grep '^羽\t' zm.yaml | sort -t'\t' -k3,3n   -> 编码（可能多个，按权重升序）

zm.txt 一行形如 `岩\t[山石\xa0ll\xa0g]`：NBSP 分隔，前面是字根、后面是每个
字根各自的码，两者严格 1:1（已对 20976 条全量校验）。`{...}` 表示复合字根，
如 `鬻 [弓米弓一口{冈}\xa0yz\xa0uf\xa0yz\xa0a\xa0j\xa0ld]`。

63 条给了两套拆法，形如 `R1 | R2 C1 | C2`，其中 C1 对应 R1、C2 对应 R2
（已对全部 63 条校验）。

取码规则（据《郑码输入法/取码》）：
    一个字至多四键。单字根字取该根全码，只有一键时补 A；两个字根字两根各取全码；
    三个字根字首根全取、次根一键、末根补到第四键；四个字根字首根全取，首根仅一键时
    次根再取一键，末两根各一键。故四根字若首根占两码，次根整根不取（如「國」）。

「全码」即每字完整取码（多为四键），与本码表中的 1–3 键简码相对。
全码里每个字母按实际出处着色，末尾补码（vv / aa / pp 等）用中性灰。

输入多个字时，对每个字分别查询并单独展示。
"""

from __future__ import annotations

import json
import os
import re
import sys

APP_ID = "com.junteam.zmcx"
DATA_NAMES = ("zm.txt", "zm.yaml")

NBSP = "\xa0"
_CODE_RE = re.compile(r"^[a-z]+$")

# 一套拆法：(字根, 该字根贡献的码) 组成的序列
Decomp = list[tuple[str, str]]


def split_root_units(root_part: str) -> list[str]:
    """把字根串拆成字根单元，`{...}` 连同里面的内容算一个单元。

    >>> split_root_units("山石")
    ['山', '石']
    >>> split_root_units("弓米弓一口{冈}")
    ['弓', '米', '弓', '一', '口', '冈']
    """
    units: list[str] = []
    i = 0
    while i < len(root_part):
        if root_part[i] == "{":
            end = root_part.find("}", i + 1)
            if end < 0:
                units.append(root_part[i + 1:])
                break
            units.append(root_part[i + 1:end])
            i = end + 1
        else:
            units.append(root_part[i])
            i += 1
    return units


def _split_group(tokens: list[str]) -> tuple[list[str], list[str]]:
    """把一个 token 组切成 (字根 token, 码 token)：码是从第一个纯小写 token 起。"""
    roots: list[str] = []
    codes: list[str] = []
    seen_code = False
    for token in tokens:
        if _CODE_RE.match(token):
            seen_code = True
            codes.append(token)
        elif seen_code:
            codes.append(token)  # 数据异常：码后面又冒出字根，按码处理
        else:
            roots.append(token)
    return roots, codes


def _pair(roots: list[str], codes: list[str]) -> Decomp:
    """字根与码一对一配对；数量对不上时保留全部信息，缺的一侧留空。"""
    units = [unit for token in roots for unit in split_root_units(token)]
    if len(units) == len(codes):
        return list(zip(units, codes))
    size = min(len(units), len(codes))
    pairs: Decomp = list(zip(units[:size], codes[:size]))
    pairs.extend((unit, "") for unit in units[size:])
    pairs.extend(("", code) for code in codes[size:])
    return pairs


def parse_decomp(raw: str) -> list[Decomp]:
    """解析 zm.txt 的 `[...]` 内容，返回 1~2 套拆法。

    普通条目：`山石\xa0ll\xa0g` -> [[('山','ll'), ('石','g')]]
    双拆法条目：`R1 | R2 C1 | C2` -> 两套，C1 配 R1、C2 配 R2
    """
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]

    tokens = [token.strip() for token in text.split(NBSP)]
    tokens = [token for token in tokens if token]

    groups: list[list[str]] = [[]]
    for token in tokens:
        for index, part in enumerate(token.split("|")):
            if index:
                groups.append([])
            if part:
                groups[-1].append(part)

    if len(groups) == 3:
        roots1, stray1 = _split_group(groups[0])
        roots2, codes1 = _split_group(groups[1])
        _, codes2 = _split_group(groups[2])
        if not stray1 and codes1 and codes2:
            return [_pair(roots1, codes1), _pair(roots2, codes2)]

    # 无 `|` 或格式意外：整体当一套拆法
    roots, codes = _split_group([token for group in groups for token in group])
    return [_pair(roots, codes)] if roots or codes else []


# 取码角色的配色：首根 / 次根 / 次末根 / 末根
ROLE_COLORS = ("#3a72cf", "#2e9a63", "#c87a21", "#b34a80")
PAD_CODE_COLOR = "#8f8f8f"  # 全码末尾的补码（vv / aa / pp 等）
ROLE_LABELS = ("首根", "次根", "次末根", "末根")


def pick_full_code(codes: list[tuple[str, int]]) -> str | None:
    """全码 = 该字最长的那条编码（郑码每字至多四键，简码则更短）。

    >>> pick_full_code([("j", 1), ("jivv", 4)])
    'jivv'
    """
    if not codes:
        return None
    return max(codes, key=lambda item: (len(item[0]), -item[1]))[0]


def align_full_code(variant: Decomp, code: str) -> list[int | None]:
    """把全码逐字母回溯到这一套拆法的字根，返回每个字母的字根下标。

    按字根顺序做最长前缀匹配；匹配不到的尾部字母即补码（vv / aa / pp 等）。
    两套拆法各自对齐，不能混用——混用会把下标套到另一套的字根上。

    >>> align_full_code([("山", "ll"), ("石", "g")], "llg")
    [0, 0, 1]
    """
    owners: list[int | None] = [None] * len(code)
    pos = 0
    for index, (_root, root_code) in enumerate(variant):
        take = 0
        for length in range(min(len(root_code), len(code) - pos), 0, -1):
            if code[pos:pos + length] == root_code[:length]:
                take = length
                break
        for offset in range(take):
            owners[pos + offset] = index
        pos += take
    return owners


def root_contribution(
    variant: Decomp,
    full: str | None,
    owners: list[int | None],
) -> tuple[list[str] | None, list[int | None] | None]:
    """按全码的实际取码，算出每个字根贡献了哪几位、以及配色序号。

    返回 (每个根贡献的字母串, 每个根的配色序号)；未参与取码为 None。
    拿不到归属时退回按字根位置编号。

    >>> root_contribution([("山", "ll"), ("石", "g")], "llg", [0, 0, 1])
    (['ll', 'g'], [0, 1])
    """
    if not full or not owners:
        return None, None
    if all(owner is None for owner in owners):
        return None, None  # 全码是纯补码（如 aapp），无从对应
    used: list[list[str]] = [[] for _ in variant]
    for position, letter in enumerate(full):
        owner = owners[position]
        if owner is not None and owner < len(used):
            used[owner].append(letter)
    ranks: list[int | None] = [None] * len(variant)
    rank = 0
    for index, letters in enumerate(used):
        if letters:
            ranks[index] = rank
            rank += 1
    return ["".join(letters) for letters in used], ranks


def describe_full_code(code: str, owners: list[int | None], variant: Decomp) -> str:
    """全码每个字母的来历，用在悬停提示里（variant 必须与 owners 同源）。"""
    parts = []
    for index, letter in enumerate(code):
        owner = owners[index]
        if owner is not None and owner < len(variant):
            parts.append(f"{letter}={variant[owner][0]}")
        else:
            parts.append(f"{letter}=补码")
    return "全码 " + code + "：" + "、".join(parts)


class CodeAnalysis:
    """一个字的全码分析结果：全码、每个字母归属哪个字根、以及配色序号。"""

    __slots__ = ("full", "owners", "owner_ranks", "contribs", "best")

    def __init__(
        self,
        full: str | None,
        owners: list[int | None],
        owner_ranks: list[int | None],
        contribs: list[tuple[list[str] | None, list[int | None] | None]],
        best: int,
    ) -> None:
        self.full = full
        self.owners = owners
        self.owner_ranks = owner_ranks
        self.contribs = contribs
        self.best = best


def analyze_codes(
    variants: list[Decomp] | None,
    codes: list[tuple[str, int]],
) -> CodeAnalysis:
    """把「拆字方案」和「编码表」对齐，算出着色要用的全部信息。

    字根标的是「取码序号」颜色，全码也必须用同一套序号，
    否则下标一错位颜色就跟字根对不上（如 嬂 拆法1 的「日」是第 5 个字根）。
    """
    full = pick_full_code(codes) if codes else None
    owners_by_variant: list[list[int | None]] = []
    contribs: list[tuple[list[str] | None, list[int | None] | None]] = []
    if full and variants:
        for variant in variants:
            owners = align_full_code(variant, full)
            owners_by_variant.append(owners)
            contribs.append(root_contribution(variant, full, owners))

    # 全码的着色取「能解释最多字母」的那套拆法
    best = (
        max(
            range(len(owners_by_variant)),
            key=lambda i: sum(o is not None for o in owners_by_variant[i]),
            default=0,
        )
        if owners_by_variant
        else 0
    )
    owners: list[int | None] = owners_by_variant[best] if owners_by_variant else []
    ranks_best = contribs[best][1] if contribs and best < len(contribs) else None
    owner_ranks: list[int | None] = []
    if ranks_best is not None:
        owner_ranks = [
            ranks_best[owner] if owner is not None and owner < len(ranks_best) else None
            for owner in owners
        ]
    return CodeAnalysis(full, owners, owner_ranks, contribs, best)


def code_letter_colors(code: str, owner_ranks: list[int | None] | None) -> list[str]:
    """全码逐字母的配色：参与取码的按取码序号上色，补码用中性灰。"""
    colors: list[str] = []
    for index, _letter in enumerate(code):
        owner = owner_ranks[index] if owner_ranks and index < len(owner_ranks) else None
        colors.append(
            PAD_CODE_COLOR if owner is None else ROLE_COLORS[owner % len(ROLE_COLORS)]
        )
    return colors


def root_roles(count: int) -> list[int | None]:
    """给每个字根标出取码角色，未参与取码的为 None。

    郑码取首根、次根、次末根、末根四码；字根不足四个时靠前的角色优先，
    例如两个字根时第二个算「次根」而不是「末根」。

    >>> root_roles(2)
    [0, 1]
    >>> root_roles(5)
    [0, 1, None, 2, 3]
    """
    roles: list[int | None] = [None] * count
    for role, pos in enumerate((0, 1, count - 2, count - 1)):
        if 0 <= pos < count and roles[pos] is None:
            roles[pos] = role
    return roles


PUA_PLACEHOLDER = "\u25af"  # ▯ 私有区字形在所有常规字体里都没有，用占位符代替


def root_display(unit: str) -> tuple[str, str | None]:
    """返回 (要显示的文字, 需要额外说明时的提示)。

    zm.txt 里有 23 条用到了私用区字符 U+E82C（如 撫 = 扌𠂉▯灬），任何常规字体
    都没有这个字形，直接显示会是豆腐块，这里换成一个明显的占位符。
    """
    if not any(0xE000 <= ord(ch) <= 0xF8FF for ch in unit):
        return unit, None
    shown = "".join(
        PUA_PLACEHOLDER if 0xE000 <= ord(ch) <= 0xF8FF else ch for ch in unit
    )
    points = "、".join(f"U+{ord(ch):04X}" for ch in unit if 0xE000 <= ord(ch) <= 0xF8FF)
    return shown, f"{points} 为私用区字形，字体缺字，原样显示会变成方块"


def find_data_files() -> tuple[str, str]:
    """在脚本目录、当前目录、环境变量 ZM_DIR 中查找 zm.txt / zm.yaml。"""
    candidates = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    env_dir = os.environ.get("ZM_DIR")
    if env_dir:
        candidates.insert(0, env_dir)
    paths = []
    for name in DATA_NAMES:
        for base in candidates:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                paths.append(path)
                break
        else:
            paths.append(os.path.join(candidates[0], name))
    return paths[0], paths[1]


THEME_MODES = ("system", "light", "dark")
THEME_LABELS = {"system": "跟随系统", "light": "浅色", "dark": "深色"}


def config_dir() -> str:
    """用户配置目录：Windows 用 %APPDATA%，其余沿用 XDG 约定。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "zm-chaxun")


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def load_theme_mode() -> str:
    """读取上次选择的主题；没有记录或文件损坏时回到「跟随系统」。"""
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "system"
    if not isinstance(data, dict):
        return "system"
    mode = data.get("theme")
    if mode in THEME_MODES:
        return mode
    legacy = data.get("dark")  # 兼容只存深浅布尔值的旧版本
    if isinstance(legacy, bool):
        return "dark" if legacy else "light"
    return "system"


def save_theme_mode(mode: str) -> None:
    """记住主题选择；写不了就算了，不影响使用。"""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as fh:
            json.dump({"theme": mode}, fh)
    except OSError:
        pass


class ZmDict:
    """内存索引版码表：拆字方案来自 zm.txt，编码/权重来自 zm.yaml。"""

    def __init__(self, txt_path: str, yaml_path: str) -> None:
        self.txt_path = txt_path
        self.yaml_path = yaml_path
        self.decomp: dict[str, list[Decomp]] = {}
        self.codes: dict[str, list[tuple[str, int]]] = {}
        self.loaded = False

    def load(self) -> None:
        decomp: dict[str, list[Decomp]] = {}
        with open(self.txt_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                char, sep, rest = line.partition("\t")
                if not rest.strip() and len(char) > 1 and "[" in char:
                    # 个别坏行（如 亥）字与 [...] 粘在同一字段，制表符后为空
                    head, _, body = char.partition("[")
                    if len(head) == 1:
                        char, rest = head, "[" + body
                if not char or not rest.strip():
                    continue
                variants = parse_decomp(rest)
                if variants:
                    decomp[char] = variants

        codes: dict[str, list[tuple[str, int]]] = {}
        with open(self.yaml_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) != 3:
                    continue
                char, code, weight = parts
                if char.startswith("#"):
                    continue  # zm.yaml 开头是注释行
                try:
                    num = int(weight)
                except ValueError:
                    continue
                codes.setdefault(char, []).append((code, num))

        # 等价于 sort -t$'\t' -k3,3n（稳定排序，权重相同保持原顺序）
        for items in codes.values():
            items.sort(key=lambda item: item[1])

        self.decomp = decomp
        self.codes = codes
        self.loaded = True

    def lookup(self, char: str) -> tuple[list[Decomp], list[tuple[str, int]]]:
        return self.decomp.get(char, []), self.codes.get(char, [])

    def query(self, text: str) -> tuple[list[tuple[str, int]], list[tuple[str, list[Decomp], list[tuple[str, int]]]]]:
        """返回 (整词编码, 逐字结果)。整词编码即 grep $'^中国\t' zm.yaml 的结果。"""
        word = "".join(text.split())
        phrase = self.codes.get(word, []) if len(word) > 1 else []
        chars = [(ch, *self.lookup(ch)) for ch in word]
        return phrase, chars
