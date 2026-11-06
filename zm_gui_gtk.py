#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""郑码编码查询 GUI —— GTK 4 界面（Linux / BSD 等有 PyGObject 的平台）。

取码、着色等规则都在 zm_core 里，这里只负责画界面。
"""

from __future__ import annotations

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from zm_core import (  # noqa: E402
    APP_ID,
    ROLE_COLORS,
    ROLE_LABELS,
    THEME_LABELS,
    THEME_MODES,
    Decomp,
    ZmDict,
    analyze_codes,
    code_letter_colors,
    describe_full_code,
    find_data_files,
    load_theme_mode,
    root_display,
    root_roles,
    save_theme_mode,
)

ROLE_CLASSES = ("root-first", "root-second", "root-third", "root-last")

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

THEME_ICONS = {
    "system": "preferences-system-symbolic",
    "light": "weather-clear-symbolic",
    "dark": "weather-clear-night-symbolic",
}
SYSTEM_SCHEMA = "org.gnome.desktop.interface"


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
        analysis = analyze_codes(variants, codes)
        full = analysis.full
        contribs = analysis.contribs
        best = analysis.best
        owners = analysis.owners
        owner_ranks = analysis.owner_ranks

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
                roles_note = describe_full_code(full, owners, variants[best])
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
        for letter, color in zip(code, code_letter_colors(code, owners)):
            safe = GLib.markup_escape_text(letter)
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
