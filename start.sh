#!/usr/bin/env bash
# zmcx —— 郑码编码查询，Linux 启动脚本
#
# 用法：
#   ./start.sh                      正常启动
#   ZM_DIR=/path/to/data ./start.sh 指定数据文件所在目录
#   PYTHON=python3.12 ./start.sh    指定解释器（默认 python3）
#
# 退出码：0 正常结束；1 环境不满足；2 缺少文件

set -euo pipefail

# 解析脚本真实位置，允许通过符号链接启动
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    LINK_DIR="$(cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
    SOURCE="$(readlink -- "$SOURCE")"
    case "$SOURCE" in
        /*) ;;
        *) SOURCE="$LINK_DIR/$SOURCE" ;;
    esac
done
SCRIPT_DIR="$(cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
APP="$SCRIPT_DIR/zm_gui.py"
PYTHON="${PYTHON:-python3}"

if [ -t 1 ]; then
    C_ERR=$'\033[31m'; C_INFO=$'\033[36m'; C_OFF=$'\033[0m'
else
    C_ERR=''; C_INFO=''; C_OFF=''
fi

info() { printf '%s%s%s\n' "$C_INFO" "$*" "$C_OFF"; }
die()  {
    local msg="$1" code="${2:-1}"
    printf '%s错误：%s%s\n' "$C_ERR" "$msg" "$C_OFF" >&2
    exit "$code"
}

usage() {
    cat <<'EOF'
zmcx —— 郑码编码查询

用法：./start.sh [--help]

环境变量：
  ZM_DIR   数据文件（zm.txt、zm.yaml）所在目录，默认与脚本同目录
  PYTHON   使用的解释器，默认 python3

示例：
  ./start.sh
  ZM_DIR=~/zmdata ./start.sh
  PYTHON=python3.12 ./start.sh
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

# 1. 解释器
command -v "$PYTHON" >/dev/null 2>&1 \
    || die "找不到 ${PYTHON}，请先安装 Python 3.9 或更高版本（或设置 PYTHON=/path/to/python3）"

python_ok() {
    "$PYTHON" - <<'PY'
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
}
python_ok || die "Python 版本过低（$(("$PYTHON" -c 'import sys;print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown))），需要 3.9 或更高"

# 2. PyGObject + GTK 4
gtk_ok() {
    "$PYTHON" - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
PY
}
if ! gtk_ok 2>/dev/null; then
    printf '%s错误：缺少 PyGObject 或 GTK 4。%s\n' "$C_ERR" "$C_OFF" >&2
    cat >&2 <<'EOF'
按发行版安装：

  Debian / Ubuntu   sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
  Fedora            sudo dnf install python3-gobject gtk4
  Arch Linux        sudo pacman -S python-gobject gtk4

若系统里装了多个 Python，请用 PYTHON 指定装好 PyGObject 的那个。

查看详细报错：

  python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk'
EOF
    exit 1
fi

# 3. 主程序与数据文件
[ -f "$APP" ] || die "找不到主程序：$APP" 2

DATA_DIR="${ZM_DIR:-$SCRIPT_DIR}"
for name in zm.txt zm.yaml; do
    [ -f "$DATA_DIR/$name" ] \
        || die "缺少数据文件 $DATA_DIR/$name（可用 ZM_DIR 指定所在目录）" 2
done

# 4. 图形环境
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    die "没有检测到图形环境（DISPLAY 与 WAYLAND_DISPLAY 都未设置）。这是 GUI 程序，需要图形会话。"
fi

# 5. 启动
info "启动 zmcx（数据：$DATA_DIR）"
exec "$PYTHON" "$APP" "$@"
