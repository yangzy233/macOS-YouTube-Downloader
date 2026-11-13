#!/usr/bin/env bash
# setup.sh — 一键完成虚拟环境创建和依赖安装
# 推荐使用 uv（无需 Xcode/编译器）：curl -LsSf https://astral.sh/uv/install.sh | sh
# 用法：bash setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

UV="$HOME/.local/bin/uv"

# ── 优先使用 uv（最快、无需 Xcode）────────────────────────────
if command -v uv &>/dev/null || [ -x "$UV" ]; then
    UV_CMD=$(command -v uv 2>/dev/null || echo "$UV")
    echo "[✓] 使用 uv: $($UV_CMD --version)"

    if [ ! -d ".venv" ]; then
        echo "正在创建虚拟环境 .venv (Python 3.12)…"
        "$UV_CMD" venv .venv --python 3.12
    else
        echo "[✓] 虚拟环境已存在，跳过创建"
    fi

    echo "正在安装依赖包…"
    "$UV_CMD" pip install -r requirements.txt --python .venv/bin/python

    echo ""
    echo "✅ 安装完成！启动程序："
    echo "   source .venv/bin/activate && python main.py"
    echo "   # 或直接："
    echo "   .venv/bin/python main.py"
    exit 0
fi

# ── 回退：使用系统 Python（需要系统 Python 可用）──────────────
echo "[!] 未找到 uv，尝试系统 Python…"
echo "    推荐先安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(sys.version_info[:2])" 2>/dev/null || echo "")
        if [[ -n "$VER" ]]; then
            PYTHON="$candidate"
            echo "[✓] 使用 Python：$PYTHON ($VER)"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo ""
    echo "[ERROR] 未找到可用的 Python 3。请先安装 uv："
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "   source ~/.local/bin/env  # 或重开终端"
    echo "   bash setup.sh"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "正在创建虚拟环境 .venv …"
    "$PYTHON" -m venv .venv
else
    echo "[✓] 虚拟环境已存在，跳过创建"
fi

source .venv/bin/activate
echo "正在安装依赖包…"
pip install --upgrade pip --quiet
pip install -r requirements.txt

echo ""
echo "✅ 安装完成！启动程序："
echo "   source .venv/bin/activate && python main.py"
