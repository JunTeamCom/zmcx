#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""郑码编码查询 GUI (GTK4)。

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
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

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

ROLE_COLORS = ("#3a72cf", "#2e9a63", "#c87a21", "#b34a80")
PAD_CODE_COLOR = "#8f8f8f"  # 全码末尾的补码（vv / aa / pp 等）

CSS_TEMPLATE = """
.card { background: alpha(currentColor, 0.06); border-radius: 10px; padding: 10px 14px; }
.char { font-size: 22pt; font-weight: 800; }
.root { font-size: 14pt; }
.root-first { color: @ROLE0@; }
.root-second { color: @ROLE1@; }
.root-third { color: @ROLE2@; }
.root-last { color: @ROLE3@; }
.root-idle { opacity: 0.4; }
.rootcode { font-size: 11pt; font-family: monospace; }
.rootcode-idle { font-size: 11pt; font-family: monospace; opacity: 0.4; }
.vsep { font-size: 9pt; opacity: 0.5; }
.code { font-size: 15pt; font-weight: 700; font-family: monospace; }
.weight { font-size: 9pt; opacity: 0.55; }
.codebtn { border-radius: 8px; padding: 4px 8px; }
.codebtn:hover { background: alpha(currentColor, 0.10); }
.missing { opacity: 0.55; }
.status { font-size: 9pt; opacity: 0.6; }
.hint { font-size: 9pt; opacity: 0.45; }
/* GtkMenuButton wraps an inner button node: the background is drawn there,
   so we must target "menubutton > button" instead of the menubutton itself. */
.themebtn > button { background: none; box-shadow: none; border: none; outline: none; }
.themebtn > button:hover { background: alpha(currentColor, 0.08); }
.themebtn > button:active, .themebtn > button:checked { background: alpha(currentColor, 0.12); }
"""
CSS = (
    CSS_TEMPLATE.replace("@ROLE0@", ROLE_COLORS[0])
    .replace("@ROLE1@", ROLE_COLORS[1])
    .replace("@ROLE2@", ROLE_COLORS[2])
    .replace("@ROLE3@", ROLE_COLORS[3])
    .encode("utf-8")
)

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


ROLE_LABELS = ("首根", "次根", "次末根", "末根")
ROLE_CLASSES = ("root-first", "root-second", "root-third", "root-last")


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


SETTINGS_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "zm-chaxun",
    "settings.json",
)


THEME_MODES = ("system", "light", "dark")
THEME_LABELS = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
THEME_ICONS = {
    "system": "preferences-system-symbolic",
    "light": "weather-clear-symbolic",
    "dark": "weather-clear-night-symbolic",
}
SYSTEM_SCHEMA = "org.gnome.desktop.interface"


