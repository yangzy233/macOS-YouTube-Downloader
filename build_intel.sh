#!/usr/bin/env bash
# build_intel.sh — 构建兼容 Intel (x86_64) Mac 的独立 .app 与 .dmg 安装包
# 用法：bash build_intel.sh

set -euo pipefail

if [ -d "/Library/Developer/CommandLineTools" ]; then
    export DEVELOPER_DIR="/Library/Developer/CommandLineTools"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "  YouTube 下载器 — macOS Intel (x86_64) 打包流水线"
echo "=================================================="

# ── 1. 准备 x86_64 Python 虚拟环境 ────────────────────────────
echo "[1/6] 检查与准备 Intel (x86_64) Python 环境…"
UV="$HOME/.local/bin/uv"
if ! command -v uv &>/dev/null && [ ! -x "$UV" ]; then
    echo "[ERROR] 未检测到 uv，请先安装 uv 或运行 setup.sh"
    exit 1
fi
UV_CMD=$(command -v uv 2>/dev/null || echo "$UV")

INTEL_PY_BIN="$HOME/.local/share/uv/python/cpython-3.12-macos-x86_64-none/bin/python3.12"
if [ ! -f "$INTEL_PY_BIN" ]; then
    echo "  -> 正在通过 uv 下载安装 macOS x86_64 架构 Python…"
    "$UV_CMD" python install 3.12 --arch x86_64
fi

if [ ! -d ".venv-intel" ]; then
    echo "  -> 正在创建 .venv-intel 独立环境…"
    "$UV_CMD" venv .venv-intel --python "$INTEL_PY_BIN"
fi

echo "  -> 正在安装 Intel 运行与打包依赖…"
"$UV_CMD" pip install -r requirements.txt pyinstaller dmgbuild --python .venv-intel/bin/python --quiet

PYTHON_INTEL=".venv-intel/bin/python"
arch -x86_64 "$PYTHON_INTEL" -c "import platform, yt_dlp, customtkinter, dmgbuild, mutagen; print('  -> Intel Python 环境就绪，运行架构:', platform.machine())"

# ── 2. 检查与准备 Intel (x86_64) 静态编译二进制 ──────────────
echo "[2/6] 检查 Intel 二进制工具（ffmpeg, ffprobe, aria2c）…"
mkdir -p bin_intel

if [ ! -f "bin_intel/ffmpeg" ] || [ ! -f "bin_intel/ffprobe" ]; then
    echo "  -> 正在下载 Intel (x86_64) 静态编译 FFmpeg 套件…"
    curl -fL "https://github.com/zackees/ffmpeg_bins/raw/main/v8.0/darwin.zip" -o bin_intel/darwin.zip
    unzip -q -o bin_intel/darwin.zip -d bin_intel/
    rm -f bin_intel/darwin.zip
    mv bin_intel/darwin/ffmpeg bin_intel/ffmpeg
    mv bin_intel/darwin/ffprobe bin_intel/ffprobe
    rmdir bin_intel/darwin 2>/dev/null || true
fi

if [ ! -f "bin_intel/aria2c" ]; then
    echo "  -> 正在下载 Intel (x86_64) aria2c…"
    curl -fL "https://github.com/AnInsomniacy/aria2-next/releases/download/v3.1.0/aria2-next-3.1.0-macos-x86_64" -o bin_intel/aria2c
fi

chmod +x bin_intel/*

# ── 3. 构建 Intel (x86_64) .app ──────────────────────────────
echo "[3/6] 执行 PyInstaller 打包 Intel (x86_64) .app…"
rm -rf build_intel dist_intel *.spec 2>/dev/null || true

arch -x86_64 "$PYTHON_INTEL" -m PyInstaller \
    --name "YouTube下载器" \
    --windowed \
    --noconfirm \
    --target-arch x86_64 \
    --osx-bundle-identifier "com.youtube.downloader" \
    --icon "app.icns" \
    --add-binary "bin_intel/ffmpeg:." \
    --add-binary "bin_intel/ffprobe:." \
    --add-binary "bin_intel/aria2c:." \
    --collect-all customtkinter \
    --collect-all yt_dlp \
    --collect-all yt_dlp_ejs \
    --collect-all mutagen \
    --hidden-import PIL \
    --hidden-import static_ffmpeg \
    --distpath dist_intel \
    --workpath build_intel \
    main.py

APP_PATH="dist_intel/YouTube下载器.app"
PLIST="$APP_PATH/Contents/Info.plist"

# ── 4. 配置 Info.plist 与权限签名 ────────────────────────────
echo "[4/6] 配置 Info.plist 与权限签名…"
if [ -f "$PLIST" ]; then
    arch -x86_64 "$PYTHON_INTEL" -c "
import plistlib
from pathlib import Path

plist_path = Path('$PLIST')
with open(plist_path, 'rb') as f:
    pl = plistlib.load(f)

pl['CFBundleDevelopmentRegion'] = 'zh_CN'
pl['CFBundleAllowMixedLocalizations'] = True
pl['CFBundleLocalizations'] = ['zh_CN', 'zh-Hans']
pl['NSHighResolutionCapable'] = True

with open(plist_path, 'wb') as f:
    plistlib.dump(pl, f)
"
fi

chmod -R +x "$APP_PATH/Contents/MacOS" 2>/dev/null || true
find "$APP_PATH" -type f \( -name "ffmpeg" -o -name "ffprobe" -o -name "aria2c" \) -exec chmod +x {} + 2>/dev/null || true

if command -v codesign &>/dev/null; then
    codesign --force --deep --sign - "$APP_PATH" 2>/dev/null || true
fi

# ── 5. 校验 Intel 二进制架构 ──────────────────────────────────
echo "[5/6] 校验架构完整性（必须全部包含 x86_64）…"
file "$APP_PATH/Contents/MacOS/YouTube下载器"
file "$APP_PATH/Contents/Resources/ffmpeg" 2>/dev/null || file "$APP_PATH/Contents/MacOS/ffmpeg" 2>/dev/null || true

# ── 6. 封装为拖拽安装型 .dmg 磁盘映像 ──────────────────────────
echo "[6/6] 封装 Intel 版 .dmg 磁盘映像…"
mkdir -p dist
DMG_PATH="dist/YouTube下载器-macOS-intel.dmg"
rm -f "$DMG_PATH"

cat << 'EOF' > dmg_settings_intel.py
# -*- coding: utf-8 -*-
import os

volume_name = "YouTube 下载器"
if os.path.isfile("app.icns"):
    badge_icon = os.path.abspath("app.icns")
    icon = os.path.abspath("app.icns")

files = ["dist_intel/YouTube下载器.app"]
symlinks = {"Applications": "/Applications"}
background = "#ffffff"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
window_rect = ((300, 200), (600, 400))
default_view = "icon-view"
icon_size = 110
text_size = 13
icon_locations = {
    "YouTube下载器.app": (180, 215),
    "Applications": (420, 215),
}
EOF

arch -x86_64 "$PYTHON_INTEL" -m dmgbuild -s dmg_settings_intel.py "YouTube 下载器" "$DMG_PATH"
rm -f dmg_settings_intel.py
rm -rf build_intel dist_intel 2>/dev/null || true

echo ""
echo "=================================================="
echo "  ✅ Intel 版本构建成功！"
echo "  DMG 文件: $SCRIPT_DIR/$DMG_PATH ($(ls -lh "$DMG_PATH" | awk '{print $5}'))"
echo "=================================================="
