#!/usr/bin/env bash
# build.sh — 将 YouTube 下载器打包为独立 macOS .app 应用程序及 .dmg 磁盘映像
# 用法：bash build.sh

set -euo pipefail

# 修复 macOS Xcode CLI 架构冲突问题：优先指定 CommandLineTools
if [ -d "/Library/Developer/CommandLineTools" ]; then
    export DEVELOPER_DIR="/Library/Developer/CommandLineTools"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON=".venv/bin/python"

echo "=================================================="
echo "  YouTube 下载器 — macOS .app & .dmg 打包流水线"
echo "=================================================="

# ── 1. 检查虚拟环境与打包工具 ──────────────────────────────────
echo "[1/7] 检查虚拟环境与打包工具依赖…"
if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] 未找到虚拟环境，请先运行："
    echo "  bash setup.sh"
    exit 1
fi

UV="$HOME/.local/bin/uv"
ensure_pkg() {
    local mod="$1"
    local pkg="${2:-$1}"
    if ! "$PYTHON" -c "import $mod" 2>/dev/null; then
        echo "  -> 正在安装 $pkg…"
        if command -v uv &>/dev/null || [ -x "$UV" ]; then
            UV_CMD=$(command -v uv 2>/dev/null || echo "$UV")
            "$UV_CMD" pip install "$pkg" --python "$PYTHON" --quiet
        else
            "$PYTHON" -m pip install "$pkg" --quiet
        fi
    fi
}

ensure_pkg "PyInstaller" "pyinstaller"
ensure_pkg "dmgbuild" "dmgbuild"
echo "  -> PyInstaller 与 dmgbuild 准备就绪"

# ── 2. 图标检查与自动生成 (icon.png -> app.icns) ──────────────
echo "[2/7] 检查与准备应用程序图标…"
if [ ! -f "app.icns" ]; then
    if [ ! -f "icon.png" ]; then
        echo "  -> 未发现 icon.png，使用 Python PIL 自动生成默认应用图标…"
        "$PYTHON" -c "
from PIL import Image, ImageDraw
size = 1024
margin = 90
radius = 185
grad = Image.new('RGBA', (size, size), (0,0,0,0))
grad_draw = ImageDraw.Draw(grad)
for y in range(margin, size - margin):
    ratio = (y - margin) / float(size - 2 * margin)
    grad_draw.line([(margin, y), (size - margin, y)], fill=(int(245 - 35 * ratio), int(30 - 20 * ratio), int(30 - 20 * ratio), 255))
mask = Image.new('L', (size, size), 0)
mask_draw = ImageDraw.Draw(mask)
mask_draw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=radius, fill=255)
bg = Image.new('RGBA', (size, size), (0,0,0,0))
bg.paste(grad, (0,0), mask)
overlay = ImageDraw.Draw(bg)
tri_center_x, tri_center_y = 512, 440
overlay.polygon([(tri_center_x - 100, tri_center_y - 120), (tri_center_x - 100, tri_center_y + 120), (tri_center_x + 130, tri_center_y)], fill=(255, 255, 255, 255))
tray_y = 700
overlay.rounded_rectangle([360, tray_y, 664, tray_y + 36], radius=18, fill=(255, 255, 255, 240))
arrow_stem_x, arrow_top_y, arrow_bottom_y = 512, 570, 660
overlay.line([(arrow_stem_x, arrow_top_y), (arrow_stem_x, arrow_bottom_y)], fill=(255, 255, 255, 255), width=36)
overlay.polygon([(arrow_stem_x - 70, arrow_bottom_y - 20), (arrow_stem_x + 70, arrow_bottom_y - 20), (arrow_stem_x, arrow_bottom_y + 35)], fill=(255, 255, 255, 255))
bg.save('icon.png')
"
    fi

    echo "  -> 使用 sips 与 iconutil 将 icon.png 编译为 macOS 标准 app.icns…"
    rm -rf icon.iconset
    mkdir -p icon.iconset
    sips -z 16 16     icon.png --out icon.iconset/icon_16x16.png >/dev/null
    sips -z 32 32     icon.png --out icon.iconset/icon_16x16@2x.png >/dev/null
    sips -z 32 32     icon.png --out icon.iconset/icon_32x32.png >/dev/null
    sips -z 64 64     icon.png --out icon.iconset/icon_32x32@2x.png >/dev/null
    sips -z 128 128   icon.png --out icon.iconset/icon_128x128.png >/dev/null
    sips -z 256 256   icon.png --out icon.iconset/icon_128x128@2x.png >/dev/null
    sips -z 256 256   icon.png --out icon.iconset/icon_256x256.png >/dev/null
    sips -z 512 512   icon.png --out icon.iconset/icon_256x256@2x.png >/dev/null
    sips -z 512 512   icon.png --out icon.iconset/icon_512x512.png >/dev/null
    sips -z 1024 1024 icon.png --out icon.iconset/icon_512x512@2x.png >/dev/null
    iconutil -c icns icon.iconset -o app.icns
    rm -rf icon.iconset
    echo "  -> app.icns 图标生成成功"
