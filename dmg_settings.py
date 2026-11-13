# -*- coding: utf-8 -*-
"""
dmg_settings.py — macOS 标准拖拽安装型 .dmg 磁盘映像布局配置
基于 dmgbuild，呈现极简纯白底色的拖拽安装窗口（类似 FlClash 视觉设计）。
"""
import os

# ── 卷宗名称与挂载图标 ──────────────────────────────────────────
volume_name = "YouTube 下载器"

# 自动匹配项目中的 app.icns，使挂载后的磁盘卷宗显示为软件图标
_icns_candidates = [
    "app.icns",
    "assets/app.icns",
    os.path.join(os.getcwd(), "app.icns"),
]
_found_icns = None
for c in _icns_candidates:
    if os.path.isfile(c):
        _found_icns = os.path.abspath(c)
        break

if _found_icns:
    badge_icon = _found_icns
    icon = _found_icns

# ── 包含内容与替身 ────────────────────────────────────────────
files = ["dist/YouTube下载器.app"]
symlinks = {"Applications": "/Applications"}

# ── 窗口外观与尺寸（极简纯白底色，隐藏多余工具栏） ───────────
# 注：dmgbuild 颜色解析器需使用小写十六进制 (#ffffff) 或英文名称 ('white')
background = "#ffffff"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False

# 窗口坐标与尺寸: ((x, y), (width, height))
window_rect = ((300, 200), (600, 400))
default_view = "icon-view"

# ── 图标尺寸与左右对称坐标 ────────────────────────────────────
icon_size = 110
text_size = 13
icon_locations = {
    "YouTube下载器.app": (180, 215),
    "Applications": (420, 215),
}
