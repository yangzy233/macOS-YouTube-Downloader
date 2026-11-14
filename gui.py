"""
gui.py — 主界面模块
遵循 macOS Apple Human Interface Guidelines (HIG) 原生极简质感设计规范。
支持单视频/播放列表/多链接批量解析与下载队列，并在画质选择旁实时展示预估体积。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional
import webbrowser

import customtkinter as ctk

from config import INTERNAL_COOKIE_PATH, load_settings, save_settings, update_setting
from downloader import (
    DownloadTask,
    clear_current_cookie,
    extract_video_urls,
    format_size,
    get_browser_cookie,
    get_current_cookie_file,
    get_video_info,
    has_valid_cookie,
    parse_playlist_or_videos,
    save_imported_cookie,
    set_browser_cookie,
    set_current_cookie_file,
    validate_cookie_file,
)

# ──────────────────────────────────────────────────────────────
# 全局主题配置与 Apple HIG 配色规范
# ──────────────────────────────────────────────────────────────
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# macOS Apple HIG 原生质感调色板（双模式：Light, Dark）
COLOR_WINDOW_BG       = ("#F2F2F7", "#1C1C1E")
COLOR_CARD_BG         = ("#FFFFFF", "#252528")
COLOR_CARD_BORDER     = ("#E5E5EA", "#333336")
COLOR_INPUT_BG        = ("#F2F2F7", "#2C2C2E")
COLOR_INPUT_BORDER    = ("#D1D1D6", "#3A3A3C")
COLOR_PRIMARY         = "#007AFF"
COLOR_PRIMARY_HOVER   = "#0062CC"
COLOR_SECONDARY       = ("#E5E5EA", "#3A3A3C")
COLOR_SECONDARY_HOVER = ("#D1D1D6", "#48484A")
COLOR_DANGER          = "#FF453A"
COLOR_DANGER_HOVER    = "#D7332A"

COLOR_TEXT_PRIMARY    = ("#000000", "#FFFFFF")
COLOR_TEXT_SECONDARY  = ("#8E8E93", "#8E8E93")
COLOR_BADGE_BG        = ("#E5E5EA", "#2C2C2E")
COLOR_BADGE_TEXT      = ("#3A3A3C", "#D1D1D6")

COLOR_STATUS_WAITING  = ("#8E8E93", "#8E8E93")
COLOR_STATUS_ACTIVE   = "#007AFF"
COLOR_STATUS_DONE     = "#34C759"
COLOR_STATUS_ERROR    = "#FF3B30"

BATCH_PRESET_OPTIONS = [
    "最高画质",
    "1080p (优先)",
    "720p (优先)",
    "仅提取音频 (MP3)",
]

GLOBAL_SUBTITLE_OPTIONS = [
    "无字幕",
    "优先简体中文",
    "优先英文",
    "下载全部字幕",
]

OUTPUT_FORMAT_OPTIONS = [
    "MP4 (苹果兼容)",
    "MKV (原画极速)",
    "WebM (原始流)",
]

OUTPUT_FORMAT_OPTIONS_HDR = [
    "MKV (原画极速)",
    "WebM (原始流)",
]

CHROME_EDGE_URL = "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc"
FIREFOX_URL = "https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/"
GITHUB_URL = "https://github.com/kairi003/Get-cookies.txt-LOCALLY"


def select_subtitle_by_preset(available_subs: list[dict], preset: str) -> str:
    """根据全局预设从可用字幕列表中选出最佳字幕 code。"""
    if not available_subs:
        return "none"
    codes = {item["code"]: item["name"] for item in available_subs}

    if preset == "无字幕" or "无" in preset:
        return "none"

    if preset == "下载全部字幕" or "全部" in preset:
        if "all" in codes:
            return "all"
        return "none"

    if preset == "优先简体中文" or "中文" in preset:
        for item in available_subs:
            if "简体中文" in item["name"] or item["name"] == "中文":
                return item["code"]
        for item in available_subs:
            if "繁体中文" in item["name"]:
                return item["code"]
        for item in available_subs:
            if "English" in item["name"] or item["code"].startswith("en"):
                return item["code"]
        return "none"

    if preset == "优先英文" or "英文" in preset or "English" in preset:
        for item in available_subs:
            if "English" in item["name"] or item["code"].startswith("en"):
                return item["code"]
        for item in available_subs:
            if "简体中文" in item["name"] or item["name"] == "中文":
                return item["code"]
        return "none"

    return "none"


def select_quality_by_preset(qualities: list[dict], preset: str, prefer_hdr: bool = False) -> dict:
    """根据全局预设名称与 HDR 偏好，在视频支持的画质列表中挑选最佳匹配项。"""
    if not qualities:
        return {}
    video_qualities = [q for q in qualities if q.get("type") != "audio"]
    audio_qualities = [q for q in qualities if q.get("type") == "audio"]

    preset_lower = str(preset).lower()
    if "音频" in preset or "mp3" in preset_lower:
        return audio_qualities[0] if audio_qualities else qualities[0]

    if not video_qualities:
        return qualities[0]

    if not prefer_hdr:
        # 用户未勾选 HDR：优先在 SDR 流中选择
        sdr_videos = [q for q in video_qualities if not q.get("is_hdr")]
        cand_pool = sdr_videos if sdr_videos else video_qualities
    else:
        # 用户勾选 HDR：在全量画质中挑选（qualities 中相同分辨率 HDR 优先）
        cand_pool = video_qualities

    if "最高" in preset:
        return cand_pool[0]
    elif "1080" in preset:
        for q in cand_pool:
            if q.get("height") and q.get("height") <= 1080:
                return q
        return cand_pool[0]
    elif "720" in preset:
        for q in cand_pool:
            if q.get("height") and q.get("height") <= 720:
                return q
        return cand_pool[0]

    return cand_pool[0]


class App(ctk.CTk):
    """主应用窗口。"""

    def __init__(self):
        super().__init__()

        self.title("YouTube 下载器")
        self.geometry("780x690")
        self.minsize(720, 630)
        self.resizable(True, True)
        self.configure(fg_color=COLOR_WINDOW_BG)

        # 状态变量
        self._settings = load_settings()
        self.enable_notification = bool(self._settings.get("enable_notification", True))
        self.default_subtitle = str(self._settings.get("default_subtitle") or "优先简体中文")
        if self.default_subtitle not in GLOBAL_SUBTITLE_OPTIONS:
            self.default_subtitle = "优先简体中文"
        self.enable_subtitles = bool(self.default_subtitle != "无字幕")
        self.prefer_hdr = bool(self._settings.get("prefer_hdr", False))
        self.output_format = str(self._settings.get("output_format") or "MP4 (苹果兼容)")
        if self.output_format not in OUTPUT_FORMAT_OPTIONS:
            self.output_format = "MP4 (苹果兼容)"

        self.notify_var = ctk.BooleanVar(value=self.enable_notification)
        self.hdr_var = ctk.BooleanVar(value=self.prefer_hdr)
        self._last_download_path: str | None = None

        self._parsed_tasks: list[dict] = []
        self._qualities: list[dict] = []
        self._download_task: DownloadTask | None = None
        self._is_downloading = False
        self._is_parsing = False
        self._queue_cancel_requested = False
        self._queue_row_widgets: list[dict] = []

        # 保存路径初始化与安全性校验（持久化记忆）
        saved_dir = str(self._settings.get("save_path") or "")
        if saved_dir and os.path.isdir(saved_dir):
            self._save_dir = saved_dir
        else:
            default_downloads = os.path.expanduser("~/Downloads")
            self._save_dir = default_downloads if os.path.isdir(default_downloads) else os.path.expanduser("~")
        self.save_path = self._save_dir

        self._updating_highlight = False
        self._current_task_view = "empty"

        # 构建 UI
        self._build_ui()

        # 兼容性别名
        self.url_textbox = self._url_textbox
        self.queue_frame = self._queue_scroll

    @property
    def save_path(self) -> str:
        return self._save_dir

    @save_path.setter
    def save_path(self, val: str):
        self._save_dir = str(val)

    def _save_settings(self):
        """将当前用户偏好配置持久化写入 settings.json。"""
        cfg = {
            "save_path": self._save_dir,
            "enable_notification": getattr(self, "enable_notification", True),
            "enable_subtitles": getattr(self, "enable_subtitles", False),
            "default_subtitle": getattr(self, "default_subtitle", "优先简体中文"),
            "prefer_hdr": getattr(self, "prefer_hdr", False),
            "output_format": getattr(self, "output_format", "MP4 (苹果兼容)"),
        }
        save_settings(cfg)

    @property
    def batch_tasks(self) -> list[dict]:
        return self._parsed_tasks

    @batch_tasks.setter
    def batch_tasks(self, val: list[dict]):
        self._parsed_tasks = val

        # 全局触控板滚轮监听（坐标判定方案，彻底解决 macOS 触控板滑动失效问题）
        self._setup_trackpad_scroll()

    # ── UI 布局体系 ───────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # 顶部标题栏
        self.grid_rowconfigure(1, weight=0)  # 链接输入卡片
        self.grid_rowconfigure(2, weight=1)  # 视频信息 / 队列卡片
        self.grid_rowconfigure(3, weight=0)  # 保存位置卡片
        self.grid_rowconfigure(4, weight=0)  # 下载控制与进度卡片
        self.grid_rowconfigure(5, weight=0)  # 底部状态栏

        self._build_header()
        self._build_url_section()
        self._build_task_section()
        self._build_savedir_section()
        self._build_progress_section()
        self._build_statusbar()

    def _create_card(self, row: int, pady=(0, 10)) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            self,
            fg_color=COLOR_CARD_BG,
            border_color=COLOR_CARD_BORDER,
            border_width=1,
            corner_radius=10,
        )
        card.grid(row=row, column=0, padx=18, pady=pady, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        return card

    # ── 1. 顶部标题栏 ──────────────────────────────────────────

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header,
            text="YouTube 下载器",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
        )
        title_label.grid(row=0, column=0, sticky="w")

        # Cookie 状态与导入按钮（位于外观模式切换左侧）
        self._cookie_btn = ctk.CTkButton(
            header,
            text="🍪 导入 Cookie / 配置指引",
            command=self._show_cookie_guide_dialog,
            width=175,
            height=28,
            corner_radius=6,
            fg_color=("#FFF0D4", "#3D2B15"),
            hover_color=("#FFE2B3", "#4D361A"),
            text_color=("#B25E00", "#FF9F0A"),
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self._cookie_btn.grid(row=0, column=1, padx=(0, 10), sticky="e")

        # 外观模式切换（中文原生选项，解决英文展示问题）
        self._appearance_menu = ctk.CTkOptionMenu(
            header,
            values=["跟随系统", "深色模式", "浅色模式"],
            command=self._on_appearance_change,
            width=110,
            height=28,
            corner_radius=6,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._appearance_menu.grid(row=0, column=2, sticky="e")
        self._appearance_menu.set("跟随系统")

        # 初始化刷新 Cookie 状态显示
        self._refresh_cookie_status()

    # ── 2. 视频链接输入区 ──────────────────────────────────────

    def _build_url_section(self):
        card = self._create_card(row=1)

        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        header_row.grid_columnconfigure(0, weight=1)

        self._url_header_label = ctk.CTkLabel(
            header_row,
            text="视频链接",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
        )
        self._url_header_label.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header_row,
            text="支持粘贴单个视频、播放列表或多条链接 (换行分隔)",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_SECONDARY,
        ).grid(row=0, column=1, sticky="e")

        # 多行文本输入区域
        self._url_textbox = ctk.CTkTextbox(
            card,
            height=68,
            corner_radius=8,
            fg_color=COLOR_INPUT_BG,
            border_width=1,
            border_color=COLOR_INPUT_BORDER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._url_textbox.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        # 配置底层 Text 控件 tag 样式（斑马纹高亮）
        tb = self._url_textbox._textbox
        tb.tag_config("odd_row", background="#222225")   # 奇数行深色
        tb.tag_config("even_row", background="#2A2A2E")  # 偶数行稍浅

        # 拦截粘贴事件（自动换行与光标推进）
        tb.bind("<<Paste>>", self._on_url_paste)
        tb.bind("<Command-v>", self._on_url_paste)
        tb.bind("<Command-V>", self._on_url_paste)
        tb.bind("<Control-v>", self._on_url_paste)
        tb.bind("<Control-V>", self._on_url_paste)

        # 监听内容变动（动态链接计数与斑马纹重算）
        tb.bind("<<Modified>>", self._update_url_highlight_and_count)
        tb.bind("<KeyRelease>", self._update_url_highlight_and_count)
        tb.bind("<ButtonRelease-1>", self._update_url_highlight_and_count)

        # 按钮栏（清空、解析）
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="e", padx=16, pady=(0, 10))

        self._clear_btn = ctk.CTkButton(
            btn_row,
            text="清空",
            width=68,
            height=30,
            corner_radius=6,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
            command=self._clear_url,
        )
        self._clear_btn.grid(row=0, column=0, padx=(0, 8))

        self._parse_btn = ctk.CTkButton(
            btn_row,
            text="解析",
            width=80,
            height=30,
            corner_radius=6,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._start_parse,
        )
        self._parse_btn.grid(row=0, column=1)

    # ── 3. 视频元数据与任务队列展示卡片 ─────────────────────────

    def _build_task_section(self):
        self._task_card = self._create_card(row=2)
        self._task_card.grid_rowconfigure(0, weight=1)

        # 视图 1：空状态占位
        self._empty_view = ctk.CTkFrame(self._task_card, fg_color="transparent")
        self._empty_view.grid(row=0, column=0, sticky="nsew", padx=16, pady=24)
        self._empty_view.grid_columnconfigure(0, weight=1)
        self._empty_view.grid_rowconfigure(0, weight=1)

        self._empty_label = ctk.CTkLabel(
            self._empty_view,
            text="尚未解析视频。请在上方输入链接后点击【解析】获取画质与预估大小。",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
        )
        self._empty_label.grid(row=0, column=0)

        # 视图 2：单视频展示视图
        self._single_view = ctk.CTkFrame(self._task_card, fg_color="transparent")
        self._single_view.grid_columnconfigure(0, weight=1)

        self._title_label = ctk.CTkLabel(
            self._single_view,
            text="",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
            anchor="w",
            justify="left",
            wraplength=710,
        )
        self._title_label.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 2))

        self._meta_label = ctk.CTkLabel(
            self._single_view,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self._meta_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        # 画质选择行（OptionMenu + 灰色胶囊 Badge）
        quality_row = ctk.CTkFrame(self._single_view, fg_color="transparent")
        quality_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            quality_row,
            text="画质规格：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        self._quality_menu = ctk.CTkOptionMenu(
            quality_row,
            values=["等待解析..."],
            command=self._on_quality_change,
            width=210,
            height=32,
            corner_radius=8,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._quality_menu.grid(row=0, column=1, sticky="w", padx=(0, 8))

        # 需求 1：macOS 风格灰色胶囊 Badge（背景 #2C2C2E，圆角 6）
        self._size_badge = ctk.CTkLabel(
            quality_row,
            text="--",
            width=70,
            height=26,
            corner_radius=6,
            fg_color=COLOR_BADGE_BG,
            text_color=COLOR_BADGE_TEXT,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self._size_badge.grid(row=0, column=2, sticky="w", padx=(0, 8))

        ctk.CTkLabel(
            quality_row,
            text="格式：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_PRIMARY,
        ).grid(row=0, column=3, sticky="w", padx=(0, 2))

        self._single_format_menu = ctk.CTkOptionMenu(
            quality_row,
            values=OUTPUT_FORMAT_OPTIONS,
            command=self._on_format_change,
            width=115,
            height=28,
            corner_radius=6,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._single_format_menu.grid(row=0, column=4, sticky="w", padx=(0, 8))
        self._single_format_menu.set(self.output_format)

        ctk.CTkLabel(
            quality_row,
            text="字幕：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_PRIMARY,
        ).grid(row=0, column=5, padx=(0, 2), sticky="w")

        self._single_subtitle_menu = ctk.CTkOptionMenu(
            quality_row,
            values=["无字幕"],
            command=self._on_single_subtitle_change,
            width=100,
            height=28,
            corner_radius=6,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._single_subtitle_menu.grid(row=0, column=6, sticky="w", padx=(0, 8))

        self._single_notify_checkbox = ctk.CTkCheckBox(
            quality_row,
            text="完成通知",
            variable=self.notify_var,
            command=self._on_notify_toggle,
            fg_color="#007AFF",
            hover_color="#0062CC",
            checkmark_color="#FFFFFF",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11),
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        )
        self._single_notify_checkbox.grid(row=0, column=7, padx=(2, 0), sticky="w")

        # 视图 3：多视频批量队列展示视图
        self._batch_view = ctk.CTkFrame(self._task_card, fg_color="transparent")
        self._batch_view.grid_columnconfigure(0, weight=1)
        self._batch_view.grid_rowconfigure(1, weight=1)

        batch_header = ctk.CTkFrame(self._batch_view, fg_color="transparent")
        batch_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 8))
        batch_header.grid_columnconfigure(0, weight=1)

        self._batch_count_label = ctk.CTkLabel(
            batch_header,
            text="任务队列 (共 0 个视频)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
        )
        self._batch_count_label.grid(row=0, column=0, sticky="w")

        self._batch_total_size_badge = ctk.CTkLabel(
            batch_header,
            text="预估总计: --",
            width=90,
            height=26,
            corner_radius=6,
            fg_color=COLOR_BADGE_BG,
            text_color=COLOR_BADGE_TEXT,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self._batch_total_size_badge.grid(row=0, column=1, padx=(0, 6))

        ctk.CTkLabel(
            batch_header,
            text="画质：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
        ).grid(row=0, column=2, padx=(0, 2))

        self._batch_preset_menu = ctk.CTkOptionMenu(
            batch_header,
            values=BATCH_PRESET_OPTIONS,
            command=self._on_batch_preset_change,
            width=100,
            height=28,
            corner_radius=6,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._batch_preset_menu.grid(row=0, column=3)
        self._batch_preset_menu.set("最高画质")

        ctk.CTkLabel(
            batch_header,
            text="格式：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
        ).grid(row=0, column=4, padx=(6, 2))

        self._batch_format_menu = ctk.CTkOptionMenu(
            batch_header,
            values=OUTPUT_FORMAT_OPTIONS,
            command=self._on_format_change,
            width=115,
            height=28,
            corner_radius=6,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._batch_format_menu.grid(row=0, column=5)
        self._batch_format_menu.set(self.output_format)

        self._hdr_checkbox = ctk.CTkCheckBox(
            batch_header,
            text="HDR",
            variable=self.hdr_var,
            command=self._on_toggle_hdr_checkbox,
            fg_color="#007AFF",
            hover_color="#0062CC",
            checkmark_color="#FFFFFF",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11),
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        )
        self._hdr_checkbox.grid(row=0, column=6, padx=(6, 0))

        ctk.CTkLabel(
            batch_header,
            text="字幕：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
        ).grid(row=0, column=7, padx=(6, 2))

        self._batch_subtitle_menu = ctk.CTkOptionMenu(
            batch_header,
            values=GLOBAL_SUBTITLE_OPTIONS,
            command=self._on_batch_subtitle_preset_change,
            width=100,
            height=28,
            corner_radius=6,
            fg_color=COLOR_INPUT_BG,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self._batch_subtitle_menu.grid(row=0, column=8)
        self._batch_subtitle_menu.set(self.default_subtitle)

        self._notify_checkbox = ctk.CTkCheckBox(
            batch_header,
            text="通知",
            variable=self.notify_var,
            command=self._on_notify_toggle,
            fg_color="#007AFF",
            hover_color="#0062CC",
            checkmark_color="#FFFFFF",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11),
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        )
        self._notify_checkbox.grid(row=0, column=9, padx=(6, 0))

        # 队列滚动列表
        self._queue_scroll = ctk.CTkScrollableFrame(
            self._batch_view,
            height=140,
            corner_radius=8,
            fg_color=COLOR_INPUT_BG,
        )
        self._queue_scroll.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        self._queue_scroll.grid_columnconfigure(0, weight=1)
        self.queue_frame = self._queue_scroll

        # 彻底废除 CustomTkinter 内部的所有解绑后门
        self.queue_frame._enable_mouse_bindings = lambda *args, **kwargs: None
        self.queue_frame._disable_mouse_bindings = lambda *args, **kwargs: None
        self.queue_frame._mouse_scroll = lambda *args, **kwargs: None

        canvas = self.queue_frame._parent_canvas
        try:
            canvas.unbind("<Enter>")
            canvas.unbind("<Leave>")
        except Exception:
            pass

        self._show_task_view("empty")

    def _show_task_view(self, view_name: str):
        """在空状态、单视频状态、多任务队列状态之间无缝切换。"""
        self._current_task_view = view_name
        self._empty_view.grid_forget()
        self._single_view.grid_forget()
        self._batch_view.grid_forget()

        if view_name == "single":
            self._single_view.grid(row=0, column=0, sticky="nsew")
        elif view_name == "batch":
            self._batch_view.grid(row=0, column=0, sticky="nsew")
        else:
            self._empty_view.grid(row=0, column=0, sticky="nsew")

    # ── 4. 保存位置卡片 ────────────────────────────────────────

    def _build_savedir_section(self):
        card = self._create_card(row=3)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=16, pady=10)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            row,
            text="保存位置：",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._dir_label = ctk.CTkLabel(
            row,
            text=self._save_dir,
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
        )
        self._dir_label.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        browse_btn = ctk.CTkButton(
            row,
            text="浏览...",
            width=76,
            height=30,
            corner_radius=6,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
            command=self._browse_dir,
        )
        browse_btn.grid(row=0, column=2, padx=(0, 6))

        open_finder_btn = ctk.CTkButton(
            row,
            text="打开访达",
            width=76,
            height=30,
            corner_radius=6,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
            command=lambda: self._reveal_in_finder(),
        )
        open_finder_btn.grid(row=0, column=3)

    # ── 5. 下载控制与进度卡片 ───────────────────────────────────

    def _build_progress_section(self):
        card = self._create_card(row=4)

        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        header_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_row,
            text="下载进度",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        # 进度条
        self._progress_bar = ctk.CTkProgressBar(
            card,
            height=8,
            corner_radius=4,
            progress_color=COLOR_PRIMARY,
        )
        self._progress_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self._progress_bar.set(0)

        # 进度统计三列（百分比、即时速度、剩余时间）
        stats_row = ctk.CTkFrame(card, fg_color="transparent")
        stats_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        stats_row.grid_columnconfigure((0, 1, 2), weight=1)

        self._pct_label = ctk.CTkLabel(
            stats_row,
            text="0.0%",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_PRIMARY,
            anchor="w",
        )
        self._pct_label.grid(row=0, column=0, sticky="w")

        self._speed_label = ctk.CTkLabel(
            stats_row,
            text="0.00 MB/s",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
            anchor="center",
        )
        self._speed_label.grid(row=0, column=1, sticky="ew")

        self._eta_label = ctk.CTkLabel(
            stats_row,
            text="预计剩余 --:--",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
            anchor="e",
        )
        self._eta_label.grid(row=0, column=2, sticky="e")

        # 操作按钮栏（开始下载 / 取消）
        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        action_row.grid_columnconfigure((0, 1), weight=1)

        self._download_btn = ctk.CTkButton(
            action_row,
            text="开始下载",
            height=40,
            corner_radius=8,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._start_download,
        )
        self._download_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self._cancel_btn = ctk.CTkButton(
            action_row,
            text="取消",
            height=40,
            corner_radius=8,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=13),
            state="disabled",
            command=self._cancel_download,
        )
        self._cancel_btn.grid(row=0, column=1, padx=(6, 0), sticky="ew")

    # ── 6. 底部状态栏 ──────────────────────────────────────────

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, corner_radius=0, height=32, fg_color=COLOR_CARD_BG)
        bar.grid(row=5, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_propagate(False)

        self._status_label = ctk.CTkLabel(
            bar,
            text="就绪：请输入 YouTube 链接并点击【解析】",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_SECONDARY,
            anchor="w",
        )
        self._status_label.grid(row=0, column=0, padx=18, pady=0, sticky="ew")

    # ── 交互事件处理 ──────────────────────────────────────────

    def _on_appearance_change(self, mode: str):
        mode_map = {
            "跟随系统": "System",
            "深色模式": "Dark",
            "浅色模式": "Light",
            "System": "System",
            "Dark": "Dark",
            "Light": "Light",
        }
        target = mode_map.get(mode, "System")
        ctk.set_appearance_mode(target)

        if hasattr(self, "_url_textbox"):
            tb = self._url_textbox._textbox
            if target == "Light":
                tb.tag_config("odd_row", background="#EAEAEA")
                tb.tag_config("even_row", background="#F4F4F6")
            else:
                tb.tag_config("odd_row", background="#222225")
                tb.tag_config("even_row", background="#2A2A2E")

    def _refresh_cookie_status(self):
        """检测并刷新顶部 Cookie 按钮/徽章外观与文案。"""
        if not hasattr(self, "_cookie_btn"):
            return
        if has_valid_cookie():
            self._cookie_btn.configure(
                text="🍪 Cookie 已生效 (点击管理)",
                width=175,
                fg_color=("#28CD41", "#30D158"),
                hover_color=("#24B53A", "#28BA4E"),
                text_color="#FFFFFF",
            )
        else:
            self._cookie_btn.configure(
                text="🍪 导入 Cookie / 配置指引",
                width=175,
                fg_color=("#FFF0D4", "#3D2B15"),
                hover_color=("#FFE2B3", "#4D361A"),
                text_color=("#B25E00", "#FF9F0A"),
            )

    def _choose_cookie_file(self) -> str | None:
        """明确委托访达（Finder）呼出文件选择器，完美继承访达的简体中文界面。"""
        has_dialog = hasattr(self, "_cookie_dialog") and self._cookie_dialog and self._cookie_dialog.winfo_exists()
        if has_dialog:
            try:
                self._cookie_dialog.attributes("-topmost", False)
            except Exception:
                pass

        try:
            cmd = '''osascript -e 'tell application "Finder"' -e 'activate' -e 'set p to POSIX path of (choose file with prompt "请选择 YouTube 的 cookies.txt 文件：" of type {"txt", "TEXT", "public.plain-text", "public.text"})' -e 'return p' -e 'end tell' 2>/dev/null'''
            try:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    chosen_path = res.stdout.strip()
                    if chosen_path and os.path.isfile(chosen_path):
                        return chosen_path
                elif res.returncode != 0:
                    return None
            except Exception:
                pass

            try:
                parent_win = self._cookie_dialog if has_dialog else self
                path = filedialog.askopenfilename(
                    parent=parent_win,
                    title="请选择 YouTube 的 cookies.txt 文件",
                    filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
                )
                if path and os.path.isfile(path):
                    return path
            except Exception:
                pass
            return None
        finally:
            if has_dialog:
                try:
                    self._cookie_dialog.attributes("-topmost", True)
                    self._cookie_dialog.lift()
                    self._cookie_dialog.focus_force()
                except Exception:
                    pass

    def _on_cookie_btn_clicked(self):
        """处理点击 Cookie 状态按钮/徽章的操作：弹出指引与管理弹窗。"""
        self._show_cookie_guide_dialog()

    def _show_cookie_guide_dialog(self):
        """构建并弹出 macOS 简约暗黑风格的 Cookie 导入与配置分步指引模态弹窗。"""
        if hasattr(self, "_cookie_dialog") and self._cookie_dialog and self._cookie_dialog.winfo_exists():
            self._cookie_dialog.lift()
            self._cookie_dialog.focus_force()
            return

        dialog = ctk.CTkToplevel(self)
        self._cookie_dialog = dialog
        dialog.title("YouTube Cookie 配置与反爬指引")
        dialog.resizable(False, False)

        # 固定尺寸 580x490 并居中于主窗口
        w_width, w_height = 580, 490
        self.update_idletasks()
        rx = self.winfo_x() + (self.winfo_width() - w_width) // 2
        ry = self.winfo_y() + (self.winfo_height() - w_height) // 2
        dialog.geometry(f"{w_width}x{w_height}+{max(rx, 40)}+{max(ry, 40)}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.attributes("-topmost", True)

        def _close_dialog():
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()
            self._cookie_dialog = None

        dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

        # 根容器
        container = ctk.CTkFrame(dialog, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=20, pady=(10, 12))

        # 1. 顶部标题栏
        title_box = ctk.CTkFrame(container, fg_color="transparent")
        title_box.pack(fill="x", pady=(0, 5))

        title_lbl = ctk.CTkLabel(
            title_box,
            text="YouTube Cookie 配置与反爬指引",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
            anchor="w",
        )
        title_lbl.pack(fill="x")

        sub_lbl = ctk.CTkLabel(
            title_box,
            text="导入浏览器 Cookie 凭据可解决未登录与人机验证拦截，解锁 4K/HDR 原画与高速下载",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_SECONDARY,
            wraplength=520,
            justify="left",
            anchor="w",
        )
        sub_lbl.pack(fill="x", pady=(1, 0))

        # 2. 当前状态指示卡片
        status_card = ctk.CTkFrame(container, fg_color=("#EAEAEA", "#252528"), corner_radius=8)
        status_card.pack(fill="x", pady=(0, 7))

        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(fill="x", padx=12, pady=6)

        status_tag = ctk.CTkLabel(
            status_inner,
            text="● 当前状态: 未导入任何 Cookie",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#C05621", "#FF9F0A"),
            anchor="w",
        )
        status_tag.pack(fill="x")

        status_path_lbl = ctk.CTkLabel(
            status_inner,
            text="未导入任何 Cookie",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_SECONDARY,
            wraplength=500,
            justify="left",
            anchor="w",
        )
        status_path_lbl.pack(fill="x", pady=(1, 0))

        # 3. 分步图文引导内容 (按 Get cookies.txt LOCALLY 规范)
        steps_card = ctk.CTkFrame(container, fg_color=("#F5F5F7", "#202023"), corner_radius=8)
        steps_card.pack(fill="x", pady=(0, 9))

        steps_data = [
            ("1", "第一步（安装扩展）", "在主力浏览器中安装开源扩展：Get cookies.txt LOCALLY。（Edge 浏览器可直接打开上方 Chrome 商店链接点击「获取」一键安装）"),
            ("2", "第二步（登录账号）", "打开并登录 YouTube 官网（https://www.youtube.com），确保能正常观看会员或受限内容。"),
            ("3", "第三步（导出凭据）", "点击浏览器工具栏的该插件图标，在弹出窗口中直接点击蓝色的「Export」按钮，保存为 cookies.txt 文件。"),
            ("4", "第四步（载入软件）", "点击下方按钮选择刚刚导出的 cookies.txt，软件将自动持久化保存并热生效。"),
        ]

        steps_container = ctk.CTkFrame(steps_card, fg_color="transparent")
        steps_container.pack(fill="x", padx=14, pady=7)

        for idx, (s_num, s_title, s_desc) in enumerate(steps_data):
            row = ctk.CTkFrame(steps_container, fg_color="transparent")
            row.pack(fill="x", pady=(0 if idx == 0 else 4, 0))

            badge = ctk.CTkLabel(
                row,
                text=s_num,
                width=20,
                height=20,
                corner_radius=10,
                fg_color=COLOR_PRIMARY,
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            badge.pack(side="left", anchor="n", pady=1)

            t_box = ctk.CTkFrame(row, fg_color="transparent")
            t_box.pack(side="left", fill="x", expand=True, padx=(10, 0))

            h_lbl = ctk.CTkLabel(
                t_box,
                text=s_title,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLOR_TEXT_PRIMARY,
                anchor="w",
            )
            h_lbl.pack(fill="x")

            # 第一步：快捷跳转按钮组 (Chrome/Edge 商店、Firefox 商店、GitHub 主页)
            if s_num == "1":
                btn_frame = ctk.CTkFrame(t_box, fg_color="transparent")
                btn_frame.pack(fill="x", pady=(3, 2))

                btn_chrome = ctk.CTkButton(
                    btn_frame,
                    text="🌐 Chrome / Edge 商店",
                    command=lambda: webbrowser.open(CHROME_EDGE_URL),
                    height=24,
                    corner_radius=6,
                    fg_color=("#E5E5EA", "#3A3A3C"),
                    hover_color=("#D1D1D6", "#48484A"),
                    text_color=("#007AFF", "#0A84FF"),
                    font=ctk.CTkFont(family="SF Pro Text", size=11),
                )
                btn_chrome.pack(side="left", padx=(0, 6))

                btn_firefox = ctk.CTkButton(
                    btn_frame,
                    text="🦊 Firefox 商店",
                    command=lambda: webbrowser.open(FIREFOX_URL),
                    height=24,
                    corner_radius=6,
                    fg_color=("#E5E5EA", "#3A3A3C"),
                    hover_color=("#D1D1D6", "#48484A"),
                    text_color=("#007AFF", "#0A84FF"),
                    font=ctk.CTkFont(family="SF Pro Text", size=11),
                )
                btn_firefox.pack(side="left", padx=(0, 6))

                btn_github = ctk.CTkButton(
                    btn_frame,
                    text="🐙 GitHub 主页",
                    command=lambda: webbrowser.open(GITHUB_URL),
                    height=24,
                    corner_radius=6,
                    fg_color=("#E5E5EA", "#3A3A3C"),
                    hover_color=("#D1D1D6", "#48484A"),
                    text_color=("#007AFF", "#0A84FF"),
                    font=ctk.CTkFont(family="SF Pro Text", size=11),
                )
                btn_github.pack(side="left")

            d_lbl = ctk.CTkLabel(
                t_box,
                text=s_desc,
                font=ctk.CTkFont(size=11),
                text_color=COLOR_TEXT_SECONDARY,
                wraplength=460,
                justify="left",
                anchor="w",
            )
            d_lbl.pack(fill="x", pady=(1, 0))

        # 4. 底部操作区
        action_bar = ctk.CTkFrame(container, fg_color="transparent")
        action_bar.pack(fill="x")

        def _refresh_dialog_status():
            if has_valid_cookie():
                c_path = get_current_cookie_file()
                b_name = get_browser_cookie()
                source_str = f"应用专属托管：{c_path}" if c_path else f"本地浏览器：{b_name}"
                status_tag.configure(text="● 当前状态: Cookie 已生效", text_color=("#1A8838", "#30D158"))
                status_path_lbl.configure(text=source_str, text_color=COLOR_TEXT_SECONDARY)
                btn_remove.pack(side="left", padx=(0, 10))
            else:
                status_tag.configure(text="● 当前状态: 未导入任何 Cookie", text_color=("#C05621", "#FF9F0A"))
                status_path_lbl.configure(text="未导入任何 Cookie（受限视频下载将触发 YouTube 反爬拦截）", text_color=COLOR_TEXT_SECONDARY)
                btn_remove.pack_forget()

        def _on_import_clicked():
            chosen = self._choose_cookie_file()
            if not chosen:
                return

            # 1. 基础内容与格式校验
            valid, reason = validate_cookie_file(chosen)
            if not valid:
                messagebox.showerror(
                    "无效的 Cookie 文件",
                    f"所选文件未通过格式校验：\n{reason}\n\n请确认是由「Get cookies.txt LOCALLY」扩展导出的 Netscape 格式 cookies.txt 文本文件。",
                    parent=dialog,
                )
                return

            # 2. 执行物理拷贝导入至应用独立专属配置目录
            try:
                saved_path = save_imported_cookie(chosen)
                self._refresh_cookie_status()
                self._set_status("Cookie 凭据已安全导入至应用配置目录", "success")
                _refresh_dialog_status()
                status_path_lbl.configure(
                    text="✅ 已安全导入至应用独立配置目录（可放心删除原文件）",
                    text_color=("#1A8838", "#30D158"),
                )
                messagebox.showinfo(
                    "导入成功",
                    f"Cookie 凭据已成功导入至应用专属配置目录：\n\n{saved_path}\n\n✅ 软件已建立独立持久化副本，您现在可以放心将下载或桌面中的原 cookies.txt 文件删除！",
                    parent=dialog,
                )
            except Exception as err:
                messagebox.showerror("导入失败", f"无法加载或保存所选 Cookie 文件：\n{err}", parent=dialog)

        def _on_remove_clicked():
            ans = messagebox.askyesno(
                "移除 Cookie",
                "确定要从应用配置目录中移除当前 YouTube Cookie 凭据吗？\n\n移除后下载受限或会员视频可能需要重新配置。",
                parent=dialog,
            )
            if ans:
                clear_current_cookie()
                self._refresh_cookie_status()
                self._set_status("已移除 Cookie 登录凭据", "info")
                _refresh_dialog_status()
                messagebox.showinfo("已移除", "Cookie 凭据已完全移除，已恢复为未登录状态。", parent=dialog)

        btn_import = ctk.CTkButton(
            action_bar,
            text="📂 选取并导入 cookies.txt",
            command=_on_import_clicked,
            width=180,
            height=32,
            corner_radius=7,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        btn_import.pack(side="left", padx=(0, 10))

        btn_remove = ctk.CTkButton(
            action_bar,
            text="移除当前 Cookie",
            command=_on_remove_clicked,
            width=115,
            height=32,
            corner_radius=7,
            fg_color=("#FEE2E2", "#3E1E1E"),
            hover_color=("#FCA5A5", "#4E2626"),
            text_color=("#DC2626", "#FF453A"),
            font=ctk.CTkFont(size=12),
        )

        btn_close = ctk.CTkButton(
            action_bar,
            text="完成",
            command=_close_dialog,
            width=80,
            height=32,
            corner_radius=7,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        btn_close.pack(side="right")

        _refresh_dialog_status()

    def _setup_trackpad_scroll(self):
        target_canvas = getattr(self.queue_frame, "_parent_canvas", None)
        if not target_canvas:
            return

        target_canvas.configure(yscrollincrement=1)

        def _handle_scroll(event, is_touchpad: bool = False):
            try:
                # 1. 物理矩形碰撞检测：鼠标指针是否落在任务队列内
                mx, my = self.winfo_pointerxy()
                qx = self.queue_frame.winfo_rootx()
                qy = self.queue_frame.winfo_rooty()
                qw = self.queue_frame.winfo_width()
                qh = self.queue_frame.winfo_height()

                if not (qx <= mx <= qx + qw and qy <= my <= qy + qh):
                    return

                # 2. 提取并计算有效滚动偏移
                # 处理 macOS Tk 触控板的高精度/打包 delta
                raw_delta = getattr(event, "delta", 0)
                step = 0

                event_type = str(getattr(event, "type", ""))
                if is_touchpad or "Touchpad" in event_type or event_type == "39" or raw_delta > 1000 or raw_delta < -1000:
                    # 兼容 16-bit 打包数据
                    raw = raw_delta & 0xFFFFFFFF
                    delta_y = raw & 0xFFFF
                    if delta_y >= 0x8000:
                        delta_y -= 0x10000
                    step = -1 * delta_y
                else:
                    if raw_delta != 0:
                        step = -1 * int(raw_delta) * 12

                if step != 0:
                    target_canvas.yview_scroll(step, "units")
            except Exception:
                pass

        # 同时绑定 macOS 滚轮与触控板手势
        try:
            self.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self.bind_all("<MouseWheel>", lambda e: _handle_scroll(e, False), add="+")
        try:
            self.bind_all("<TouchpadScroll>", lambda e: _handle_scroll(e, True), add="+")
        except Exception:
            pass
        self.bind_all("<Button-4>", lambda e: _handle_scroll(type("E", (), {"delta": 1, "type": "MouseWheel"})(), False), add="+")
        self.bind_all("<Button-5>", lambda e: _handle_scroll(type("E", (), {"delta": -1, "type": "MouseWheel"})(), False), add="+")

    def _on_url_paste(self, event=None):
        """拦截粘贴事件：清除首尾多余空格，自动补齐换行符 \\n，推进光标到下一行。"""
        try:
            clipboard_text = self.clipboard_get()
        except Exception:
            return "break"

        if not clipboard_text:
            return "break"

        lines = [line.strip() for line in clipboard_text.strip().splitlines() if line.strip()]
        if not lines:
            return "break"

        cleaned = "\n".join(lines)
        tb = self._url_textbox._textbox

        try:
            tb.delete("sel.first", "sel.last")
        except Exception:
            pass

        # 自动补齐换行符，保证每条链接独占一行并准备下一行继续粘贴
        insert_text = cleaned + "\n"
        tb.insert("insert", insert_text)
        tb.see("insert")

        # 刷新计数与高亮
        self._update_url_highlight_and_count()
        return "break"

    def _update_url_highlight_and_count(self, event=None):
        """动态更新链接数量标签并重算输入框斑马纹（奇偶行交替高亮）。"""
        if getattr(self, "_updating_highlight", False):
            return
        self._updating_highlight = True
        try:
            tb = self._url_textbox._textbox
            if tb.edit_modified():
                tb.edit_modified(False)

            content = tb.get("1.0", "end-1c")

            # 1. 动态链接计数
            urls = extract_video_urls(content)
            count = len(urls)
            if count > 0:
                self._url_header_label.configure(text=f"视频链接 (已识别 {count} 个链接)")
            else:
                self._url_header_label.configure(text="视频链接")

            # 2. 斑马纹高亮
            tb.tag_remove("odd_row", "1.0", "end")
            tb.tag_remove("even_row", "1.0", "end")

            if content.strip():
                total_lines = int(tb.index("end-1c").split(".")[0])
                for line_num in range(1, total_lines + 1):
                    tag = "odd_row" if line_num % 2 == 1 else "even_row"
                    tb.tag_add(tag, f"{line_num}.0", f"{line_num}.end+1c")
        finally:
            self._updating_highlight = False

    def _clear_url(self):
        self._url_textbox.delete("1.0", "end")
        self._update_url_highlight_and_count()
        self._parsed_tasks = []
        self._qualities = []
        self._queue_row_widgets = []
        self._show_task_view("empty")
        self._reset_progress()
        self._download_btn.configure(state="disabled", text="开始下载")
        self._set_status("就绪：请输入 YouTube 链接并点击【解析】", "info")

    def _browse_dir(self):
        """明确委托访达（Finder）呼出目录选择器，完美继承访达的简体中文界面与侧边栏。"""
        chosen_path = None
        cmd = '''osascript -e 'tell application "Finder"' -e 'activate' -e 'set p to POSIX path of (choose folder with prompt "请选择视频保存文件夹：")' -e 'return p' -e 'end tell' 2>/dev/null'''
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            self.lift()
            self.focus_force()
            if res.returncode == 0:
                p = res.stdout.strip()
                if p and os.path.isdir(p):
                    chosen_path = p
        except Exception:
            pass

        if not chosen_path:
            try:
                p = filedialog.askdirectory(initialdir=self._save_dir, title="请选择视频保存文件夹")
                if p and os.path.isdir(p):
                    chosen_path = p
            except Exception:
                pass

        if chosen_path and os.path.isdir(chosen_path):
            self._save_dir = chosen_path
            self.save_path = chosen_path
            if hasattr(self, "_dir_label"):
                self._dir_label.configure(text=chosen_path)
            self._save_settings()
            update_setting("save_path", chosen_path)
            self._set_status(f"已更新保存目录: {chosen_path}", "info")

    _browse_save_path = _browse_dir
    _select_folder = _browse_dir
    select_save_dir = _browse_dir
    browse_directory = _browse_dir

    def _get_effective_quality_size(self, q: dict, task: dict | None = None) -> tuple[int, str]:
        """根据当前选中的输出封装格式计算该画质规格的真实预估体积。"""
        if not q:
            return 0, "--"
        raw_bytes = q.get("raw_size_bytes") or q.get("size_bytes") or 0

        fmt = None
        if task and isinstance(task, dict) and task.get("output_format"):
            fmt = task.get("output_format")
        elif len(self._parsed_tasks) == 1 and self._parsed_tasks[0].get("output_format"):
            fmt = self._parsed_tasks[0].get("output_format")
        if not fmt:
            fmt = getattr(self, "output_format", "MP4 (苹果兼容)")

        # HDR 模式下统一采用原画容器（MKV/WebM），不转码
        is_hdr = bool(q.get("is_hdr")) or ("hdr" in str(q.get("label", "")).lower())
        if is_hdr:
            sz_bytes = raw_bytes
        elif "MP4" in fmt and q.get("needs_transcode"):
            sz_bytes = q.get("transcode_size_bytes") or raw_bytes
        else:
            sz_bytes = raw_bytes
        return sz_bytes, format_size(sz_bytes)

    def _sync_format_with_quality(self, task: dict | None = None):
        """
        根据画质规格（HDR 或 SDR）动态联动并同步【格式】下拉菜单：
        1. 当选中画质为 HDR 时：
           - 格式下拉菜单仅保留 ["MKV (原画极速)", "WebM (原始流)"]（移除 MP4）；
           - 若当前格式为 MP4 或不在支持列表中，自动设为 "MKV (原画极速)"，标记 auto_switched_to_mkv。
        2. 当选中画质为普通 SDR 时：
           - 恢复完整列表 ["MP4 (苹果兼容)", "MKV (原画极速)", "WebM (原始流)"]；
           - 若此前因 HDR 被自动切到了 MKV（且未手动指定其他格式），自动恢复为全局默认格式。
        3. 同步刷新预估体积 Badge 与元数据。
        """
        if task is None:
            if self._parsed_tasks:
                task = self._parsed_tasks[0]
            else:
                return

        q = task.get("selected_quality") or {}
        is_hdr = bool(q.get("is_hdr")) or ("hdr" in str(q.get("label", "")).lower())

        # 匹配该任务关联的格式下拉菜单
        fmt_menu = task.get("format_menu_widget") or task.get("format_menu")
        if fmt_menu is None:
            if len(self._parsed_tasks) == 1 and hasattr(self, "_single_format_menu"):
                fmt_menu = self._single_format_menu
            elif hasattr(self, "_batch_format_menu"):
                fmt_menu = self._batch_format_menu

        cur_fmt = task.get("output_format") or getattr(self, "output_format", "MP4 (苹果兼容)")

        if is_hdr:
            # HDR 模式：仅保留原画容器（MKV 与 WebM），移除 MP4
            if fmt_menu:
                try:
                    fmt_menu.configure(values=OUTPUT_FORMAT_OPTIONS_HDR)
                except Exception:
                    pass

            if cur_fmt not in OUTPUT_FORMAT_OPTIONS_HDR or "MP4" in cur_fmt:
                task["auto_switched_to_mkv"] = True
                task["output_format"] = "MKV (原画极速)"
                if fmt_menu:
                    try:
                        fmt_menu.set("MKV (原画极速)")
                    except Exception:
                        pass
            else:
                task["output_format"] = cur_fmt
                if fmt_menu:
                    try:
                        fmt_menu.set(cur_fmt)
                    except Exception:
                        pass
        else:
            # 普通 SDR 模式：恢复完整选项
            if fmt_menu:
                try:
                    fmt_menu.configure(values=OUTPUT_FORMAT_OPTIONS)
                except Exception:
                    pass

            if task.get("auto_switched_to_mkv"):
                task["auto_switched_to_mkv"] = False
                default_fmt = getattr(self, "output_format", "MP4 (苹果兼容)")
                task["output_format"] = default_fmt
                if fmt_menu:
                    try:
                        fmt_menu.set(default_fmt)
                    except Exception:
                        pass
            else:
                task["output_format"] = cur_fmt
                if fmt_menu:
                    try:
                        fmt_menu.set(cur_fmt)
                    except Exception:
                        pass

        # 刷新单视频体积 Badge
        if hasattr(self, "_size_badge"):
            _, sz_str = self._get_effective_quality_size(q, task)
            try:
                self._size_badge.configure(text=sz_str)
            except Exception:
                pass

        # 刷新多视频卡片体积
        self._update_card_size(task)
        if hasattr(self, "_batch_total_size_badge"):
            self._update_total_size()

    def _on_format_change(self, new_format: str):
        """用户切换输出封装格式（MP4 / MKV / WebM）时，更新持久化配置并实时联动所有卡片预估体积。"""
        self.output_format = new_format
        update_setting("output_format", new_format)

        if hasattr(self, "_batch_format_menu"):
            try:
                self._batch_format_menu.set(new_format)
            except Exception:
                pass
        if hasattr(self, "_single_format_menu"):
            try:
                self._single_format_menu.set(new_format)
            except Exception:
                pass

        # 刷新单视频视图中的体积 badge
        if self._parsed_tasks:
            s_task = self._parsed_tasks[0]
            s_task["output_format"] = new_format
            s_task["auto_switched_to_mkv"] = False
            cur_q = s_task.get("selected_quality") or (self._qualities[0] if self._qualities else {})
            if cur_q and hasattr(self, "_size_badge"):
                _, sz_str = self._get_effective_quality_size(cur_q, s_task)
                self._size_badge.configure(text=sz_str)

        # 刷新多视频队列卡片中的体积 badge 与元数据
        for task in self.batch_tasks:
            task["output_format"] = new_format
            task["auto_switched_to_mkv"] = False
            self._update_card_size(task)

        if hasattr(self, "_batch_total_size_badge"):
            self._update_total_size()

    def _on_quality_change(self, selected_label: str):
        """画质下拉框改变时，同步刷新格式联动与右侧胶囊 Badge 的预估体积。"""
        if not self._qualities:
            return
        q = next((item for item in self._qualities if item.get("label") == selected_label), None)
        if q:
            if self._parsed_tasks:
                task = self._parsed_tasks[0]
                task["custom_quality"] = True
                task["selected_quality"] = q
                task["prefer_hdr"] = bool(q.get("is_hdr", False)) or ("hdr" in str(q.get("label", "")).lower())
                self._sync_format_with_quality(task)
            else:
                _, sz_str = self._get_effective_quality_size(q)
                self._size_badge.configure(text=sz_str)

    def _find_matching_quality(self, quality_options: list[dict], global_preset: str, prefer_hdr: bool = False) -> dict:
        """在该视频的可选列表中寻找最接近全局预设的画质。"""
        return select_quality_by_preset(quality_options, global_preset, prefer_hdr=prefer_hdr)

    def _update_card_size(self, task: dict):
        """刷新指定任务卡片上的预估体积 Badge 与元数据标签。"""
        q = task.get("selected_quality") or {}
        _, sz_str = self._get_effective_quality_size(q, task)
        if task.get("size_badge"):
            try:
                task["size_badge"].configure(text=sz_str)
            except Exception:
                pass
        if task.get("meta_lbl"):
            try:
                dur = task.get("duration_str", "--:--")
                uploader = task.get("uploader", "YouTube")
                lbl = q.get("label", "")
                task["meta_lbl"].configure(text=f"{dur}  •  {lbl}  •  {uploader}")
            except Exception:
                pass

    def _update_total_size(self):
        """重新汇总并刷新顶部的【预估总计】体积数值。"""
        total_bytes = sum(self._get_effective_quality_size(t.get("selected_quality") or {}, t)[0] for t in self._parsed_tasks)
        self._batch_total_size_badge.configure(text=f"预估总计: {format_size(total_bytes)}")

    def _on_toggle_hdr_checkbox(self):
        """用户勾选/取消勾选【HDR】时，在全量队列中平移切换各视频的 HDR 与 SDR 规格并持久化。"""
        is_hdr_enabled = bool(self.hdr_var.get()) if hasattr(self, "hdr_var") else False
        self.prefer_hdr = is_hdr_enabled
        update_setting("prefer_hdr", is_hdr_enabled)
        preset_name = self._batch_preset_menu.get() if hasattr(self, "_batch_preset_menu") else "最高画质"

        for task in self.batch_tasks:
            opts = task.get("quality_options") or task.get("qualities") or []
            if not opts:
                continue

            # 1. 如果用户手动定制过该卡片的画质 (custom_quality == True)，平滑平移其分辨率对应的 HDR / SDR 选项
            if task.get("custom_quality", False):
                cur_q = task.get("selected_quality") or {}
                # 如果是纯音频流则无需切换 HDR
                if cur_q.get("type") == "audio":
                    continue

                cur_height = cur_q.get("height")
                if not cur_height:
                    lbl = cur_q.get("label") or (task["menu_widget"].get() if task.get("menu_widget") else "")
                    m = re.search(r"(\d{3,4})p", str(lbl))
                    if m:
                        cur_height = int(m.group(1))

                if cur_height:
                    if not is_hdr_enabled:
                        # 取消 HDR：寻找相同高度但不带 HDR 的纯 SDR 选项（如 "1080p"）
                        cand = next(
                            (q for q in opts if q.get("height") == cur_height and not q.get("is_hdr") and q.get("type") != "audio"),
                            None
                        )
                    else:
                        # 启用 HDR：寻找相同高度且带 HDR 的选项（如 "1080p60 HDR"）
                        cand = next(
                            (q for q in opts if q.get("height") == cur_height and q.get("is_hdr") and q.get("type") != "audio"),
                            None
                        )

                    if cand:
                        task["selected_quality"] = cand
                        task["prefer_hdr"] = is_hdr_enabled
                        if task.get("menu_widget") and cand.get("label"):
                            try:
                                task["menu_widget"].set(cand["label"])
                            except Exception:
                                pass
                        self._sync_format_with_quality(task)

            # 2. 如果未被用户单独修改过 (custom_quality == False)，按全局预设与新 HDR 状态自适应挑选
            else:
                target_q = self._find_matching_quality(opts, preset_name, prefer_hdr=is_hdr_enabled)
                if target_q:
                    task["selected_quality"] = target_q
                    task["prefer_hdr"] = is_hdr_enabled
                    if task.get("menu_widget") and target_q.get("label"):
                        try:
                            task["menu_widget"].set(target_q["label"])
                        except Exception:
                            pass
                    self._sync_format_with_quality(task)

        # 单视频视图联动同步
        if getattr(self, "_current_task_view", "batch") == "single" and self._parsed_tasks:
            s_task = self._parsed_tasks[0]
            s_opts = s_task.get("quality_options") or s_task.get("qualities") or []
            s_cur_q = s_task.get("selected_quality") or {}
            s_height = s_cur_q.get("height")
            if s_height:
                if not is_hdr_enabled:
                    s_cand = next((q for q in s_opts if q.get("height") == s_height and not q.get("is_hdr") and q.get("type") != "audio"), None)
                else:
                    s_cand = next((q for q in s_opts if q.get("height") == s_height and q.get("is_hdr") and q.get("type") != "audio"), None)
                if s_cand:
                    s_task["selected_quality"] = s_cand
                    s_task["prefer_hdr"] = is_hdr_enabled
                    if s_cand.get("label"):
                        try:
                            self._quality_menu.set(s_cand["label"])
                        except Exception:
                            pass
                    self._sync_format_with_quality(s_task)

        # 3. 批量控制栏格式下拉框同步联动
        if hasattr(self, "_batch_format_menu"):
            if is_hdr_enabled:
                self._batch_format_menu.configure(values=OUTPUT_FORMAT_OPTIONS_HDR)
                if "MP4" in self._batch_format_menu.get():
                    self._batch_format_menu.set("MKV (原画极速)")
            else:
                self._batch_format_menu.configure(values=OUTPUT_FORMAT_OPTIONS)
                if self._batch_format_menu.get() not in OUTPUT_FORMAT_OPTIONS:
                    self._batch_format_menu.set(self.output_format)

        # 4. 汇总刷新顶部的【预估总计】标签
        self._update_total_size()

    _on_hdr_toggle = _on_toggle_hdr_checkbox

    def _on_batch_subtitle_preset_change(self, preset_name: str):
        """用户在全局控制栏切换【默认字幕】时，将未被单独修改过的卡片自动同步为最匹配的字幕选项。"""
        self.default_subtitle = preset_name
        self.enable_subtitles = bool(preset_name != "无字幕")
        update_setting("default_subtitle", preset_name)
        update_setting("enable_subtitles", self.enable_subtitles)

        for task in self.batch_tasks:
            # 仅修改未被单独定制过的卡片 (custom_subtitle == False)
            if not task.get("custom_subtitle", False):
                subs = task.get("available_subtitles") or [{"code": "none", "name": "无字幕"}]
                target_code = select_subtitle_by_preset(subs, preset_name)
                task["selected_subtitle"] = target_code
                target_name = next((s["name"] for s in subs if s["code"] == target_code), "无字幕")
                if task.get("sub_menu_widget"):
                    try:
                        task["sub_menu_widget"].set(target_name)
                    except Exception:
                        pass

        # 单视频视图同步
        if getattr(self, "_current_task_view", "batch") == "single" and self._parsed_tasks:
            s_task = self._parsed_tasks[0]
            if not s_task.get("custom_subtitle", False):
                subs = s_task.get("available_subtitles") or [{"code": "none", "name": "无字幕"}]
                target_code = select_subtitle_by_preset(subs, preset_name)
                s_task["selected_subtitle"] = target_code
                target_name = next((s["name"] for s in subs if s["code"] == target_code), "无字幕")
                if hasattr(self, "_single_subtitle_menu"):
                    try:
                        self._single_subtitle_menu.set(target_name)
                    except Exception:
                        pass

    def _on_single_subtitle_change(self, selected_name: str):
        """单视频视图中用户手动选择字幕语言。"""
        if not self._parsed_tasks:
            return
        task = self._parsed_tasks[0]
        task["custom_subtitle"] = True
        subs = task.get("available_subtitles") or []
        code = next((s["code"] for s in subs if s["name"] == selected_name), "none")
        task["selected_subtitle"] = code

    def _on_card_subtitle_change(self, selected_name: str, task: dict):
        """用户在批量队列卡片上单独选择字幕语言。"""
        task["custom_subtitle"] = True
        subs = task.get("available_subtitles") or []
        code = next((s["code"] for s in subs if s["name"] == selected_name), "none")
        task["selected_subtitle"] = code

    _on_subtitle_toggle = _on_batch_subtitle_preset_change

    def _on_notify_toggle(self):
        """用户勾选/取消【完成通知】：即时更新并持久化。"""
        val = bool(self.notify_var.get()) if hasattr(self, "notify_var") else True
        self.enable_notification = val
        update_setting("enable_notification", val)

    def _send_macos_notification(self, title: str, message: str):
        """发送 macOS 系统原生通知横幅，并伴随 Glass 提示音。"""
        if not getattr(self, "enable_notification", True):
            return
        try:
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{safe_msg}" with title "{safe_title}" sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False)
        except Exception as e:
            print(f"[通知发送异常] {e}")

    def _reveal_in_finder(self, task_or_path=None):
        """在 macOS 访达中高亮定位文件。"""
        path = None
        if isinstance(task_or_path, str):
            path = task_or_path
        elif isinstance(task_or_path, dict):
            path = task_or_path.get("final_filepath")
        elif task_or_path is None:
            path = getattr(self, "_last_download_path", None)

        if path and os.path.exists(path):
            try:
                subprocess.run(["open", "-R", path], check=False)
                return
            except Exception as e:
                print(f"[访达定位失败] {e}")

        # 兜底打开保存文件夹
        if self._save_dir and os.path.exists(self._save_dir):
            try:
                subprocess.run(["open", self._save_dir], check=False)
            except Exception:
                pass

    def _on_batch_preset_change(self, preset_name: str | None = None):
        """批量模式下切换全局预设或 HDR 开关时，仅更新未被用户单独修改过的视频卡片。"""
        if preset_name is None:
            preset_name = self._batch_preset_menu.get() if hasattr(self, "_batch_preset_menu") else "最高画质"
        prefer_hdr = bool(self.hdr_var.get()) if hasattr(self, "hdr_var") else False

        for task in self.batch_tasks:
            # 仅修改未被单独定制过的视频 (custom_quality == False)
            if not task.get("custom_quality", False):
                opts = task.get("quality_options") or task.get("qualities") or []
                target_q = self._find_matching_quality(opts, preset_name, prefer_hdr=prefer_hdr)
                task["selected_quality"] = target_q
                task["prefer_hdr"] = prefer_hdr
                if task.get("menu_widget") and target_q.get("label"):
                    try:
                        task["menu_widget"].set(target_q["label"])
                    except Exception:
                        pass
                self._sync_format_with_quality(task)

        # 单视频视图同步
        if getattr(self, "_current_task_view", "batch") == "single" and self._parsed_tasks:
            s_task = self._parsed_tasks[0]
            if not s_task.get("custom_quality", False):
                opts = s_task.get("quality_options") or s_task.get("qualities") or []
                target_q = self._find_matching_quality(opts, preset_name, prefer_hdr=prefer_hdr)
                if target_q:
                    s_task["selected_quality"] = target_q
                    s_task["prefer_hdr"] = prefer_hdr
                    if hasattr(self, "_quality_menu") and target_q.get("label"):
                        try:
                            self._quality_menu.set(target_q["label"])
                        except Exception:
                            pass
                    self._sync_format_with_quality(s_task)

        self._update_total_size()

    def _on_card_quality_change(self, selected_label: str, task: dict):
        """用户在卡片内手动切换下拉选项时，标记 custom_quality 并刷新体积与格式联动。"""
        task["custom_quality"] = True
        opts = task.get("quality_options") or task.get("qualities") or []
        matching_q = next((q for q in opts if q.get("label") == selected_label), None)
        if matching_q:
            task["selected_quality"] = matching_q
            task["prefer_hdr"] = bool(matching_q.get("is_hdr", False)) or ("hdr" in str(matching_q.get("label", "")).lower())
            if task.get("menu_widget"):
                try:
                    task["menu_widget"].set(selected_label)
                except Exception:
                    pass
            self._sync_format_with_quality(task)

    def _delete_queue_item(self, target_task: dict):
        """从任务队列中剔除指定视频，销毁对应卡片并重排序号与总大小。"""
        if self._is_downloading:
            messagebox.showwarning("提示", "当前正在执行下载任务，无法移除视频！")
            return

        if target_task in self._parsed_tasks:
            self._parsed_tasks.remove(target_task)
        self._queue_row_widgets = [w for w in self._queue_row_widgets if w.get("task") != target_task]

        # 销毁卡片界面组件
        card = target_task.get("card_widget")
        if card:
            try:
                card.destroy()
            except Exception:
                pass

        # 序号重排与动态联动
        for idx, t in enumerate(self._parsed_tasks):
            if t.get("index_label"):
                try:
                    t["index_label"].configure(text=f"{idx + 1}.")
                except Exception:
                    pass

        # 更新队列计数与开始下载按钮文本
        remain_count = len(self._parsed_tasks)
        self._batch_count_label.configure(text=f"任务队列 (共 {remain_count} 个视频)")

        if remain_count > 0:
            self._download_btn.configure(state="normal", text=f"开始下载队列 (共 {remain_count} 个)")
        else:
            self._download_btn.configure(state="disabled", text="开始下载")
            self._set_status("队列已清空", "info")

        # 重新汇总并刷新顶部的“预估总计”体积
        self._update_total_size()

        # 强制 Canvas 重新计算完整内容的几何高度，激活滚动能力
        self.update_idletasks()
        canvas = getattr(self.queue_frame, "_parent_canvas", None)
        if canvas:
            canvas.configure(scrollregion=canvas.bbox("all"))
        self._setup_trackpad_scroll()

    # ── 视频批量解析流程（独立后台线程）─────────────────────────

    def _start_parse(self):
        raw_text = self._url_textbox.get("1.0", "end").strip()
        if not raw_text:
            messagebox.showwarning("提示", "请先输入 YouTube 视频或播放列表链接！")
            return
        if self._is_parsing or self._is_downloading:
            return

        self._is_parsing = True
        self._parse_btn.configure(state="disabled", text="解析中...")
        self._url_textbox.configure(state="disabled")
        self._clear_btn.configure(state="disabled")
        self._download_btn.configure(state="disabled")
        if hasattr(self, "_hdr_checkbox"):
            self._hdr_checkbox.configure(state="disabled")
        if hasattr(self, "_batch_subtitle_menu"):
            self._batch_subtitle_menu.configure(state="disabled")
        if hasattr(self, "_notify_checkbox"):
            self._notify_checkbox.configure(state="disabled")
        if hasattr(self, "_single_subtitle_menu"):
            self._single_subtitle_menu.configure(state="disabled")
        if hasattr(self, "_single_notify_checkbox"):
            self._single_notify_checkbox.configure(state="disabled")
        if hasattr(self, "_batch_preset_menu"):
            self._batch_preset_menu.configure(state="disabled")
        self._set_status("正在解析视频可用画质与数据，请稍候...", "info")

        threading.Thread(target=self._run_parse_worker, args=(raw_text,), daemon=True).start()

    def _run_parse_worker(self, raw_text: str):
        def _prog_cb(idx: int, total: int, msg: str):
            self.after(0, lambda m=msg: self._set_status(m, "info"))

        try:
            results = parse_playlist_or_videos(raw_text, on_progress=_prog_cb)
        except Exception as exc:
            self.after(0, self._on_parse_error, str(exc))
            return

        valid_results = [r for r in results if r.get("id")]
        failed_results = [r for r in results if not r.get("id")]

        if not valid_results:
            if failed_results:
                if len(failed_results) == 1:
                    first_err = failed_results[0].get("error", "未知错误")
                    self.after(0, self._on_parse_error, first_err)
                    return
                else:
                    err_lines = [f"• {f.get('url', '')}: {f.get('error', '')}" for f in failed_results[:3]]
                    detail = "\n".join(err_lines)
                    self.after(0, self._on_parse_error, f"所有链接在重试 3 次后均无法解析：\n\n{detail}")
                    return
            self.after(0, self._on_parse_error,
                       "未解析到任何有效视频。\n\n请检查链接是否正确，或等待片刻后重试。")
            return

        self.after(0, self._on_parse_success, valid_results, failed_results)

    def _on_parse_success(self, results: list[dict], failed_results: list[dict] | None = None):
        self._is_parsing = False
        self._parse_btn.configure(state="normal", text="解析")
        self._url_textbox.configure(state="normal")
        self._clear_btn.configure(state="normal")
        if hasattr(self, "_hdr_checkbox"):
            self._hdr_checkbox.configure(state="normal")
        if hasattr(self, "_batch_subtitle_menu"):
            self._batch_subtitle_menu.configure(state="normal")
        if hasattr(self, "_notify_checkbox"):
            self._notify_checkbox.configure(state="normal")
        if hasattr(self, "_single_subtitle_menu"):
            self._single_subtitle_menu.configure(state="normal")
        if hasattr(self, "_single_notify_checkbox"):
            self._single_notify_checkbox.configure(state="normal")
        if hasattr(self, "_batch_preset_menu"):
            self._batch_preset_menu.configure(state="normal")
        self._parsed_tasks = results

        # 根据当前全局预设与 HDR 偏好匹配默认画质，并初始化数据结构
        prefer_hdr = bool(self.hdr_var.get()) if hasattr(self, "hdr_var") else False
        current_preset = self._batch_preset_menu.get() if hasattr(self, "_batch_preset_menu") else "最高画质"
        for t in self._parsed_tasks:
            t["url"] = t.get("url")
            t["info"] = t
            t["quality_options"] = t.get("qualities") or []
            t["custom_quality"] = False
            t["prefer_hdr"] = prefer_hdr
            t["available_subtitles"] = t.get("available_subtitles") or [{"code": "none", "name": "无字幕"}]
            t["custom_subtitle"] = False
            t["selected_subtitle"] = select_subtitle_by_preset(t["available_subtitles"], self.default_subtitle)
            t["final_filepath"] = None
            t["selected_quality"] = self._find_matching_quality(
                t["quality_options"], current_preset, prefer_hdr=prefer_hdr
            )
            t["size_badge"] = None
            t["menu_widget"] = None
            t["sub_menu_widget"] = None
            t["status_badge"] = None
            t["card_widget"] = None
            t["index_label"] = None
            t["delete_btn"] = None

        success_count = len(results)
        fail_count = len(failed_results) if failed_results else 0

        # 单视频视图展示
        if len(results) == 1:
            info = results[0]
            self._qualities = info.get("qualities", [])
            self._title_label.configure(text=info.get("title", "未知标题"))
            self._meta_label.configure(
                text=f"时长: {info.get('duration_str', '--:--')}   •   频道: {info.get('uploader', 'YouTube')}"
            )

            if self._qualities:
                labels = [q["label"] for q in self._qualities]
                self._quality_menu.configure(values=labels, state="normal")
                # 默认单视频选中匹配的画质
                default_q = info.get("selected_quality") or self._qualities[0]
                default_lbl = default_q.get("label", labels[0])
                self._quality_menu.set(default_lbl)
                info["selected_quality"] = default_q
                info["format_menu"] = self._single_format_menu

                # 动态联动格式与体积 Badge
                self._sync_format_with_quality(info)

                # 动态填充单视频字幕下拉菜单
                subs = info.get("available_subtitles") or [{"code": "none", "name": "无字幕"}]
                sub_names = [s["name"] for s in subs]
                self._single_subtitle_menu.configure(values=sub_names, state="normal")
                cur_code = info.get("selected_subtitle", "none")
                cur_name = next((s["name"] for s in subs if s["code"] == cur_code), sub_names[0])
                self._single_subtitle_menu.set(cur_name)
                self._download_btn.configure(state="normal", text="开始下载")
                if fail_count > 0:
                    self._set_status(f"解析完成：成功 {success_count} 个，失败 {fail_count} 个", "warning")
                else:
                    self._set_status("解析完成：可选择画质后开始下载", "success")
            else:
                self._set_status("未能解析到该视频的画质流", "warning")

            self._show_task_view("single")

        # 多视频队列视图展示
        else:
            for task_info in self._parsed_tasks:
                self._sync_format_with_quality(task_info)
            self._render_batch_queue()
            self._download_btn.configure(state="normal", text=f"开始下载队列 (共 {success_count} 个)")
            if fail_count > 0:
                self._set_status(f"解析完成：成功 {success_count} 个，失败 {fail_count} 个", "warning")
            else:
                self._set_status(f"解析完成：共发现 {success_count} 个有效视频", "success")
            self._show_task_view("batch")

        # 如果有失败项，弹窗简要说明（非阻塞，after 延迟避免与 UI 切换冲突）
        if failed_results:
            def _show_fail_dialog():
                lines = []
                has_bot = False
                for item in failed_results[:5]:  # 最多显示前 5 条
                    url_short = item.get("url", "")
                    if len(url_short) > 60:
                        url_short = url_short[:57] + "..."
                    err = item.get("error", "未知错误")
                    # 友好化常见错误信息
                    if "Sign in" in err or "bot" in err.lower() or "confirm" in err.lower() or "登录" in err or "风控" in err:
                        err_friendly = "要求登录验证（反爬风控拦截）"
                        has_bot = True
                    elif "unavailable" in err.lower():
                        err_friendly = "视频不可用（已删除或设为私密）"
                    elif "403" in err:
                        err_friendly = "访问被拒绝 (地区或年龄限制)"
                    else:
                        err_friendly = err.splitlines()[0][:60]
                    lines.append(f"• {url_short}\n  原因: 重试 3 次后仍失败 - {err_friendly}")
                if len(failed_results) > 5:
                    lines.append(f"... 及另外 {len(failed_results) - 5} 条")
                detail = "\n\n".join(lines)
                if has_bot:
                    ans = messagebox.askyesno(
                        "部分链接解析失败 (检测到登录风控)",
                        f"以下 {fail_count} 个链接在自动重试 3 次后未能成功解析，其余 {success_count} 个已加入队列：\n\n{detail}\n\n"
                        "检测到部分链接受 YouTube 登录风控拦截，是否立即打开【Cookie 导入与配置指引】进行配置？",
                        parent=self
                    )
                    if ans:
                        self._show_cookie_guide_dialog()
                else:
                    messagebox.showwarning(
                        "部分链接解析失败",
                        f"以下 {fail_count} 个链接在自动重试 3 次后未能成功解析，其余 {success_count} 个已加入队列：\n\n{detail}",
                        parent=self
                    )
            self.after(400, _show_fail_dialog)

    def _render_batch_queue(self):
        """渲染批量下载队列列表。"""
        for w in self._queue_scroll.winfo_children():
            w.destroy()
        self._queue_row_widgets = []

        canvas = getattr(self._queue_scroll, "_parent_canvas", None)
        total_bytes = 0
        for idx, task_info in enumerate(self._parsed_tasks):
            sel_q = task_info.get("selected_quality") or {}
            q_bytes, q_sz_str = self._get_effective_quality_size(sel_q, task_info)
            total_bytes += q_bytes

            row = ctk.CTkFrame(
                self._queue_scroll,
                fg_color=COLOR_CARD_BG,
                border_color=COLOR_CARD_BORDER,
                border_width=1,
                corner_radius=8,
            )
            row.pack(fill="x", padx=4, pady=3)
            row.grid_columnconfigure(1, weight=1)

            task_info["card_widget"] = row

            # Column 0: 序号
            idx_lbl = ctk.CTkLabel(
                row,
                text=f"{idx + 1}.",
                width=24,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLOR_TEXT_SECONDARY,
            )
            idx_lbl.grid(row=0, column=0, rowspan=2, padx=(8, 4), pady=6)
            task_info["index_label"] = idx_lbl

            # Column 1: 标题与元数据
            title_text = task_info.get("title") or "未知视频"
            if len(title_text) > 38:
                title_text = title_text[:36] + "..."
            title_lbl = ctk.CTkLabel(
                row,
                text=title_text,
                anchor="w",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLOR_TEXT_PRIMARY,
            )
            title_lbl.grid(row=0, column=1, sticky="w", padx=4, pady=(6, 0))

            q_lbl = sel_q.get("label", "")
            meta_lbl = ctk.CTkLabel(
                row,
                text=f"{task_info.get('duration_str', '--:--')}  •  {q_lbl}  •  {task_info.get('uploader', 'YouTube')}",
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=COLOR_TEXT_SECONDARY,
            )
            meta_lbl.grid(row=1, column=1, sticky="w", padx=4, pady=(0, 6))
            task_info["meta_lbl"] = meta_lbl

            # Column 2: 【单视频画质下拉框】
            q_options = task_info.get("quality_options") or task_info.get("qualities") or []
            menu_labels = [q["label"] for q in q_options if q.get("label")]
            if not menu_labels:
                menu_labels = [q_lbl or "默认画质"]

            card_menu = ctk.CTkOptionMenu(
                row,
                values=menu_labels,
                command=lambda val, t=task_info: self._on_card_quality_change(val, t),
                width=100,
                height=26,
                corner_radius=6,
                fg_color="#3A3A3C",
                button_color="#48484A",
                button_hover_color="#5A5A5C",
                text_color="#FFFFFF",
                dropdown_fg_color="#2C2C2E",
                dropdown_hover_color="#3A3A3C",
                dropdown_text_color="#FFFFFF",
                font=ctk.CTkFont(size=11),
                dynamic_resizing=False,
            )
            initial_val = sel_q.get("label") or menu_labels[0]
            card_menu.set(initial_val)
            card_menu.grid(row=0, column=2, rowspan=2, padx=(4, 2), pady=6)
            task_info["menu_widget"] = card_menu

            # Column 3: 【单视频字幕下拉框】
            sub_options = task_info.get("available_subtitles") or [{"code": "none", "name": "无字幕"}]
            sub_names = [s["name"] for s in sub_options] if sub_options else ["无字幕"]
            cur_sub_code = task_info.get("selected_subtitle", "none")
            cur_sub_name = next((s["name"] for s in sub_options if s["code"] == cur_sub_code), sub_names[0])

            sub_menu = ctk.CTkOptionMenu(
                row,
                values=sub_names,
                command=lambda val, t=task_info: self._on_card_subtitle_change(val, t),
                width=95,
                height=26,
                corner_radius=6,
                fg_color="#3A3A3C",
                button_color="#48484A",
                button_hover_color="#5A5A5C",
                text_color="#FFFFFF",
                dropdown_fg_color="#2C2C2E",
                dropdown_hover_color="#3A3A3C",
                dropdown_text_color="#FFFFFF",
                font=ctk.CTkFont(size=11),
                dynamic_resizing=False,
            )
            sub_menu.set(cur_sub_name)
            sub_menu.grid(row=0, column=3, rowspan=2, padx=(2, 4), pady=6)
            task_info["sub_menu_widget"] = sub_menu

            # Column 4: 【预估体积胶囊 Badge】
            sz_badge = ctk.CTkLabel(
                row,
                text=q_sz_str,
                width=62,
                height=24,
                corner_radius=5,
                fg_color=COLOR_BADGE_BG,
                text_color=COLOR_BADGE_TEXT,
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            sz_badge.grid(row=0, column=4, rowspan=2, padx=4, pady=6)
            task_info["size_badge"] = sz_badge

            # Column 5: 【状态徽章 / 访达定位按钮】
            if task_info.get("final_filepath"):
                st_badge = ctk.CTkButton(
                    row,
                    text="已完成 (在访达中定位)",
                    width=118,
                    height=24,
                    corner_radius=6,
                    fg_color="#3A3A3C",
                    hover_color="#48484A",
                    text_color="#34C759",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    state="normal",
                    command=lambda t=task_info: self._reveal_in_finder(t),
                )
            else:
                st_badge = ctk.CTkButton(
                    row,
                    text="等待中",
                    width=60,
                    height=24,
                    corner_radius=6,
                    fg_color=COLOR_BADGE_BG,
                    hover_color=COLOR_BADGE_BG,
                    text_color=COLOR_TEXT_SECONDARY,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    state="disabled",
                )
            st_badge.grid(row=0, column=5, rowspan=2, padx=4, pady=6)
            task_info["status_badge"] = st_badge

            # Column 6: 【✕ 删除按钮】
            del_btn = ctk.CTkButton(
                row,
                text="✕",
                width=26,
                height=26,
                corner_radius=6,
                fg_color="transparent",
                hover_color="#FF453A",
                text_color="#8E8E93",
                font=ctk.CTkFont(family="SF Pro Text", size=12, weight="bold"),
                command=lambda t=task_info: self._delete_queue_item(t),
            )
            del_btn.grid(row=0, column=6, rowspan=2, padx=(2, 8), pady=6)
            task_info["delete_btn"] = del_btn

            self._queue_row_widgets.append({
                "row": row,
                "size_badge": sz_badge,
                "status_badge": st_badge,
                "meta_lbl": meta_lbl,
                "menu_widget": card_menu,
                "sub_menu_widget": sub_menu,
                "delete_btn": del_btn,
                "task": task_info,
            })

            # 双重穿透绑定：直接将卡片容器及其所有子控件显式绑定 MouseWheel 与 TouchpadScroll
            target_canvas = getattr(self.queue_frame, "_parent_canvas", None)
            if target_canvas:
                def _bind_card_scroll(w):
                    def _card_scroll(e, is_tp=False):
                        raw_delta = getattr(e, "delta", 0)
                        ev_type = str(getattr(e, "type", ""))
                        if is_tp or "Touchpad" in ev_type or ev_type == "39" or raw_delta > 1000 or raw_delta < -1000:
                            raw = raw_delta & 0xFFFFFFFF
                            delta_y = raw & 0xFFFF
                            if delta_y >= 0x8000:
                                delta_y -= 0x10000
                            step = -1 * delta_y
                        else:
                            step = -1 * int(raw_delta) * 12 if raw_delta != 0 else 0
                        if step != 0:
                            target_canvas.yview_scroll(step, "units")

                    w.bind("<MouseWheel>", lambda e: _card_scroll(e, False), add="+")
                    try:
                        w.bind("<TouchpadScroll>", lambda e: _card_scroll(e, True), add="+")
                    except Exception:
                        pass
                    for child in w.winfo_children():
                        _bind_card_scroll(child)

                _bind_card_scroll(row)

        self._batch_count_label.configure(text=f"任务队列 (共 {len(self._parsed_tasks)} 个视频)")
        self._batch_total_size_badge.configure(text=f"预估总计: {format_size(total_bytes)}")

        # 强制 Canvas 重新计算完整内容的几何高度，激活滚动能力
        self.update_idletasks()
        canvas = self.queue_frame._parent_canvas
        if canvas:
            canvas.configure(scrollregion=canvas.bbox("all"))

        # 确保在列表添加卡片之后刷新全局滚轮监听
        self._setup_trackpad_scroll()

    def _on_parse_error(self, err_msg: str):
        self._is_parsing = False
        self._parse_btn.configure(state="normal", text="解析")
        self._url_textbox.configure(state="normal")
        self._clear_btn.configure(state="normal")
        if hasattr(self, "_hdr_checkbox"):
            self._hdr_checkbox.configure(state="normal")
        if hasattr(self, "_batch_subtitle_menu"):
            self._batch_subtitle_menu.configure(state="normal")
        if hasattr(self, "_notify_checkbox"):
            self._notify_checkbox.configure(state="normal")
        if hasattr(self, "_single_subtitle_menu"):
            self._single_subtitle_menu.configure(state="normal")
        if hasattr(self, "_single_notify_checkbox"):
            self._single_notify_checkbox.configure(state="normal")
        if hasattr(self, "_batch_preset_menu"):
            self._batch_preset_menu.configure(state="normal")

        first_line = err_msg.splitlines()[0] if err_msg else "解析失败"
        self._set_status(f"解析失败: {first_line}", "error")

        is_bot = (
            "sign in" in err_msg.lower()
            or "bot" in err_msg.lower()
            or "confirm" in err_msg.lower()
            or "登录" in err_msg
            or "风控" in err_msg
            or "429" in err_msg
        )

        if not err_msg.startswith("该视频在重试") and not err_msg.startswith("所有链接在重试") and not err_msg.startswith("未解析到任何有效视频"):
            err_box_msg = f"该视频在重试 3 次后仍无法解析：\n\n{err_msg}"
        else:
            err_box_msg = err_msg

        if is_bot:
            self._set_status("解析遇到 YouTube 登录验证拦截 (反爬风控)", "error")
            ans = messagebox.askyesno(
                "YouTube 登录验证拦截",
                f"{err_box_msg}\n\n检测到未登录受限或机器人验证拦截。配置 Cookie 即可解锁 4K/HDR 原画与防风控验证。\n\n是否立即打开【Cookie 导入与配置指引】？",
                parent=self,
            )
            if ans:
                self._show_cookie_guide_dialog()
            return

        messagebox.showerror("解析失败", err_box_msg)

    # ── 视频下载流程（支持单视频与批量队列）─────────────────────

    def _start_download(self):
        if not self._parsed_tasks:
            messagebox.showwarning("提示", "请先解析视频！")
            return
        if self._is_downloading:
            return

        self._reset_progress()
        self._set_downloading(True)

        if getattr(self, "_current_task_view", "single") == "single" and len(self._parsed_tasks) == 1:
            task_info = self._parsed_tasks[0]
            selected_label = self._quality_menu.get()
            selected_quality = next(
                (q for q in self._qualities if q.get("label") == selected_label),
                task_info.get("selected_quality") or self._qualities[0]
            )
            self._set_status("正在智能探测网络连接与带宽...", "info")
            is_hdr = bool(selected_quality.get("is_hdr", False))
            sub_code = task_info.get("selected_subtitle", "none")
            enable_sub = bool(sub_code != "none" and sub_code)
            task = DownloadTask(
                url=task_info["url"],
                quality=selected_quality,
                save_dir=self._save_dir,
                on_progress=self._cb_progress,
                on_status=self._cb_status,
                on_complete=self._cb_complete,
                on_error=self._cb_error,
                enable_hdr=is_hdr,
                enable_subtitles=enable_sub,
                subtitle_lang=sub_code,
                output_format=task_info.get("output_format") or getattr(self, "output_format", "MP4 (苹果兼容)"),
            )
            self._download_task = task
            task.start()
        else:
            self._queue_cancel_requested = False
            threading.Thread(target=self._run_batch_download_worker, daemon=True).start()

    def _run_batch_download_worker(self):
        """批量队列下载工作线程：依次串行处理，防止网络拥堵与触发风控。"""
        total = len(self._parsed_tasks)
        success_count = 0
        failed_count = 0

        for idx, task_info in enumerate(self._parsed_tasks):
            if self._queue_cancel_requested:
                break

            self.after(0, self._update_queue_item_status, idx, "下载中", COLOR_PRIMARY, "#FFFFFF")
            self.after(0, self._set_status, f"正在下载 ({idx + 1}/{total}): {task_info.get('title')}", "info")

            done_event = threading.Event()
            task_err = [None]

            def _on_prog(data: dict):
                self.after(0, self._apply_queue_progress_ui, idx, total, data)

            def _on_stat(msg: str, lvl: str):
                self.after(0, self._set_status, f"({idx + 1}/{total}) {msg}", lvl)

            def _on_comp(final_path: str | None = None):
                task_info["final_filepath"] = final_path
                done_event.set()

            def _on_err(msg: str):
                task_err[0] = msg
                done_event.set()

            q = task_info.get("selected_quality") or (task_info.get("qualities") or [{}])[0]
            prefer_hdr = task_info.get(
                "prefer_hdr",
                bool(self.hdr_var.get()) if hasattr(self, "hdr_var") else False
            )
            sub_code = task_info.get("selected_subtitle", "none")
            enable_sub = bool(sub_code != "none" and sub_code)
            task = DownloadTask(
                url=task_info["url"],
                quality=q,
                save_dir=self._save_dir,
                on_progress=_on_prog,
                on_status=_on_stat,
                on_complete=_on_comp,
                on_error=_on_err,
                enable_hdr=prefer_hdr,
                enable_subtitles=enable_sub,
                subtitle_lang=sub_code,
                output_format=task_info.get("output_format") or getattr(self, "output_format", "MP4 (苹果兼容)"),
            )
            self._download_task = task
            task.start()

            done_event.wait()

            if self._queue_cancel_requested:
                task_info["status"] = "cancelled"
                self.after(0, self._update_queue_item_status, idx, "已取消", COLOR_STATUS_ERROR, "#FFFFFF")
                break
            elif task_err[0]:
                failed_count += 1
                task_info["status"] = "failed"
                self.after(0, self._update_queue_item_status, idx, "失败", COLOR_STATUS_ERROR, "#FFFFFF")
            else:
                success_count += 1
                task_info["status"] = "completed"
                fp = task_info.get("final_filepath") or getattr(task, "final_output_path", None)
                task_info["final_filepath"] = fp
                self._last_download_path = fp
                self.after(0, self._update_queue_item_status, idx, "已完成", COLOR_STATUS_DONE, "#FFFFFF", fp)
                # 只有队列仅有 1 个视频时触发单视频完成通知；多视频时保持静默
                if total == 1 and getattr(self, "enable_notification", True):
                    v_title = task_info.get("title") or "视频"
                    self._send_macos_notification("视频下载完成", f"{v_title} 已成功保存")

        self.after(0, self._on_batch_queue_finished, success_count, failed_count, self._queue_cancel_requested, total)

    def _cancel_download(self):
        self._queue_cancel_requested = True
        if self._download_task:
            self._download_task.cancel()
        self._set_downloading(False)
        self._reset_progress()
        self._set_status("已取消下载，已自动清理临时文件", "warning")

    # ── 线程安全进度调度 ────────────────────────────────────────

    def _cb_progress(self, data: dict):
        self.after(0, lambda: self._apply_progress_ui(data))

    def _cb_status(self, message: str, level: str):
        self.after(0, lambda: self._set_status(message, level))

    def _cb_complete(self, final_path: str | None = None):
        self.after(0, lambda: self._on_single_download_complete(final_path))

    def _cb_error(self, message: str):
        self.after(0, lambda: self._on_single_download_error(message))

    def _apply_progress_ui(self, data: dict):
        percent = float(data.get("percent", 0.0))
        speed_str = data.get("speed_str")
        if not speed_str:
            speed_mb = float(data.get("speed_mb", 0.0))
            speed_str = f"{speed_mb:.2f} MB/s"
        eta_str = str(data.get("eta_str", "--:--"))

        bar_val = min(max(percent / 100.0, 0.0), 1.0)
        self._progress_bar.set(bar_val)
        self._pct_label.configure(text=f"{percent:.1f}%")
        self._speed_label.configure(text=speed_str)
        self._eta_label.configure(text=f"预计剩余 {eta_str}")

    def _apply_queue_progress_ui(self, idx: int, total: int, data: dict):
        percent = float(data.get("percent", 0.0))
        overall_pct = ((idx + percent / 100.0) / total) * 100.0
        speed_str = data.get("speed_str") or f"{float(data.get('speed_mb', 0.0)):.2f} MB/s"
        eta_str = str(data.get("eta_str", "--:--"))

        bar_val = min(max(overall_pct / 100.0, 0.0), 1.0)
        self._progress_bar.set(bar_val)
        self._pct_label.configure(text=f"{overall_pct:.1f}% ({idx + 1}/{total})")
        self._speed_label.configure(text=speed_str)
        self._eta_label.configure(text=f"预计剩余 {eta_str}")

    def _update_queue_item_status(self, idx: int, text: str, bg_color, text_color: str = "#FFFFFF", final_filepath: str | None = None):
        if 0 <= idx < len(self._queue_row_widgets):
            row_dict = self._queue_row_widgets[idx]
            badge = row_dict["status_badge"]
            task = row_dict.get("task", {})
            if final_filepath:
                task["final_filepath"] = final_filepath

            if text == "已完成":
                badge.configure(
                    text="已完成 (在访达中定位)",
                    width=118,
                    fg_color="#3A3A3C",
                    hover_color="#48484A",
                    text_color="#34C759",
                    state="normal",
                    command=lambda t=task: self._reveal_in_finder(t),
                )
            else:
                badge.configure(
                    text=text,
                    width=68,
                    fg_color=bg_color,
                    hover_color=bg_color,
                    text_color=text_color,
                    state="disabled",
                    command=None,
                )

    def _on_single_download_complete(self, final_path: str | None = None):
        self._set_downloading(False)
        self._progress_bar.set(1.0)
        self._pct_label.configure(text="100%")
        self._eta_label.configure(text="完成")
        self._set_status("下载完成：视频已成功保存", "success")

        resolved_path = final_path or getattr(self._download_task, "final_output_path", None)
        if self._parsed_tasks:
            self._parsed_tasks[0]["final_filepath"] = resolved_path
        self._last_download_path = resolved_path

        if getattr(self, "enable_notification", True):
            v_title = self._parsed_tasks[0].get("title", "视频") if self._parsed_tasks else "视频"
            self._send_macos_notification("视频下载完成", f"{v_title} 已成功保存")

        messagebox.showinfo("下载完成", f"视频已成功下载并完成混流！\n保存至：\n{self._save_dir}")

    def _on_single_download_error(self, message: str):
        self._set_downloading(False)
        self._set_status(f"下载失败: {message.splitlines()[0]}", "error")
        is_bot = (
            "sign in" in message.lower()
            or "bot" in message.lower()
            or "confirm" in message.lower()
            or "登录" in message
            or "风控" in message
            or "429" in message
        )
        if is_bot:
            self._set_status("下载遇到 YouTube 登录验证拦截 (反爬风控)", "error")
            ans = messagebox.askyesno(
                "YouTube 登录验证拦截",
                f"视频下载中断：YouTube 要求登录验证 (反爬风控拦截)。\n\n配置浏览器 Cookie 凭据即可解锁 4K/HDR 原画与防风控限制。\n\n是否立即打开【Cookie 导入与配置指引】？",
                parent=self,
            )
            if ans:
                self._show_cookie_guide_dialog()
            return

        messagebox.showerror("下载失败", message)

    def _on_batch_queue_finished(self, success_count: int, failed_count: int, was_cancelled: bool, total_count: int | None = None):
        self._set_downloading(False)
        if total_count is None:
            total_count = len(self._parsed_tasks)
        if was_cancelled:
            self._set_status("已停止批量队列下载，已自动清理临时文件", "warning")
        elif failed_count == 0:
            self._progress_bar.set(1.0)
            self._pct_label.configure(text="100%")
            self._eta_label.configure(text="完成")
            self._set_status(f"队列下载完成：共 {success_count} 个视频已全部保存", "success")
            if total_count > 1 and getattr(self, "enable_notification", True) and success_count > 0:
                self._send_macos_notification("全部任务已完成", f"共 {success_count} 个视频已全部下载完毕")
            messagebox.showinfo("下载完成", f"队列中的 {success_count} 个视频已全部成功下载！\n保存至：\n{self._save_dir}")
        else:
            self._set_status(f"队列处理完毕：{success_count} 个成功，{failed_count} 个失败", "warning")
            if total_count > 1 and getattr(self, "enable_notification", True) and success_count > 0:
                self._send_macos_notification("全部任务已完成", f"共 {success_count} 个视频已全部下载完毕")
            messagebox.showwarning(
                "下载完成",
                f"队列处理完毕：\n成功：{success_count} 个\n失败：{failed_count} 个\n\n已保存至：\n{self._save_dir}"
            )

    def _set_status(self, message: str, level: str = "info"):
        color_map = {
            "success": COLOR_STATUS_DONE,
            "error":   COLOR_STATUS_ERROR,
            "warning": ("#E5A100", "#FF9F0A"),
            "info":    COLOR_TEXT_SECONDARY,
        }
        color = color_map.get(level, COLOR_TEXT_SECONDARY)
        self._status_label.configure(text=message, text_color=color)

    def _set_downloading(self, state: bool):
        self._is_downloading = state
        if state:
            self._download_btn.configure(state="disabled", text="下载中...")
            self._cancel_btn.configure(
                state="normal",
                fg_color=COLOR_DANGER,
                hover_color=COLOR_DANGER_HOVER,
                text_color="#FFFFFF",
            )
            self._parse_btn.configure(state="disabled")
            self._clear_btn.configure(state="disabled")
            self._quality_menu.configure(state="disabled")
            if hasattr(self, "_hdr_checkbox"):
                self._hdr_checkbox.configure(state="disabled")
            if hasattr(self, "_batch_subtitle_menu"):
                self._batch_subtitle_menu.configure(state="disabled")
            if hasattr(self, "_notify_checkbox"):
                self._notify_checkbox.configure(state="disabled")
            if hasattr(self, "_single_subtitle_menu"):
                self._single_subtitle_menu.configure(state="disabled")
            if hasattr(self, "_single_notify_checkbox"):
                self._single_notify_checkbox.configure(state="disabled")
            if hasattr(self, "_batch_preset_menu"):
                self._batch_preset_menu.configure(state="disabled")
            for t in self._parsed_tasks:
                if t.get("menu_widget"):
                    try:
                        t["menu_widget"].configure(state="disabled")
                    except Exception:
                        pass
                if t.get("sub_menu_widget"):
                    try:
                        t["sub_menu_widget"].configure(state="disabled")
                    except Exception:
                        pass
                if t.get("delete_btn"):
                    try:
                        t["delete_btn"].configure(state="disabled")
                    except Exception:
                        pass
        else:
            self._download_btn.configure(state="normal", text="开始下载")
            self._cancel_btn.configure(
                state="disabled",
                fg_color=COLOR_SECONDARY,
                hover_color=COLOR_SECONDARY_HOVER,
                text_color=COLOR_TEXT_PRIMARY,
            )
            self._parse_btn.configure(state="normal")
            self._clear_btn.configure(state="normal")
            self._quality_menu.configure(state="normal")
            if hasattr(self, "_hdr_checkbox"):
                self._hdr_checkbox.configure(state="normal")
            if hasattr(self, "_batch_subtitle_menu"):
                self._batch_subtitle_menu.configure(state="normal")
            if hasattr(self, "_notify_checkbox"):
                self._notify_checkbox.configure(state="normal")
            if hasattr(self, "_single_subtitle_menu"):
                self._single_subtitle_menu.configure(state="normal")
            if hasattr(self, "_single_notify_checkbox"):
                self._single_notify_checkbox.configure(state="normal")
            if hasattr(self, "_batch_preset_menu"):
                self._batch_preset_menu.configure(state="normal")
            for t in self._parsed_tasks:
                if t.get("menu_widget"):
                    try:
                        t["menu_widget"].configure(state="normal")
                    except Exception:
                        pass
                if t.get("sub_menu_widget"):
                    try:
                        t["sub_menu_widget"].configure(state="normal")
                    except Exception:
                        pass
                if t.get("delete_btn"):
                    try:
                        t["delete_btn"].configure(state="normal")
                    except Exception:
                        pass

    def _reset_progress(self, status_text: str | None = None):
        self._progress_bar.set(0)
        self._pct_label.configure(text="0.0%")
        self._speed_label.configure(text="0.00 MB/s")
        self._eta_label.configure(text="预计剩余 --:--")
        if status_text:
            self._set_status(status_text, "info")