def load_theme_mode() -> str:
    """读取上次选择的主题；没有记录或文件损坏时回到「跟随系统」。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
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
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"theme": mode}, fh)
    except OSError:
        pass


def make_system_settings() -> Gio.Settings | None:
    """系统的外观设置对象；桌面没有这套 schema 时返回 None。"""
    try:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(SYSTEM_SCHEMA, True) if source else None
        if schema is None or "color-scheme" not in schema.list_keys():
            return None
        return Gio.Settings.new_full(schema, None, None)
    except (AttributeError, TypeError):
        return None


def system_prefers_dark(system_settings: Gio.Settings | None = None) -> bool | None:
    """系统是否偏好深色；完全拿不到时返回 None。"""
    if system_settings is None:
        system_settings = make_system_settings()
    if system_settings is not None:
        return system_settings.get_string("color-scheme") == "prefer-dark"
    settings = Gtk.Settings.get_default()
    if settings is not None:
        name = settings.get_property("gtk-theme-name") or ""
        if name:
            return "dark" in name.lower()
    return None


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


class ZmWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, zdict: ZmDict) -> None:
        super().__init__(application=app)
        self.zdict = zdict
        self._debounce_id = 0
        self._status_reset_id = 0
        self._load_summary = ""

        self.set_default_size(760, 680)
        self.set_titlebar(self._build_header())

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(14)

        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("输入汉字，可输入多个，如：羽好")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda *_: self._run_query())
        self.entry.connect("search-changed", self._on_changed)
        root.append(self.entry)

        self.status = Gtk.Label(xalign=0.0)
        self.status.set_css_classes(["status"])
        self.status.set_selectable(False)
        root.append(self.status)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_css_classes(["background"])
        self.listbox.add_css_class("boxed-list-separate")
        self.listbox.set_show_separators(False)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.listbox)
        root.append(scroller)

        self.set_child(root)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.entry.set_sensitive(False)
        self.status.set_text("正在加载码表…")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _build_header(self) -> Gtk.HeaderBar:
        header = Gtk.HeaderBar()
        title = Gtk.Label(label="郑码编码查询")
        title.add_css_class("title")
        header.set_title_widget(title)

        self._theme_mode = load_theme_mode()
        self._system_settings = make_system_settings()

        menu = Gio.Menu()
        for mode in THEME_MODES:
            menu.append(THEME_LABELS[mode], f"win.theme::{mode}")

        self.theme_button = Gtk.MenuButton()
        self.theme_button.set_valign(Gtk.Align.CENTER)
        self.theme_button.add_css_class("themebtn")  # 平时透明，样式见 CSS
        self.theme_button.set_menu_model(menu)
        self.theme_button.set_tooltip_text("主题")
        header.pack_end(self.theme_button)

        action = Gio.SimpleAction.new_stateful(
            "theme", GLib.VariantType.new("s"), GLib.Variant("s", self._theme_mode)
        )
        action.connect("activate", self._on_theme_selected)
        self.add_action(action)
        self._theme_action = action

        self._apply_theme_mode(self._theme_mode)
        if self._system_settings is not None:
            self._system_settings.connect("changed::color-scheme", self._on_system_theme_changed)
        return header

    def _on_theme_selected(self, _action: Gio.SimpleAction, parameter: GLib.Variant | None) -> None:
        mode = parameter.get_string() if parameter is not None else self._theme_mode
        if mode not in THEME_MODES:
            return
        self._theme_action.set_state(GLib.Variant("s", mode))
        self._theme_mode = mode
        self._apply_theme_mode(mode)
        save_theme_mode(mode)

    def _on_system_theme_changed(self, _settings: Gio.Settings, _key: str) -> None:
        if self._theme_mode == "system":
            self._apply_theme_mode("system")

    def _apply_theme_mode(self, mode: str) -> None:
        if mode == "system":
            dark = bool(system_prefers_dark(self._system_settings))
        else:
            dark = mode == "dark"
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", dark)

        self.theme_button.set_icon_name(THEME_ICONS[mode])
        label = THEME_LABELS[mode]
        if mode == "system":
            label = f"{label}（当前{'深色' if dark else '浅色'}）"
        self.theme_button.set_tooltip_text(f"主题：{label}")

    def _load_worker(self) -> None:
        try:
            self.zdict.load()
        except OSError as exc:
            GLib.idle_add(self._on_load_failed, str(exc))
            return
        GLib.idle_add(self._on_loaded)

    def _on_loaded(self) -> bool:
        self.entry.set_sensitive(True)
        self.entry.grab_focus()
        self._load_summary = (
            "已加载 %s（%d 字） + %s（%d 字 / %d 条编码）"
            % (
                os.path.basename(self.zdict.txt_path),
                len(self.zdict.decomp),
                os.path.basename(self.zdict.yaml_path),
                len(self.zdict.codes),
                sum(len(v) for v in self.zdict.codes.values()),
            )
        )
        self.status.set_text(self._load_summary)
        return GLib.SOURCE_REMOVE

    def _on_load_failed(self, message: str) -> bool:
        self.status.set_text(f"加载失败：{message}")
        return GLib.SOURCE_REMOVE

    def _on_changed(self, *_args: object) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(250, self._run_query)

    def _run_query(self) -> bool:
        self._debounce_id = 0
        text = self.entry.get_text()
        while self.listbox.get_first_child() is not None:
            self.listbox.remove(self.listbox.get_first_child())

        if not self.zdict.loaded:
            return GLib.SOURCE_REMOVE
        phrase_codes, chars = self.zdict.query(text)
        if not chars:
            return GLib.SOURCE_REMOVE

        if phrase_codes:
            word = "".join(text.split())
            self.listbox.append(
                self._make_card(
                    word,
                    None,
                    phrase_codes,
                    title_text="词语",
                )
            )
        for char, variants, codes in chars:
            self.listbox.append(self._make_card(char, variants, codes))
        return GLib.SOURCE_REMOVE

    def _make_card(
        self,
        char: str,
        variants: list[Decomp] | None,
        codes: list[tuple[str, int]],
        title_text: str = "拆字方案",
    ) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_css_classes(["card"])

        # 全码先算出来：字根的着色要跟它一致（哪个根出了哪几位）
        full = pick_full_code(codes) if codes else None
        owners_by_variant: list[list[int | None]] = []
        contribs: list[tuple[list[str] | None, list[int | None] | None]] = []
        if full and variants:
            for variant in variants:
                owners_by_variant.append(align_full_code(variant, full))
                contribs.append(
                    self._root_contribution(variant, full, owners_by_variant[-1])
                )
        # 全码的着色取「能解释最多字母」的那套拆法
        best = max(
            range(len(owners_by_variant)),
            key=lambda i: sum(o is not None for o in owners_by_variant[i]),
            default=0,
        ) if owners_by_variant else 0
        owners: list[int | None] = owners_by_variant[best] if owners_by_variant else []
        # 字根标的是「取码序号」颜色，全码也必须用同一套序号，
        # 否则下标一错位颜色就跟字根对不上（如 嬂 拆法1 的「日」是第 5 个字根）
        ranks_best = contribs[best][1] if contribs and best < len(contribs) else None
        owner_ranks: list[int | None] = []
        if ranks_best is not None:
            owner_ranks = [
                ranks_best[owner] if owner is not None and owner < len(ranks_best) else None
                for owner in owners
            ]

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        char_label = Gtk.Label(label=char, xalign=0.0)
        char_label.set_css_classes(["char"])
        char_label.set_selectable(True)
        char_label.set_wrap(True)
        head.append(char_label)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        info.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0.0)
        title.set_markup(f"<span size='small' alpha='60%'>{GLib.markup_escape_text(title_text)}</span>")
        info.append(title)
        if variants:
            for index, decomp in enumerate(variants):
                per_root, ranks = contribs[index] if index < len(contribs) else (None, None)
                info.append(
                    self._make_decomp_widget(
                        decomp, index, len(variants), per_root, ranks
                    )
                )
        elif variants is not None:
            none_label = Gtk.Label(label="zm.txt 中未找到该字", xalign=0.0)
            none_label.set_css_classes(["missing"])
            info.append(none_label)
        head.append(info)
        box.append(head)

        if codes:
            roles_note = ""
            if full and variants and owners:
                roles_note = self._describe_full_code(full, owners, variants[best])
            colored = owner_ranks if any(rank is not None for rank in owner_ranks) else None
            for code, weight in codes:
                box.append(
                    self._make_code_row(
                        code, weight, colored if code == full else None, roles_note
                    )
                )
        else:
            empty = Gtk.Label(label="zm.yaml 中无编码记录", xalign=0.0)
            empty.set_css_classes(["missing"])
            box.append(empty)

        row.set_child(box)
        return row

    @staticmethod
    def _root_contribution(
        variant: Decomp,
        full: str | None,
        owners: list[int | None],
    ) -> tuple[list[str] | None, list[int | None] | None]:
        """按全码的实际取码，算出每个字根贡献了哪几位、以及配色序号。

        返回 (每个根贡献的字母串, 每个根的配色序号)；未参与取码为 None。
        拿不到归属时退回按字根位置编号。
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

    def _make_decomp_widget(
        self,
        decomp: Decomp,
        index: int,
        total: int,
        contributed: list[str] | None = None,
        ranks: list[int | None] | None = None,
    ) -> Gtk.Widget:
        """一个字的一套拆法：逐字根并列展示「字根 + 它自己的码」，如 山ll 石g。

        着色的依据是全码的实际取码：参与了全码的字根按取码先后上色，
        没参与的留灰（例如四根字里首根占两码时，次根整根不取）。
        """
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(2)
        flow.set_column_spacing(8)
        flow.set_max_children_per_line(24)
        flow.set_valign(Gtk.Align.CENTER)

        roles = ranks if ranks is not None else root_roles(len(decomp))
        for position, ((unit, unit_code), role) in enumerate(zip(decomp, roles)):
            took = contributed[position] if contributed is not None and position < len(contributed) else None
            shown, note = root_display(unit)
            idle = role is None
            if unit_code:
                item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
                root_label = Gtk.Label(label=shown, xalign=0.0)
                root_label.set_css_classes(["root", ROLE_CLASSES[role]] if not idle else ["root", "root-idle"])
                item.append(root_label)
                code_label = Gtk.Label(label=unit_code, xalign=0.0)
                code_label.set_css_classes(["rootcode-idle"] if idle else ["rootcode", ROLE_CLASSES[role]])
                code_label.set_valign(Gtk.Align.END)
                item.append(code_label)
                tip = f"{unit} → {unit_code}"
            elif unit:
                item = Gtk.Label(label=shown, xalign=0.0)
                item.set_css_classes(["root", ROLE_CLASSES[role]] if not idle else ["root", "root-idle"])
                tip = f"{unit} → （数据中缺该字根的码）"
            else:
                continue
            if idle:
                tip = f"{tip}（未参与全码取码）"
            elif took:
                tip = f"{tip}（全码取 {took}）"
            else:
                tip = f"{tip}（{ROLE_LABELS[role]}）"
            if note:
                tip = f"{tip}\n{note}"
            item.set_tooltip_text(tip)
            flow.append(item)

        if total > 1:
            wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            tag = Gtk.Label(label=f"拆法{index + 1}", xalign=0.0)
            tag.set_css_classes(["vsep"])
            tag.set_valign(Gtk.Align.CENTER)
            wrap.append(tag)
            wrap.append(flow)
            return wrap
        return flow

    def _describe_full_code(self, code: str, owners: list[int | None], variant: Decomp) -> str:
        """全码每个字母的来历，用在悬停提示里（variant 必须与 owners 同源）。"""
        parts = []
        for index, letter in enumerate(code):
            owner = owners[index]
            if owner is not None and owner < len(variant):
                parts.append(f"{letter}={variant[owner][0]}")
            else:
                parts.append(f"{letter}=补码")
        return "全码 " + code + "：" + "、".join(parts)

    def _make_code_row(
        self,
        code: str,
        weight: int,
        owners: list[int | None] | None = None,
        roles_note: str = "",
    ) -> Gtk.Widget:
        button = Gtk.Button()
        button.set_css_classes(["flat", "codebtn"])
        tip = f"点击复制 {code}"
        if owners is not None and roles_note:
            tip = f"{tip}\n{roles_note}"
        button.set_tooltip_text(tip)
        button.connect("clicked", self._copy_code, code)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        code_label = Gtk.Label(xalign=0.0)
        code_label.set_css_classes(["code"])
        if owners is None:
            code_label.set_text(code)
        else:
            code_label.set_markup(self._code_markup(code, owners))
        inner.append(code_label)

        weight_label = Gtk.Label(label=f"权重 {weight}", xalign=1.0, hexpand=True)
        weight_label.set_css_classes(["weight"])
        inner.append(weight_label)

        button.set_child(inner)
        return button

    @staticmethod
    def _code_markup(code: str, owners: list[int | None]) -> str:
        """按取码序号逐字母着色；补码用中性灰。"""
        parts = []
        for index, letter in enumerate(code):
            safe = GLib.markup_escape_text(letter)
            owner = owners[index] if index < len(owners) else None
            if owner is None:
                color = PAD_CODE_COLOR
            else:
                color = ROLE_COLORS[owner % len(ROLE_COLORS)]
            parts.append(f'<span foreground="{color}">{safe}</span>')
        return "".join(parts)

    def _copy_code(self, _button: Gtk.Button, code: str) -> None:
        self.get_clipboard().set(code)
        self._flash_status(f"已复制编码：{code}（文本 UTF-8）")

    def _flash_status(self, message: str) -> None:
        if self._status_reset_id:
            GLib.source_remove(self._status_reset_id)
        self.status.set_text(message)
        self._status_reset_id = GLib.timeout_add(2500, self._restore_status)

    def _restore_status(self) -> bool:
        self._status_reset_id = 0
        self.status.set_text(self._load_summary)
        return GLib.SOURCE_REMOVE


class ZmApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        txt_path, yaml_path = find_data_files()
        self.zdict = ZmDict(txt_path, yaml_path)
        self.window: ZmWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = ZmWindow(self, self.zdict)
        self.window.present()


def main(argv: list[str]) -> int:
    app = ZmApp()
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