else
    echo "  -> 已存在 app.icns，直接复用"
fi

# ── 3. 准备内置二进制权限 ──────────────────────────────────────
echo "[3/7] 检查内置二进制工具（ffmpeg, ffprobe, aria2c）…"
chmod 755 ffmpeg ffprobe aria2c 2>/dev/null || true

# ── 4. 清理旧构建目录 ──────────────────────────────────────────
echo "[4/7] 清理旧构建缓存…"
rm -rf build dist *.spec 2>/dev/null || true

# ── 5. 执行 PyInstaller 打包 ──────────────────────────────────
echo "[5/7] 执行 PyInstaller 打包为 macOS .app（预计需 1-2 分钟）…"

PYINSTALLER_ARGS=(
    --name "YouTube下载器"
    --windowed
    --noconfirm
    --osx-bundle-identifier "com.youtube.downloader"
    --icon "app.icns"
    --add-binary "ffmpeg:."
    --add-binary "ffprobe:."
    --add-binary "aria2c:."
    --collect-all customtkinter
    --collect-all yt_dlp
    --collect-all yt_dlp_ejs
    --hidden-import PIL
    --hidden-import static_ffmpeg
)

if [ -f "cookies.txt" ]; then
    echo "  -> 包含 cookies.txt 配置文件"
    PYINSTALLER_ARGS+=(--add-data "cookies.txt:.")
fi

"$PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}" main.py

# ── 6. 完善 .app 结构、权限与本地化配置 ────────────────────────
echo "[6/7] 配置 .app 本地化、执行权限与签名…"

APP_PATH="dist/YouTube下载器.app"
PLIST="$APP_PATH/Contents/Info.plist"

if [ -d "$APP_PATH" ]; then
    # 注入 macOS 简体中文属性，确保访达弹窗、右键菜单和系统交互为纯中文
    if [ -f "$PLIST" ]; then
        "$PYTHON" -c "
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
print('  -> 已成功配置 Info.plist 简体中文本地化')
"
    fi

    # 确保内部二进制工具具有可执行权限
    chmod -R +x "$APP_PATH/Contents/MacOS" 2>/dev/null || true
    find "$APP_PATH" -type f \( -name "ffmpeg" -o -name "ffprobe" -o -name "aria2c" \) -exec chmod +x {} + 2>/dev/null || true

    # Ad-hoc 签名以防止 macOS 安全拦截
    if command -v codesign &>/dev/null; then
        echo "  -> 正在应用 macOS 本地签名…"
        codesign --force --deep --sign - "$APP_PATH" 2>/dev/null || true
    fi
else
    echo "[ERROR] 未在 dist 目录下找到 YouTube下载器.app"
    exit 1
fi

# ── 7. 封装为 macOS 标准拖拽安装型 .dmg 磁盘映像 ────────────────
echo "[7/7] 封装为拖拽安装型 .dmg 磁盘映像…"
DMG_PATH="dist/YouTube下载器-macOS-arm64.dmg"
rm -f "$DMG_PATH"

echo "  -> 正在运行 dmgbuild 生成 .dmg 镜像…"
"$PYTHON" -m dmgbuild -s dmg_settings.py "YouTube 下载器" "$DMG_PATH"

if [ -f "$DMG_PATH" ]; then
    DMG_SIZE=$(ls -lh "$DMG_PATH" | awk '{print $5}')
    echo ""
    echo "=================================================="
    echo "  ✅ 全部打包成功！"
    echo "  独立应用程序: $SCRIPT_DIR/$APP_PATH"
    echo "  DMG 安装映像: $SCRIPT_DIR/$DMG_PATH ($DMG_SIZE)"
    echo "=================================================="
    echo ""
    echo "启动与安装方式："
    echo "  直接打开：open '$APP_PATH'"
    echo "  挂载安装：open '$DMG_PATH'"
    echo ""
else
    echo "[ERROR] 未能成功生成 .dmg 镜像"
    exit 1
fi
