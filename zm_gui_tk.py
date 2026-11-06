#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""郑码编码查询 GUI —— Tkinter 界面（Windows / macOS / Linux 通用）。

Tkinter 是 Python 自带的，无需额外安装依赖，所以在 Windows 上开箱即用。
取码、着色等规则都在 zm_core 里，这里只负责画界面。

界面结构与 GTK 版（zm_gui_gtk.py）保持一致：
    卡片 = 大字 + 拆字方案（逐字根「字根 + 它自己的码」）+ 编码列表（点击复制）
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import font as tkfont_alias

from zm_core import (
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

PLACEHOLDER = "输入汉字，可输入多个，如：羽好"

# 浅色 / 深色两套配色，尽量贴近 GTK 版在 GNOME 下的观感
LIGHT_PALETTE = {
    "bg": "#fafafa",
    "card": "#f0f0f0",
    "card_border": "#e3e3e3",
    "fg": "#2f2f2f",
    "muted": "#6e6e6e",
    "faint": "#9a9a9a",
    "idle": "#a9a9a9",
    "button": "#e9e9e9",
    "button_hover": "#dcdcdc",
    "button_border": "#dcdcdc",
    "entry_bg": "#ffffff",
    "entry_fg": "#2f2f2f",
    "placeholder": "#a6a6a6",
    "separator": "#e0e0e0",
    "scroll_bg": "#f0f0f0",
    "scroll_fg": "#c0c0c0",
}

DARK_PALETTE = {
    "bg": "#242424",
    "card": "#2f2f2f",
    "card_border": "#3b3b3b",
    "fg": "#e6e6e6",
    "muted": "#a6a6a6",
    "faint": "#8a8a8a",
    "idle": "#7f7f7f",
    "button": "#333333",
    "button_hover": "#414141",
    "button_border": "#474747",
    "entry_bg": "#1e1e1e",
    "entry_fg": "#e6e6e6",
    "placeholder": "#808080",
    "separator": "#3a3a3a",
    "scroll_bg": "#2a2a2a",
    "scroll_fg": "#4a4a4a",
}

# 中文字形优先用这些字体（Windows 上一般用微软雅黑）
CJK_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "SimHei",
    "SimSun",
    "Segoe UI",
)
MONO_FAMILIES = (
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Menlo",
    "Courier New",
    "NSimSun",
)


def pick_family(candidates: tuple[str, ...]) -> str:
    """从候选字体里挑一个系统里真实存在的；都没有就用 Tk 默认字体。"""
    try:
        available = set(tkfont.families())
    except tk.TclError:  # pragma: no cover - 没有显示器时才会发生
        available = set()
    for name in candidates:
        if name in available:
            return name
    return "TkDefaultFont"


def system_prefers_dark() -> bool | None:
    """系统是否偏好深色；完全拿不到时返回 None。

    Windows 读注册表里「应用主题」的明暗开关；其他平台看 GTK 的 settings.ini。
    """
    if sys.platform == "win32":
        try:
            import winreg
        except ImportError:  # pragma: no cover
            return None
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        except OSError:
            return None
        return not bool(value)

    # 其它平台：读 GTK 4 的用户配置
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    for path in (
        os.path.join(base, "gtk-4.0", "settings.ini"),
        os.path.join(base, "gtk-3.0", "settings.ini"),
    ):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("prefer-dark"):
                return "true" in line.split("=", 1)[-1].strip().lower()
            if line.startswith("gtk-theme-name"):
                name = line.split("=", 1)[-1].strip().strip('"').lower()
                if name:
                    return "dark" in name
    return None


