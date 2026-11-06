#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zmcx —— 郑码编码查询，跨平台启动入口。

界面有两套，行为一致：
    gtk —— PyGObject + GTK 4，Linux 上的原生观感（zm_gui_gtk.py）
    tk  —— Tkinter，Python 自带，Windows / macOS / Linux 都能跑（zm_gui_tk.py）

默认自动挑：有 GTK 4 且有图形会话就用 GTK，否则用 Tkinter。
也可以用 --backend 强制指定。

用法：
    python zm_gui.py                  自动选择界面
    python zm_gui.py --backend tk     强制用 Tkinter
    python zm_gui.py --backend gtk    强制用 GTK 4
"""

from __future__ import annotations

import os
import sys

from zm_core import find_data_files

USAGE = """zmcx —— 郑码编码查询

用法：
  python zm_gui.py [--backend gtk|tk]

选项：
  --backend gtk   用 GTK 4 界面（需要 PyGObject + GTK 4）
  --backend tk    用 Tkinter 界面（Python 自带，Windows 默认走这个）
  -h, --help      显示本帮助

环境变量：
  ZM_DIR   数据文件（zm.txt、zm.yaml）所在目录，默认与脚本同目录
"""


def has_display() -> bool:
    """有没有图形会话（Linux 下没有 DISPLAY 就没法开窗口）。"""
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def gtk_available() -> bool:
    """这台机器上能不能用 GTK 4。"""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except Exception:
        return False
    return True


def tk_available() -> bool:
    """Python 里有没有带 Tkinter。"""
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def choose_backend(requested: str | None) -> str:
    """按优先级挑一个可用的界面后端。"""
    if requested in ("gtk", "tk"):
        if requested == "gtk" and not (gtk_available() and has_display()):
            raise SystemExit(
                "错误：无法使用 GTK 4 界面。\n"
                "  需要 PyGObject 与 GTK 4，并且处于图形会话中。\n"
                "  Debian / Ubuntu   sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0\n"
                "  Fedora            sudo dnf install python3-gobject gtk4\n"
                "  Arch Linux        sudo pacman -S python-gobject gtk4\n"
                "  只想立刻用起来？改用 Tkinter：python zm_gui.py --backend tk"
            )
        if requested == "tk" and not tk_available():
            raise SystemExit(
                "错误：当前 Python 没有 Tkinter。\n"
                "  Debian / Ubuntu   sudo apt install python3-tk\n"
                "  Windows            重新运行 python.org 的安装程序，勾选 tcl/tk 组件"
            )
        return requested

    if gtk_available() and has_display():
        return "gtk"
    if tk_available():
        return "tk"
    raise SystemExit(
        "错误：找不到可用的图形界面后端。\n"
        "  装 Tkinter：  Debian / Ubuntu  sudo apt install python3-tk\n"
        "  或装 GTK 4：  Debian / Ubuntu  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0"
    )


def check_data_files() -> str | None:
    """数据文件缺了就返回一段说明，供启动前给出中文提示。"""
    txt_path, yaml_path = find_data_files()
    missing = [path for path in (txt_path, yaml_path) if not os.path.isfile(path)]
    if not missing:
        return None
    lines = "\n".join(f"  {path}" for path in missing)
    return (
        f"缺少数据文件：\n{lines}\n"
        "这两个文件应当和 zm_gui.py 放在一起，也可用 ZM_DIR 环境变量指定所在目录。"
    )


def main(argv: list[str]) -> int:
    requested: str | None = None
    args = list(argv[1:])
    while args:
        arg = args.pop(0)
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg == "--backend":
            if not args:
                print(USAGE, file=sys.stderr)
                return 1
            requested = args.pop(0).lower()
        elif arg.startswith("--backend="):
            requested = arg.split("=", 1)[1].lower()
        else:
            print(f"未知参数：{arg}\n", file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return 1

    if requested is not None and requested not in ("gtk", "tk"):
        print(f"未知的界面后端：{requested}（可选 gtk 或 tk）\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 1

    backend = choose_backend(requested)

    problem = check_data_files()
    if problem:
        print(f"错误：{problem}", file=sys.stderr)
        return 2

    if backend == "gtk":
        from zm_gui_gtk import main as run
    else:
        from zm_gui_tk import main as run
    # 只把程序名传下去：--backend 是给本分发器看的，
    # Gtk.Application.run() 会用 GOption 解析参数，不认识它会直接报错
    return run([argv[0]])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