class Tooltip:
    """跟随鼠标的提示框（Canvas 上没有原生 tooltip）。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.window: tk.Toplevel | None = None
        self._after_id: str | None = None

    def show(self, text: str, x: int, y: int) -> None:
        self.hide()
        if not text:
            return
        self._after_id = self.root.after(400, lambda: self._popup(text, x, y))

    def _popup(self, text: str, x: int, y: int) -> None:
        self._after_id = None
        window = tk.Toplevel(self.root)
        window.wm_overrideredirect(True)
        try:
            window.wm_attributes("-topmost", True)
        except tk.TclError:  # pragma: no cover
            pass
        frame = tk.Frame(window, bg="#333333", bd=0)
        frame.pack()
        label = tk.Label(
            frame,
            text=text,
            justify="left",
            bg="#333333",
            fg="#f2f2f2",
            font=("TkDefaultFont", 9),
            padx=8,
            pady=5,
        )
        label.pack()
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        pos_x = self.root.winfo_pointerx() + 12
        pos_y = self.root.winfo_pointery() + 18
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        pos_x = min(pos_x, max(0, screen_w - width - 8))
        pos_y = min(pos_y, max(0, screen_h - height - 8))
        window.geometry(f"+{pos_x}+{pos_y}")

    def hide(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._after_id = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class ZmTkApp:
    """Tkinter 版主窗口。"""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("郑码编码查询")
        self.root.geometry("760x680")
        self.root.minsize(560, 460)

        self.zdict = ZmDict(*find_data_files())
        self._theme_mode = load_theme_mode()
        self._palette = LIGHT_PALETTE
        self._dark: bool | None = None
        self._result: tuple[list[tuple[str, int]], list[tuple[str, list[Decomp], list[tuple[str, int]]]]] = (
            [],
            [],
        )
        self._debounce_id: str | None = None
        self._status_reset_id: str | None = None
        self._resize_id: str | None = None
        self._load_summary = ""
        self._tips: dict[str, str] = {}
        self._tip_counter = 0
        self._code_buttons: list[dict] = []
        self._hovered: dict | None = None
        self._events: queue.Queue = queue.Queue()

        self._make_fonts()
        self._build_ui()
        self.tooltip = Tooltip(self.root)
        self._apply_theme(self._theme_mode)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.root.after(100, self._poll_system_theme)
        self.root.after(50, self._drain_events)
        self._load_worker()

    # ---------- 字体与配色 ----------

    def _make_fonts(self) -> None:
        family = pick_family(CJK_FAMILIES)
        mono = pick_family(MONO_FAMILIES)
        self.font_char = tkfont_alias.Font(root=self.root, family=family, size=22, weight="bold")
        self.font_root = tkfont_alias.Font(root=self.root, family=family, size=13)
        self.font_rootcode = tkfont_alias.Font(root=self.root, family=mono, size=11)
        self.font_code = tkfont_alias.Font(root=self.root, family=mono, size=15, weight="bold")
        self.font_small = tkfont_alias.Font(root=self.root, family=family, size=9)
        self.font_status = tkfont_alias.Font(root=self.root, family=family, size=9)
        self.font_entry = tkfont_alias.Font(root=self.root, family=family, size=12)
        self.font_header = tkfont_alias.Font(root=self.root, family=family, size=12, weight="bold")
        self.font_button = tkfont_alias.Font(root=self.root, family=family, size=9)

    # ---------- 界面搭建 ----------

    def _build_ui(self) -> None:
        root = self.root
        root.configure(bg=self._palette["bg"])

        outer = tk.Frame(root, bg=self._palette["bg"])
        outer.pack(fill="both", expand=True)

        # 顶栏：标题 + 主题菜单
        header = tk.Frame(outer, bg=self._palette["bg"])
        header.pack(fill="x", padx=14, pady=(12, 6))
        title = tk.Label(
            header,
            text="郑码编码查询",
            bg=self._palette["bg"],
            fg=self._palette["fg"],
            font=self.font_header,
            anchor="w",
        )
        title.pack(side="left")

        self.theme_var = tk.StringVar(value=self._theme_mode)
        self.theme_button = tk.Menubutton(
            header,
            text="主题",
            bg=self._palette["bg"],
            fg=self._palette["fg"],
            activebackground=self._palette["button_hover"],
            activeforeground=self._palette["fg"],
            font=self.font_button,
            relief="flat",
            highlightthickness=0,
            padx=6,
        )
        self.theme_button.pack(side="right")
        menu = tk.Menu(
            self.theme_button,
            tearoff=0,
            bg=self._palette["card"],
            fg=self._palette["fg"],
            activebackground=self._palette["button_hover"],
            activeforeground=self._palette["fg"],
            font=self.font_button,
        )
        for mode in THEME_MODES:
            menu.add_radiobutton(
                label=THEME_LABELS[mode],
                variable=self.theme_var,
                value=mode,
                command=lambda m=mode: self._set_theme(m),
            )
        self.theme_button.configure(menu=menu)
        self.theme_menu = menu

        # 搜索框
        entry_frame = tk.Frame(outer, bg=self._palette["bg"])
        entry_frame.pack(fill="x", padx=14, pady=(0, 8))
        self.entry = tk.Entry(
            entry_frame,
            font=self.font_entry,
            bg=self._palette["entry_bg"],
            fg=self._palette["entry_fg"],
            insertbackground=self._palette["entry_fg"],
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=self._palette["separator"],
            highlightcolor=self._palette["separator"],
        )
        self.entry.pack(fill="x")
        self.entry.insert(0, PLACEHOLDER)
        self.entry.configure(fg=self._palette["placeholder"])
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<Return>", lambda _e: self._run_query())
        self.entry.bind("<KeyRelease>", self._on_key_release)
        self.entry.bind("<MouseWheel>", self._on_mousewheel)
        self.entry.bind("<Button-4>", self._on_mousewheel)
        self.entry.bind("<Button-5>", self._on_mousewheel)

        # 状态行
        self.status = tk.Label(
            outer,
            text="正在加载码表…",
            bg=self._palette["bg"],
            fg=self._palette["faint"],
            font=self.font_status,
            anchor="w",
        )
        self.status.pack(fill="x", padx=14, pady=(0, 6))

        # 结果区：Canvas + 滚动条
        body = tk.Frame(outer, bg=self._palette["bg"])
        body.pack(fill="both", expand=True, padx=(14, 14), pady=(0, 12))
        self.scrollbar = tk.Scrollbar(
            body,
            orient="vertical",
            command=self._on_scroll,
            bd=0,
            relief="flat",
            width=12,
        )
        self.scrollbar.pack(side="right", fill="y", padx=(6, 0))
        self.canvas = tk.Canvas(
            body,
            bg=self._palette["bg"],
            highlightthickness=0,
            bd=0,
            yscrollcommand=self.scrollbar.set,
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollincrement=40)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)

    # ---------- 主题 ----------

    def _apply_theme(self, mode: str) -> None:
        if mode == "system":
            dark = bool(system_prefers_dark())
        else:
            dark = mode == "dark"
        if dark == self._dark:
            # 明暗没变就不重画，省得每 15 秒把滚动位置冲掉
            self._dark = dark
            return
        self._dark = dark
        pal = DARK_PALETTE if dark else LIGHT_PALETTE
        self._palette = pal

        self.root.configure(bg=pal["bg"])
        for widget in self.root.winfo_children():
            self._recolor(widget)
        self.canvas.configure(bg=pal["bg"])
        self.status.configure(bg=pal["bg"], fg=pal["faint"])
        self.entry.configure(bg=pal["entry_bg"], fg=pal["entry_fg"])
        if self.entry.get() == PLACEHOLDER:
            self.entry.configure(fg=pal["placeholder"])
        self.theme_button.configure(
            bg=pal["bg"], fg=pal["fg"], activebackground=pal["button_hover"]
        )
        self.theme_menu.configure(
            bg=pal["card"], fg=pal["fg"], activebackground=pal["button_hover"],
            activeforeground=pal["fg"],
        )
        self.scrollbar.configure(
            bg=pal["scroll_bg"], troughcolor=pal["bg"], activebackground=pal["scroll_fg"]
        )
        self._render()

    def _recolor(self, widget: tk.Misc) -> None:
        """递归把背景/前景色换成当前主题。"""
        pal = self._palette
        try:
            if isinstance(widget, (tk.Frame, tk.Label)):
                widget.configure(bg=pal["bg"])
            if isinstance(widget, tk.Label):
                widget.configure(fg=pal["fg"])
        except tk.TclError:  # pragma: no cover
            pass
        for child in widget.winfo_children():
            self._recolor(child)

    def _set_theme(self, mode: str) -> None:
        if mode not in THEME_MODES:
            return
        self._theme_mode = mode
        self.theme_var.set(mode)
        self._apply_theme(mode)
        save_theme_mode(mode)

    def _poll_system_theme(self) -> None:
        """「跟随系统」时定期看看系统明暗是不是变了。"""
        if self._theme_mode == "system":
            self._apply_theme("system")
        self.root.after(15000, self._poll_system_theme)

    # ---------- 数据加载 ----------

    def _load_worker(self) -> None:
        """后台线程读码表，读完通过队列通知主线程。

        Tkinter 不是线程安全的，不能在工作线程里直接调 root.after()，
        所以只往队列里塞消息，由主线程的 _drain_events 统一取。
        """

        def worker() -> None:
            try:
                self.zdict.load()
            except OSError as exc:
                self._events.put(("failed", str(exc)))
                return
            self._events.put(("loaded", None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        """主线程侧的事件泵：处理后台线程的消息。"""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "loaded":
                    self._on_loaded()
                elif kind == "failed":
                    self._on_load_failed(payload)
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def _on_loaded(self) -> None:
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
        self.status.configure(text=self._load_summary)
        self.entry.focus_set()

    def _on_load_failed(self, message: str) -> None:
        self.status.configure(text=f"加载失败：{message}")

    # ---------- 查询 ----------

    def _on_key_release(self, _event: object) -> None:
        if self._debounce_id is not None:
            self.root.after_cancel(self._debounce_id)
        self._debounce_id = self.root.after(250, self._run_query)

    def _run_query(self) -> None:
        self._debounce_id = None
        text = self.entry.get()
        if text == PLACEHOLDER:
            text = ""
        if not self.zdict.loaded:
            return
        phrase_codes, chars = self.zdict.query(text)
        self._result = (phrase_codes, chars)
        self._render()

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        if self._resize_id is not None:
            self.root.after_cancel(self._resize_id)
        self._resize_id = self.root.after(150, self._render)

    # ---------- 绘制 ----------

    def _render(self) -> None:
        self._resize_id = None
        canvas = self.canvas
        canvas.delete("all")
        self._tips.clear()
        self._tip_counter = 0
        self._code_buttons.clear()
        self._hovered = None

        phrase_codes, chars = self._result
        width = max(canvas.winfo_width(), 420)
        y = 6
        if phrase_codes:
            word = "".join(self.entry.get().split())
            y = self._draw_card(word, None, phrase_codes, "词语", width, y)
        for char, variants, codes in chars:
            y = self._draw_card(char, variants, codes, "拆字方案", width, y)
        canvas.configure(scrollregion=(0, 0, width, y + 6))

    def _rounded_rect(self, x1: float, y1: float, x2: float, y2: float, radius: float = 10, **kw):
        """用平滑多边形画圆角矩形（Canvas 没有原生圆角矩形）。"""
        r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, splinesteps=16, **kw)

    def _tip_tag(self, text: str) -> str:
        self._tip_counter += 1
        tag = f"tip:{self._tip_counter}"
        self._tips[tag] = text
        return tag

    def _draw_card(
        self,
        char: str,
        variants: list[Decomp] | None,
        codes: list[tuple[str, int]],
        title_text: str,
        width: float,
        top: float,
    ) -> float:
        pal = self._palette
        canvas = self.canvas
        pad = 14
        card_x0 = 4
        card_x1 = width - 4
        inner_x = card_x0 + pad
        inner_right = card_x1 - pad

        analysis = analyze_codes(variants, codes) if variants is not None else analyze_codes([], codes)
        full = analysis.full
        contribs = analysis.contribs
        best = analysis.best
        owners = analysis.owners
        owner_ranks = analysis.owner_ranks

        # ---- 拆字方案：先把每一套拆法拆成若干「字根 + 它的码」，再按宽度折行 ----
        rows: list[list[dict]] = []  # 每行是一组条目
        for index, variant in enumerate(variants or []):
            per_root, ranks = contribs[index] if index < len(contribs) else (None, None)
            roles = ranks if ranks is not None else root_roles(len(variant))
            items: list[dict] = []
            if len(variants) > 1:
                items.append({"kind": "tag", "text": f"拆法{index + 1}", "tip": ""})
            for position, ((unit, unit_code), role) in enumerate(zip(variant, roles)):
                took = (
                    per_root[position]
                    if per_root is not None and position < len(per_root)
                    else None
                )
                shown, note = root_display(unit)
                idle = role is None
                color = pal["idle"] if idle else ROLE_COLORS[role]
                if unit_code:
                    tip = f"{unit} → {unit_code}"
                elif unit:
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
                items.append(
                    {
                        "kind": "root",
                        "text": shown,
                        "code": unit_code,
                        "color": color,
                        "idle": idle,
                        "tip": tip,
                    }
                )
            if not items:
                continue
            for line in self._wrap_items(items, inner_right - inner_x, card_x0 + pad):
                rows.append(line)

        missing = variants is not None and not variants

        # ---- 计算各块高度 ----
        char_h = self.font_char.metrics("linespace")
        title_h = self.font_small.metrics("linespace")
        row_h = max(self.font_root.metrics("linespace"), self.font_rootcode.metrics("linespace"))
        code_row_h = max(self.font_code.metrics("linespace"), self.font_small.metrics("linespace")) + 14

        info_lines = len(rows) + 1 if rows or missing else 1
        if missing:
            info_lines = 1  # 「zm.txt 中未找到该字」占一行
        head_h = max(char_h, title_h + (len(rows) + (1 if missing else 0)) * (row_h + 3))

        # ---- 先算卡片高度，再画底板 ----
        code_rows: list[tuple[str, int, list[int | None] | None]] = []
        if codes:
            roles_note = ""
            if full and variants and owners:
                roles_note = describe_full_code(full, owners, variants[best])
            colored = owner_ranks if any(rank is not None for rank in owner_ranks) else None
            for code, weight in codes:
                code_rows.append((code, weight, colored if code == full else None))
        else:
            roles_note = ""

        codes_h = len(code_rows) * code_row_h + (len(code_rows) - 1) * 4 if code_rows else 0
        missing_h = 0 if codes else self.font_small.metrics("linespace") + 4
        card_h = pad + head_h + (codes_h + missing_h + 10 if (codes_h or missing_h) else 0) + pad

        self._rounded_rect(
            card_x0,
            top,
            card_x1,
            top + card_h,
            radius=10,
            fill=pal["card"],
            outline=pal["card_border"],
            width=1,
        )

        # ---- 大字 ----
        if char:
            canvas.create_text(
                inner_x,
                top + pad + 2,
                text=char,
                font=self.font_char,
                fill=pal["fg"],
                anchor="nw",
            )

        # ---- 右侧信息列 ----
        info_x = inner_x + max(self.font_char.measure(char), 40) + 12
        y = top + pad
        canvas.create_text(
            info_x,
            y,
            text=title_text,
            font=self.font_small,
            fill=pal["muted"],
            anchor="nw",
        )
        y += title_h + 2

        if missing:
            canvas.create_text(
                info_x,
                y,
                text="zm.txt 中未找到该字",
                font=self.font_small,
                fill=pal["faint"],
                anchor="nw",
            )
            y += row_h + 3
        else:
            for line in rows:
                x = info_x
                for item in line:
                    if item["kind"] == "tag":
                        canvas.create_text(
                            x,
                            y + (row_h - self.font_small.metrics("linespace")) / 2,
                            text=item["text"],
                            font=self.font_small,
                            fill=pal["faint"],
                            anchor="nw",
                        )
                        x += self.font_small.measure(item["text"]) + 8
                        continue
                    tag = self._tip_tag(item["tip"])
                    canvas.create_text(
                        x,
                        y,
                        text=item["text"],
                        font=self.font_root,
                        fill=item["color"],
                        anchor="nw",
                        tags=(tag,),
                    )
                    x += self.font_root.measure(item["text"]) + 1
                    if item["code"]:
                        canvas.create_text(
                            x,
                            y + (row_h - self.font_rootcode.metrics("linespace")) / 2,
                            text=item["code"],
                            font=self.font_rootcode,
                            fill=item["color"] if not item["idle"] else pal["idle"],
                            anchor="nw",
                            tags=(tag,),
                        )
                        x += self.font_rootcode.measure(item["code"])
                    x += 8
                y += row_h + 3

        # ---- 编码列表 ----
        y = top + pad + head_h + 6
        if code_rows:
            for code, weight, colored in code_rows:
                rect = self._rounded_rect(
                    inner_x,
                    y,
                    inner_right,
                    y + code_row_h,
                    radius=8,
                    fill=pal["button"],
                    outline=pal["button_border"],
                    width=1,
                )
                tip = f"点击复制 {code}"
                if colored and roles_note:
                    tip = f"{tip}\n{roles_note}"
                tag = self._tip_tag(tip)
                cx = inner_x + 10
                if colored:
                    for letter, color in zip(code, code_letter_colors(code, colored)):
                        canvas.create_text(
                            cx,
                            y + (code_row_h - self.font_code.metrics("linespace")) / 2,
                            text=letter,
                            font=self.font_code,
                            fill=color,
                            anchor="nw",
                            tags=(tag,),
                        )
                        cx += self.font_code.measure(letter)
                else:
                    canvas.create_text(
                        cx,
                        y + (code_row_h - self.font_code.metrics("linespace")) / 2,
                        text=code,
                        font=self.font_code,
                        fill=pal["fg"],
                        anchor="nw",
                        tags=(tag,),
                    )
                canvas.create_text(
                    inner_right - 10,
                    y + (code_row_h - self.font_small.metrics("linespace")) / 2,
                    text=f"权重 {weight}",
                    font=self.font_small,
                    fill=pal["faint"],
                    anchor="ne",
                )
                self._code_buttons.append(
                    {
                        "item": rect,
                        "bbox": (inner_x, y, inner_right, y + code_row_h),
                        "code": code,
                        "tip": tip,
                    }
                )
                y += code_row_h + 4
        else:
            canvas.create_text(
                inner_x,
                y,
                text="zm.yaml 中无编码记录",
                font=self.font_small,
                fill=pal["faint"],
                anchor="nw",
            )

        return top + card_h + 10

    def _wrap_items(self, items: list[dict], max_width: float, _left: float) -> list[list[dict]]:
        """把字根条目按可用宽度折行（对应 GTK 版的 FlowBox）。"""
        lines: list[list[dict]] = []
        current: list[dict] = []
        width = 0.0
        for item in items:
            if item["kind"] == "tag":
                item_width = self.font_small.measure(item["text"]) + 8
            else:
                item_width = (
                    self.font_root.measure(item["text"])
                    + (1 + self.font_rootcode.measure(item["code"]) if item["code"] else 0)
                    + 8
                )
            if current and (width + item_width > max_width or len(current) >= 24):
                lines.append(current)
                current = []
                width = 0.0
            current.append(item)
            width += item_width
        if current:
            lines.append(current)
        return lines

    # ---------- 交互 ----------

    def _on_focus_in(self, _event: object) -> None:
        if self.entry.get() == PLACEHOLDER:
            self.entry.delete(0, tk.END)
            self.entry.configure(fg=self._palette["entry_fg"])

    def _on_focus_out(self, _event: object) -> None:
        if not self.entry.get():
            self.entry.insert(0, PLACEHOLDER)
            self.entry.configure(fg=self._palette["placeholder"])

    def _on_scroll(self, *args: str) -> None:
        self.canvas.yview(*args)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if sys.platform == "win32":
            delta = -1 if event.delta > 0 else 1
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = 0
        if delta:
            self.canvas.yview_scroll(delta, "units")

    def _on_motion(self, event: tk.Event) -> None:
        canvas = self.canvas
        canvas_x = canvas.canvasx(event.x)
        canvas_y = canvas.canvasy(event.y)

        hovered = None
        for button in self._code_buttons:
            x1, y1, x2, y2 = button["bbox"]
            if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                hovered = button
                break
        if hovered is not self._hovered:
            pal = self._palette
            for button in self._code_buttons:
                canvas.itemconfigure(
                    button["item"],
                    fill=pal["button_hover"] if button is hovered else pal["button"],
                )
            self._hovered = hovered

        tip = None
        if hovered is not None:
            tip = hovered["tip"]
        else:
            item = canvas.find_withtag("current")
            if item:
                for tag in canvas.gettags(item[0]):
                    if tag.startswith("tip:"):
                        tip = self._tips.get(tag)
                        break
        if tip:
            self.tooltip.show(tip, event.x_root, event.y_root)
        else:
            self.tooltip.hide()

    def _on_leave(self, _event: object) -> None:
        self.tooltip.hide()

    def _on_click(self, event: tk.Event) -> None:
        canvas = self.canvas
        canvas_x = canvas.canvasx(event.x)
        canvas_y = canvas.canvasy(event.y)
        for button in self._code_buttons:
            x1, y1, x2, y2 = button["bbox"]
            if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                self._copy_code(button["code"])
                return

    def _copy_code(self, code: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.root.update()  # 让 Tk 真正把内容交给系统剪贴板
        self._flash_status(f"已复制编码：{code}（文本 UTF-8）")

    def _flash_status(self, message: str) -> None:
        if self._status_reset_id is not None:
            self.root.after_cancel(self._status_reset_id)
        self.status.configure(text=message)
        self._status_reset_id = self.root.after(2500, self._restore_status)

    def _restore_status(self) -> None:
        self._status_reset_id = None
        self.status.configure(text=self._load_summary)

    def _on_close(self) -> None:
        save_theme_mode(self._theme_mode)
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main(argv: list[str]) -> int:
    return ZmTkApp().run()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
