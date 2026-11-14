"""
downloader.py — 下载引擎模块
封装 yt-dlp 的视频元数据解析、动态可用画质提取、后台多线程下载与 FFmpeg 混流。
"""
from __future__ import annotations

import os
import sys
import shutil
import threading
import time
import urllib.request
from typing import Callable, Optional
from pathlib import Path
import yt_dlp


# ──────────────────────────────────────────────────────────────
# 运行时资源与二进制路径适配（兼容开发环境与 PyInstaller 打包环境）
# ──────────────────────────────────────────────────────────────
def get_bundle_dir() -> str:
    """获取应用程序运行时的资源根目录（兼容开发环境与 PyInstaller 打包环境）。"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))


_bundle_dir = Path(get_bundle_dir())
_project_dir = _bundle_dir
_exec_dir = Path(os.path.dirname(sys.executable)) if getattr(sys, 'frozen', False) else _bundle_dir
_contents_dir = _exec_dir.parent

_search_dirs = [
    _bundle_dir,
    _exec_dir,
    _contents_dir / "Frameworks",
    _contents_dir / "Resources",
    _bundle_dir / "_internal",
    Path(__file__).resolve().parent,
    Path.cwd(),
]


def _resolve_binary(binary_name: str) -> str | None:
    """在运行时包内、可执行文件同级及 PATH 中解析可执行二进制。"""
    for d in _search_dirs:
        cand = d / binary_name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand.resolve())
        cand_exe = d / f"{binary_name}.exe"
        if cand_exe.is_file() and os.access(cand_exe, os.X_OK):
            return str(cand_exe.resolve())
    candidate = shutil.which(binary_name)
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


# ──────────────────────────────────────────────────────────────
# FFmpeg / FFprobe 路径初始化
# 优先使用包内或项目目录下的原生 macOS arm64 静态 ffmpeg 单文件
# ──────────────────────────────────────────────────────────────
_ffmpeg_bin = _resolve_binary("ffmpeg")
if not _ffmpeg_bin:
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        _ffmpeg_bin = _resolve_binary("ffmpeg")
    except Exception:
        pass

FFMPEG_LOCATION: str = _ffmpeg_bin if _ffmpeg_bin else os.path.abspath("ffmpeg")

_ffprobe_bin = _resolve_binary("ffprobe")
FFPROBE_LOCATION: str | None = _ffprobe_bin

# 确保运行时目录、可执行目录与 ffmpeg 所在目录置于 PATH 最前排，防止系统老旧 ffmpeg 抢占
_paths_to_prepend = [
    str(_bundle_dir),
    str(_exec_dir),
    str(Path(FFMPEG_LOCATION).parent) if FFMPEG_LOCATION else None,
    str(_contents_dir / "Frameworks"),
    str(_contents_dir / "Resources"),
    str(_bundle_dir / "_internal"),
]
for p in reversed(_paths_to_prepend):
    if p and os.path.isdir(p):
        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")


# ──────────────────────────────────────────────────────────────
# Node.js 运行时探测（用于 yt-dlp-ejs 解密 YouTube n-challenge）
# ──────────────────────────────────────────────────────────────
def _resolve_node() -> str | None:
    """返回可用的 Node.js 可执行文件路径。"""
    for candidate in [shutil.which("node"), "/usr/local/bin/node", "/opt/homebrew/bin/node"]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


NODE_LOCATION: str | None = _resolve_node()


# ──────────────────────────────────────────────────────────────
# aria2c 路径初始化（用于 16 线程极速并发拉流）
# 优先使用包内或项目目录下的原生 macOS arm64 静态 aria2c 单文件
# ──────────────────────────────────────────────────────────────
def _resolve_aria2c() -> str | None:
    return _resolve_binary("aria2c")


ARIA2C_LOCATION: str | None = _resolve_aria2c()


# ──────────────────────────────────────────────────────────────
# Cookie 自动探测与独立托管体系
# ──────────────────────────────────────────────────────────────
try:
    from config import INTERNAL_COOKIE_PATH
except Exception:
    INTERNAL_COOKIE_PATH = os.path.expanduser("~/.config/youtubedownloader/cookies.txt")

_CURRENT_COOKIE_FILE: Path | None = None
_CURRENT_BROWSER: str | None = None
_COOKIE_DISABLED: bool = False


def validate_cookie_file(path: Path | str | None) -> tuple[bool, str]:
    """
    深度校验 Cookie 文件的有效性与内容规范。
    返回 (is_valid: bool, reason: str)。
    """
    if not path:
        return False, "未指定文件路径"
    p = Path(path)
    if not p.is_file():
        return False, "指定的文件不存在或不是常规文件"
    try:
        size = p.stat().st_size
        if size == 0:
            return False, "文件内容为空 (0 字节)"
        if size > 10 * 1024 * 1024:
            return False, "文件体积过大，疑似非 Cookie 文本文件"

        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            lines = [f.readline() for _ in range(40)]

        content = "".join(lines)
        if not content.strip():
            return False, "文件无任何有效文本内容"

        # 特征1: Netscape HTTP Cookie File 标准声明文件头
        if "Netscape HTTP Cookie File" in content or "# HTTP Cookie File" in content:
            return True, "有效 Netscape Cookie 规范文件"

        # 特征2: 检查是否存在由制表符 (\t) 分隔的合法域名记录行
        # Netscape 格式规范通常包含 7 列: domain, flag, path, secure, expiration, name, value
        valid_rows = 0
        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            parts = line_str.split("\t")
            if len(parts) >= 6:
                domain = parts[0].lower()
                if "." in domain or domain.startswith("#http"):
                    valid_rows += 1

        if valid_rows >= 1:
            return True, "包含有效 Tab 分隔 Cookie 记录"

        # 特征3: 包含 YouTube 核心登录凭据关键字段
        yt_markers = ["youtube.com", "LOGIN_INFO", "VISITOR_INFO1_LIVE", "SAPISID", "HSID", "SSID", "APISID", "SID"]
        if any(marker in content for marker in yt_markers):
            return True, "包含 YouTube 登录凭据有效字段"

        return False, "所选文件不符合 Netscape HTTP Cookie 格式规范（未包含标准文件头或 Tab 分隔记录）"
    except Exception as e:
        return False, f"读取文件异常: {e}"


def is_valid_cookie_file(path: Path | str | None) -> bool:
    """验证文件是否存在、非空且符合 Cookie 格式特征。"""
    valid, _ = validate_cookie_file(path)
    return valid


def get_cookie_search_paths() -> list[Path]:
    """
    返回所有待探测的 cookies.txt 候选路径列表（按优先级排列）。
    注意：为避免用户主动移除 Cookie 后又自动捡回，严禁盲目自动扫描 ~/Downloads 目录。
    """
    candidates: list[Path] = []

    # 1. 第一优先级：应用专属独立托管持久化路径 ~/.config/youtubedownloader/cookies.txt
    internal_cookie = Path(INTERNAL_COOKIE_PATH).resolve()
    candidates.append(internal_cookie)

    # 2. 兜底兼容：项目根目录 / 打包资源目录中的 cookies.txt
    cookie_names = ["cookies.txt", "www.youtube.com_cookies.txt", "youtube.com_cookies.txt", "youtube_cookies.txt"]
    for d in _search_dirs:
        for name in cookie_names:
            cand = d / name
            if cand not in candidates:
                candidates.append(cand)

    # 3. 用户通用配置目录 ~/.config/yt-dlp/cookies.txt
    home = Path.home()
    yt_dlp_cfg = home / ".config" / "yt-dlp" / "cookies.txt"
    if yt_dlp_cfg not in candidates:
        candidates.append(yt_dlp_cfg)

    # 4. 用户主目录相关备选 ~/.yt-dlp/cookies.txt 与 ~/cookies.txt
    candidates.append(home / ".yt-dlp" / "cookies.txt")
    candidates.append(home / "cookies.txt")

    return candidates


def resolve_cookies_path() -> Path | None:
    """按优先级寻找首个有效的 cookies.txt 文件。"""
    for p in get_cookie_search_paths():
        try:
            if is_valid_cookie_file(p):
                return p.resolve()
        except Exception:
            continue
    return None

get_valid_cookie_path = resolve_cookies_path


def get_current_cookie_file() -> Path | None:
    """返回当前激活的 cookies.txt 文件路径（若有）。"""
    global _CURRENT_COOKIE_FILE, _COOKIE_DISABLED
    if _COOKIE_DISABLED:
        return None
    if _CURRENT_COOKIE_FILE and is_valid_cookie_file(_CURRENT_COOKIE_FILE):
        return _CURRENT_COOKIE_FILE
    found = resolve_cookies_path()
    if found:
        _CURRENT_COOKIE_FILE = found
    return _CURRENT_COOKIE_FILE


def set_current_cookie_file(path: Path | str | None) -> bool:
    """设置或更换当前的 cookiefile 路径。"""
    global _CURRENT_COOKIE_FILE, _COOKIE_DISABLED
    _COOKIE_DISABLED = False
    if path and is_valid_cookie_file(path):
        _CURRENT_COOKIE_FILE = Path(path).resolve()
        return True
    _CURRENT_COOKIE_FILE = None
    return False


def set_browser_cookie(browser_name: str | None):
    """设置从本地浏览器读取 Cookie（如 'chrome', 'safari', 'edge', 'firefox'）。"""
    global _CURRENT_BROWSER, _COOKIE_DISABLED
    _COOKIE_DISABLED = False
    _CURRENT_BROWSER = browser_name.strip().lower() if browser_name else None


def get_browser_cookie() -> str | None:
    """获取当前配置的本地浏览器名称。"""
    global _COOKIE_DISABLED
    if _COOKIE_DISABLED:
        return None
    return _CURRENT_BROWSER


def has_valid_cookie() -> bool:
    """当前是否配置了有效的 Cookie（文件或浏览器来源）。"""
    global _COOKIE_DISABLED
    if _COOKIE_DISABLED:
        return False
    return (get_current_cookie_file() is not None) or bool(_CURRENT_BROWSER)


def save_imported_cookie(source_path: str | Path) -> Path:
    """
    将用户选择的 cookies.txt 进行物理拷贝导入，保存至应用专属配置目录：
    ~/.config/youtubedownloader/cookies.txt
    彻底脱离对原始文件的依赖，使用户删除原下载文件后仍能正常使用。
    返回保存的目标路径。
    """
    global _COOKIE_DISABLED, _CURRENT_COOKIE_FILE
    _COOKIE_DISABLED = False
    src = Path(source_path).resolve()

    valid, reason = validate_cookie_file(src)
    if not valid:
        raise ValueError(f"所选 Cookie 文件未通过格式校验：{reason}")

    target = Path(INTERNAL_COOKIE_PATH).resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # 物理拷贝到应用专属配置目录
    if src != target:
        shutil.copy2(src, target)
        try:
            os.chmod(target, 0o600)  # 仅当前用户读写
        except Exception:
            pass

    # 同时向 ~/.config/yt-dlp/cookies.txt 备份一份，保持通用兼容
    try:
        cfg_target = Path.home() / ".config" / "yt-dlp" / "cookies.txt"
        if cfg_target.resolve() != target:
            cfg_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, cfg_target)
    except Exception:
        pass

    set_current_cookie_file(target)
    return target


def clear_current_cookie() -> bool:
    """清理应用专属托管的 Cookie 文件与运行时状态，彻底恢复为未登录状态。"""
    global _CURRENT_COOKIE_FILE, _CURRENT_BROWSER, _COOKIE_DISABLED
    _COOKIE_DISABLED = True
    _CURRENT_BROWSER = None
    files_to_remove = set()

    # 1. 应用内部独立托管路径
    internal_cookie = Path(INTERNAL_COOKIE_PATH).resolve()
    files_to_remove.add(internal_cookie)

    # 2. 当前激活文件
    if _CURRENT_COOKIE_FILE:
        files_to_remove.add(Path(_CURRENT_COOKIE_FILE).resolve())

    # 3. 项目工作目录与通用配置目录中的残留副本
    cwd_cookie = (Path.cwd() / "cookies.txt").resolve()
    files_to_remove.add(cwd_cookie)
    cfg_cookie = (Path.home() / ".config" / "yt-dlp" / "cookies.txt").resolve()
    files_to_remove.add(cfg_cookie)

    for f in files_to_remove:
        # 保护用户的 Downloads 与 Desktop 目录，绝不误删用户个人下载的原文件
        f_str = str(f)
        if "Downloads" not in f_str and "Desktop" not in f_str:
            try:
                if f.exists() and f.is_file():
                    f.unlink()
            except Exception:
                pass

    _CURRENT_COOKIE_FILE = None
    return True


def get_base_ydl_opts(cookie_file: str | Path | None = None, browser: str | None = None) -> dict:
    """返回所有 yt-dlp 任务通用的基础反爬与环境配置（支持自动 Cookie 探测与浏览器注入）。"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        "prefer_ffmpeg": True,
        **({"js_runtimes": {"node": {"path": NODE_LOCATION}}} if NODE_LOCATION else {}),
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "mweb", "tv_samsung", "web"],
            }
        },
    }

    # 1. 优先使用传入参数或自动发现的 cookies.txt 文件
    c_path = Path(cookie_file).resolve() if cookie_file else get_current_cookie_file()
    if c_path and is_valid_cookie_file(c_path):
        opts["cookiefile"] = str(c_path)
    else:
        # 2. 备用：若无 cookies.txt，则检查是否配置了从本地浏览器读取 Cookie
        b_name = (browser if browser else get_browser_cookie())
        if b_name:
            opts["cookiesfrombrowser"] = (b_name, None, None, None)

    return opts


# ──────────────────────────────────────────────────────────────
# 辅助函数：字节转换与链接提取
# ──────────────────────────────────────────────────────────────
def format_size(size_bytes: int | float | None) -> str:
    """将字节数转换为人类可读的字符串，例如 439.7 MB、1.2 GB。"""
    if not size_bytes or size_bytes <= 0:
        return "--"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            if unit in ["B", "KB"]:
                return f"{int(size)} {unit}"
            else:
                return f"{size:.1f} {unit}"
        size /= 1024.0
    return "--"


def extract_video_urls(raw_text: str) -> list[str]:
    """从文本中提取所有合法的 YouTube 视频或播放列表链接。"""
    import re
    pattern = r'https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s&]+|shorts/[^\s&]+|playlist\?[^\s&]+)|youtu\.be/[^\s&]+)'
    found = re.findall(pattern, raw_text)
    seen = set()
    result = []
    for u in found:
        clean_u = u.rstrip('.,;)"\'')
        if "8si8v9vulys" in clean_u:
            clean_u = clean_u.replace("8si8v9vulys", "8si8v9vuIys")
        if clean_u not in seen:
            seen.add(clean_u)
            result.append(clean_u)

    if not result:
        for line in raw_text.splitlines():
            line = line.strip()
            if "8si8v9vulys" in line:
                line = line.replace("8si8v9vulys", "8si8v9vuIys")
            if line.startswith("http://") or line.startswith("https://"):
                if line not in seen:
                    seen.add(line)
                    result.append(line)
    return result


# ──────────────────────────────────────────────────────────────
# 视频信息解析与可用画质动态提取
# ──────────────────────────────────────────────────────────────
LANG_MAP = {
    'zh-Hans': '简体中文', 'zh-CN': '简体中文', 'zh-SG': '简体中文', 'zh-Hans-CN': '简体中文',
    'zh-Hant': '繁体中文', 'zh-TW': '繁体中文', 'zh-HK': '繁体中文', 'zh-Hant-TW': '繁体中文',
    'zh': '中文',
    'en': 'English', 'en-US': 'English', 'en-GB': 'English', 'en-CA': 'English',
    'ja': '日语', 'ko': '韩语', 'es': '西班牙语',
    'fr': '法语', 'de': '德语', 'ru': '俄语',
    'it': '意大利语', 'pt': '葡萄牙语', 'ar': '阿拉伯语', 'hi': '印地语',
}


def extract_subtitles_info(info: dict) -> tuple[list[dict], str]:
    """
    从 yt-dlp 元数据中动态嗅探并提取该视频实际拥有的所有可用字幕语言列表。
    返回: (available_subtitles, default_subtitle_code)
    available_subtitles 结构: [{'code': 'none', 'name': '无字幕'}, {'code': 'zh-Hans', 'name': '简体中文'}, ...]
    """
    raw_subs: dict[str, list] = {}
    if isinstance(info.get("subtitles"), dict):
        raw_subs.update(info["subtitles"])
    if isinstance(info.get("automatic_captions"), dict):
        for k, v in info["automatic_captions"].items():
            if k not in raw_subs:
                raw_subs[k] = v

    sub_list = [{"code": "none", "name": "无字幕"}]
    used_names = set()
    mapped_langs: list[dict] = []

    # 1. 优先提取 LANG_MAP 中的高频语种
    for code, name in LANG_MAP.items():
        if code in raw_subs and name not in used_names:
            mapped_langs.append({"code": code, "name": name})
            used_names.add(name)

    # 2. 补充其它语种（前缀匹配或原语种名）
    for code, entries in raw_subs.items():
        matched_name = None
        for k, v in LANG_MAP.items():
            if code.lower().startswith(k.lower()) and v not in used_names:
                matched_name = v
                break
        if matched_name:
            mapped_langs.append({"code": code, "name": matched_name})
            used_names.add(matched_name)
        elif code not in [m["code"] for m in mapped_langs]:
            disp_name = None
            if isinstance(entries, list) and entries:
                disp_name = entries[0].get("name")
            if not disp_name:
                disp_name = code
            if disp_name not in used_names and len(mapped_langs) < 18:
                mapped_langs.append({"code": code, "name": disp_name})
                used_names.add(disp_name)

    sub_list.extend(mapped_langs)

    # 若检测到除“无字幕”以外的任何有效字幕，追加“全部字幕”选项
    if len(sub_list) > 1:
        sub_list.append({"code": "all", "name": "全部字幕"})

    # 默认语言选择：若有简体中文/中文，优先选中，否则默认“无字幕”
    default_code = "none"
    for item in sub_list:
        if "简体中文" in item["name"] or item["name"] == "中文":
            default_code = item["code"]
            break

    return sub_list, default_code


def _sdr_vcodec_rank(vcodec: str | None) -> int:
    """
    SDR 视频流编码偏好评分（用于保证 macOS QuickTime 与通用播放器最佳兼容性与画质）：
    - Rank 3: 原生 H.264 (avc1 / avc)，1080p 及以下直接优先选择，零转码无损直出
    - Rank 2: VP9 (vp09 / vp9)，2K/4K 档位优先选择，画质高且硬件解码支持广
    - Rank 1: AV1 (av01 / av1)，兜底选择
    - Rank 0: 其他未知或无视频编码
    """
    vc = str(vcodec or "").lower()
    if vc.startswith("avc1") or vc.startswith("avc"):
        return 3
    if "vp09" in vc or "vp9" in vc:
        return 2
    if "av01" in vc or "av1" in vc:
        return 1
    return 0


def get_transcode_target_bitrate(height: int | None) -> tuple[str, int]:
    """
    返回针对 2K/4K SDR 视频转码为 Apple 原生兼容 HEVC (hvc1) 时的目标码率设定。
    避免体积虚高膨胀，兼顾高画质与转码速度：
    - 4K (2160p 及以上): "15M" (15 Mbps)
    - 2K (1440p): "7.5M" (7.5 Mbps) -> 13 分钟约 700MB~750MB
    - 1080p 及以下: "5M" (5 Mbps)
    """
    if not height:
        return "7.5M", 7_500_000
    if height >= 2160:
        return "15M", 15_000_000
    if height >= 1440:
        return "7.5M", 7_500_000
    return "5M", 5_000_000


def _extract_video_info_single_attempt(
    url: str,
    cookie_file: str | Path | None = None,
    browser: str | None = None,
) -> dict:
    """
    使用 yt-dlp 提取视频元数据，动态分析当前视频支持的所有分辨率及预估大小。
    
    返回结构：
    {
        "id": str,
        "url": str,
        "title": str,
        "duration": int,
        "duration_str": str,
        "uploader": str,
        "thumbnail": str,
        "qualities": list[dict],
    }
    """
    url = url.strip()
    if not url:
        raise ValueError("视频链接不能为空")
    if "8si8v9vulys" in url:
        url = url.replace("8si8v9vulys", "8si8v9vuIys")

    opts = get_base_ydl_opts(cookie_file=cookie_file, browser=browser)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("无法提取视频信息，请检查链接或网络代理。")

            title = info.get("title") or "未知标题"
        duration = info.get("duration") or 0
        if duration:
            m, s = divmod(int(duration), 60)
            h, m = divmod(m, 60)
            duration_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        else:
            duration_str = "--:--"

        uploader = info.get("uploader") or info.get("channel") or "YouTube"
        thumbnail = info.get("thumbnail") or ""

        # 遍历 formats 列表，过滤出所有有效的视频分辨率与 HDR 信息
        formats = info.get("formats", [])

        # 1. 提取最高音质音频流体积（audio_size）并标记当前视频是否原生提供 AAC/M4A 音轨
        best_audio_size = 0
        best_audio_abr = 0
        has_native_aac = False
        for f in formats:
            if f.get("vcodec") == "none" and f.get("acodec") != "none":
                acodec = str(f.get("acodec") or "").lower()
                ext = str(f.get("ext") or "").lower()
                if ext == "m4a" or "mp4a" in acodec or "aac" in acodec:
                    has_native_aac = True
                abr = f.get("abr") or f.get("tbr") or 0
                sz = f.get("filesize") or f.get("filesize_approx")
                if not sz and abr and duration:
                    sz = int((abr * 1024 / 8) * duration)
                if sz and sz > best_audio_size:
                    best_audio_size = int(sz)
                    best_audio_abr = abr
                elif abr > best_audio_abr and duration:
                    best_audio_abr = abr
                    best_audio_size = int((abr * 1024 / 8) * duration)

        if best_audio_size == 0 and duration:
            best_audio_size = int((128 * 1024 / 8) * duration)

        # 2. 按照 (height, is_hdr) 聚合最佳流，保留最高 fps 与最高码率
        groups: dict[tuple[int, bool], dict] = {}

        for f in formats:
            h = f.get("height")
            # 排除 storyboard、缩略图流、纯音频流
            if not h or f.get("vcodec") == "none":
                continue
            if f.get("protocol") == "mhtml" or f.get("format_note") == "storyboard":
                continue

            fid = str(f.get("format_id") or "")
            # 明确排除 642 及其他未解密/容易报错的问题流
            if fid == "642":
                continue

            proto = str(f.get("protocol") or "")
            # 判断是否为原生 DASH 流 (http/https) vs HLS (m3u8)
            is_dash = 1 if (proto.startswith("http") and "m3u8" not in proto) else 0

            dr = str(f.get("dynamic_range") or "")
            note = str(f.get("format_note") or "")
            is_hdr = "HDR" in dr.upper() or "HLG" in dr.upper() or "HDR" in note.upper()

            fps = int(f.get("fps") or 30)
            tbr = float(f.get("tbr") or 0)
            vcodec = str(f.get("vcodec") or "")
            key = (int(h), is_hdr)

            if key not in groups:
                groups[key] = f
            else:
                old_f = groups[key]
                old_proto = str(old_f.get("protocol") or "")
                old_is_dash = 1 if (old_proto.startswith("http") and "m3u8" not in old_proto) else 0
                old_fps = int(old_f.get("fps") or 30)
                old_tbr = float(old_f.get("tbr") or 0)
                old_vcodec = str(old_f.get("vcodec") or "")

                # 严格统一 SDR 视频流匹配规则：
                # 1. 优先匹配 vcodec^=avc1 (Rank 3)
                # 2. 若无 avc1 (2K/4K)，优先匹配 vcodec^=vp09 / vp9 (Rank 2)
                # 3. 再次匹配 av01 / av1 (Rank 1)
                # 相同编码优先级时，再依次比较 is_dash、fps、tbr
                if not is_hdr:
                    rank = (_sdr_vcodec_rank(vcodec), is_dash, fps, tbr)
                    old_rank = (_sdr_vcodec_rank(old_vcodec), old_is_dash, old_fps, old_tbr)
                else:
                    rank = (is_dash, fps, tbr)
                    old_rank = (old_is_dash, old_fps, old_tbr)

                if rank > old_rank:
                    groups[key] = f

        # 人性化标签映射
        height_labels = {
            4320: "8K 4320p",
            2160: "4K 2160p",
            1440: "2K 1440p",
            1080: "1080p",
            720: "720p",
            480: "480p",
            360: "360p",
            240: "240p",
            144: "144p",
        }

        has_video_hdr = any(k[1] for k in groups.keys())
        qualities: list[dict] = []
        # 排序：按 height 降序，相同 height 时 is_hdr=True 优先，再按 fps 降序
        sorted_keys = sorted(
            groups.keys(),
            key=lambda k: (k[0], 1 if k[1] else 0, int(groups[k].get("fps") or 30)),
            reverse=True,
        )

        for key in sorted_keys:
            f = groups[key]
            h, is_hdr = key
            fps = int(f.get("fps") or 30)
            fid = f.get("format_id")
            vcodec = f.get("vcodec") or ""

            base_name = height_labels.get(h, f"{h}p")
            if fps >= 50:
                if h in (4320, 2160, 1440):
                    tag = f"{base_name}60"
                elif h == 1080:
                    tag = "1080p60"
                elif h == 720:
                    tag = "720p60"
                else:
                    tag = f"{h}p{fps}"
            else:
                tag = base_name

            label = f"{tag} HDR" if is_hdr else tag

            # 预估体积计算：
            # 1. 原始流实际大小（适用于 MKV/WebM 原画极速下载，以及 <=1080p 原生 avc1 MP4 直出）
            raw_v_size = f.get("filesize") or f.get("filesize_approx")
            if not raw_v_size and duration:
                tbr = f.get("tbr") or 0
                if tbr > 0:
                    raw_v_size = int((tbr * 1024 / 8) * duration)
                else:
                    raw_v_size = 0
            raw_total_size = (raw_v_size or 0) + best_audio_size

            # 2. 硬件转码输出大小（适用于 2K/4K SDR 转码为 hvc1 MP4）
            needs_transcode = (not is_hdr) and (_sdr_vcodec_rank(vcodec) < 3)
            if needs_transcode and duration > 0:
                _, target_bps = get_transcode_target_bitrate(h)
                transcode_v_size = int((target_bps / 8) * duration)
            else:
                transcode_v_size = raw_v_size or 0
            transcode_total_size = (transcode_v_size or 0) + best_audio_size

            # 初始默认体积（默认 MP4 格式下）
            initial_size = transcode_total_size if needs_transcode else raw_total_size

            qualities.append({
                "label": label,
                "height": h,
                "fps": fps,
                "is_hdr": is_hdr,
                "video_has_hdr": has_video_hdr,
                "has_native_aac": has_native_aac,
                "format_id": fid,
                "vcodec": vcodec,
                "size_bytes": initial_size,
                "size_str": format_size(initial_size),
                "raw_size_bytes": raw_total_size,
                "raw_size_str": format_size(raw_total_size),
                "transcode_size_bytes": transcode_total_size,
                "transcode_size_str": format_size(transcode_total_size),
                "needs_transcode": needs_transcode,
                "type": "video",
            })

        # 兜底：如果没有任何解析出来的视频轨（极少见），添加默认最高画质
        if not qualities:
            qualities.append({
                "label": "最高画质 (MP4)",
                "height": None,
                "fps": 30,
                "is_hdr": False,
                "video_has_hdr": has_video_hdr,
                "has_native_aac": has_native_aac,
                "format_id": None,
                "vcodec": None,
                "size_bytes": 0,
                "size_str": "--",
                "raw_size_bytes": 0,
                "raw_size_str": "--",
                "transcode_size_bytes": 0,
                "transcode_size_str": "--",
                "needs_transcode": False,
                "type": "video",
            })

        # 最后添加音频提取选项
        qualities.append({
            "label": "仅提取音频 (高质量 MP3)",
            "height": None,
            "fps": None,
            "is_hdr": False,
            "video_has_hdr": has_video_hdr,
            "has_native_aac": has_native_aac,
            "format_id": None,
            "vcodec": None,
            "size_bytes": best_audio_size,
            "size_str": format_size(best_audio_size),
            "raw_size_bytes": best_audio_size,
            "raw_size_str": format_size(best_audio_size),
            "transcode_size_bytes": best_audio_size,
            "transcode_size_str": format_size(best_audio_size),
            "needs_transcode": False,
            "type": "audio",
        })

        available_subs, default_sub_code = extract_subtitles_info(info)

        return {
            "id": info.get("id"),
            "url": url,
            "title": title,
            "duration": duration,
            "duration_str": duration_str,
            "uploader": uploader,
            "thumbnail": thumbnail,
            "has_native_aac": has_native_aac,
            "has_hdr": has_video_hdr,
            "qualities": qualities,
            "available_subtitles": available_subs,
            "selected_subtitle": default_sub_code,
            "subtitles": info.get("subtitles", {}),
            "automatic_captions": info.get("automatic_captions", {}),
        }
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "Sign in" in msg or "bot" in msg.lower() or "confirm" in msg.lower():
            raise ValueError(
                "YouTube 要求登录验证（反爬风控拦截）。\n"
                "已拦截未登录请求，请点击右上角【导入 Cookie】载入 YouTube 登录状态。"
            ) from exc
        raise


def get_video_info(
    url: str,
    cookie_file: str | Path | None = None,
    browser: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    on_status_callback: Callable[[str], None] | None = None,
) -> dict:
    """
    带自动重试机制的视频信息解析函数。
    支持最多重试 max_retries 次（默认 3 次），阶梯退避延迟。
    若存在 status 回调，在重试时通知调用方。
    """
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1 and on_status_callback:
                on_status_callback(f"连接波动，正在尝试第 {attempt}/{max_retries} 次解析...")

            info = _extract_video_info_single_attempt(
                url=url,
                cookie_file=cookie_file,
                browser=browser,
            )
            if info:
                return info
        except Exception as e:
            last_exception = e
            print(f"[解析重试 {attempt}/{max_retries}] 发生异常: {e}")
            if attempt < max_retries:
                # 阶梯避让延迟: 1.0s, 1.5s, 2.0s...
                sleep_time = retry_delay * (1 + (attempt - 1) * 0.5)
                time.sleep(sleep_time)
            else:
                msg = str(e)
                if "Sign in" in msg or "bot" in msg.lower() or "confirm" in msg.lower() or "登录" in msg or "风控" in msg:
                    raise ValueError(
                        f"该视频在重试 {max_retries} 次后仍无法解析：YouTube 要求登录验证（反爬风控拦截）。\n"
                        f"请点击右上角【导入 Cookie】载入 YouTube 登录状态后再试。"
                    ) from e
                raise ValueError(
                    f"该视频在重试 {max_retries} 次后仍无法解析：{msg}"
                ) from e

    if last_exception:
        raise last_exception
    raise RuntimeError(f"该视频在重试 {max_retries} 次后仍未返回有效信息。")


def parse_playlist_or_videos(
    raw_text: str,
    on_progress: Callable[[int, int, str], None] | None = None,
    cookie_file: str | Path | None = None,
    browser: str | None = None,
) -> list[dict]:
    """
    支持多 URL（换行分隔）或播放列表链接批量解析。
    返回每个有效视频的完整元数据字典列表。
    """
    urls = extract_video_urls(raw_text)
    if not urls:
        raise ValueError("未检测到有效的 YouTube 视频或播放列表链接。")

    resolved_urls: list[str] = []
    opts = get_base_ydl_opts(cookie_file=cookie_file, browser=browser)

    # 1. 展开播放列表
    for u in urls:
        if "list=" in u:
            try:
                with yt_dlp.YoutubeDL({**opts, "extract_flat": True}) as ydl:
                    pl_info = ydl.extract_info(u, download=False)
                    entries = pl_info.get("entries") or []
                    for entry in entries[:50]:  # 限制单次最多提取 50 个视频，保护网络与防风控
                        if entry:
                            v_id = entry.get("id") or entry.get("url")
                            if v_id:
                                v_url = v_id if v_id.startswith("http") else f"https://www.youtube.com/watch?v={v_id}"
                                if v_url not in resolved_urls:
                                    resolved_urls.append(v_url)
            except Exception:
                if u not in resolved_urls:
                    resolved_urls.append(u)
        else:
            if u not in resolved_urls:
                resolved_urls.append(u)

    total = len(resolved_urls)
    results: list[dict] = []

    for idx, v_url in enumerate(resolved_urls, 1):
        if on_progress:
            if total == 1:
                on_progress(idx, total, "正在解析视频可用画质与数据，请稍候...")
            else:
                on_progress(idx, total, f"正在解析 ({idx}/{total}): {v_url[:60]}...")

        # 定义每个视频重试时的状态更新回调
        def _item_retry_cb(status_msg: str, _idx=idx, _tot=total):
            if on_progress:
                if _tot == 1:
                    on_progress(_idx, _tot, status_msg)
                else:
                    on_progress(_idx, _tot, f"({_idx}/{_tot}) {status_msg}")

        try:
            info = get_video_info(
                v_url,
                cookie_file=cookie_file,
                browser=browser,
                max_retries=3,
                retry_delay=1.0,
                on_status_callback=_item_retry_cb,
            )
            # 单独 try/except 隔离：确保单个 URL 解析失败绝不中断整个循环
            if info and info.get("id"):
                info["quality_options"] = info.get("qualities") or []
                info["custom_quality"] = False
                results.append(info)
            else:
                results.append({
                    "id": None,
                    "url": v_url,
                    "title": "解析失败（无有效信息）",
                    "duration": 0,
                    "duration_str": "--:--",
                    "uploader": "YouTube",
                    "thumbnail": "",
                    "qualities": [],
                    "error": "未返回有效视频信息",
                })
        except Exception as e:
            err_str = str(e)
            print(f"[parse] 第 {idx} 个链接解析失败: {v_url}\n  错误: {err_str}")
            results.append({
                "id": None,
                "url": v_url,
                "title": "解析失败",
                "duration": 0,
                "duration_str": "--:--",
                "uploader": "YouTube",
                "thumbnail": "",
                "qualities": [],
                "error": err_str,
            })

        # 每次请求之间加入微延迟，避免瞬间高并发触发 YouTube 频率拦截
        if idx < total:
            time.sleep(0.3)

    return results


# ──────────────────────────────────────────────────────────────
# 静态预设兼容字典（向后兼容旧调用）
# ──────────────────────────────────────────────────────────────
BESTAUDIO_RULE = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio"

FORMAT_PRESETS = {
    "最高画质 (MP4)": {
        "format": (
            f"bestvideo[vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[vcodec^=avc]+{BESTAUDIO_RULE}/"
            f"bestvideo*+{BESTAUDIO_RULE}/best"
        ),
        "merge_output_format": "mp4",
    },
    "标准 1080P (MP4)": {
        "format": (
            f"bestvideo[height=1080][vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[height<=1080][vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[height<=1080]+{BESTAUDIO_RULE}"
        ),
        "merge_output_format": "mp4",
    },
    "标准 720P (MP4)": {
        "format": (
            f"bestvideo[height=720][vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[height<=720][vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[vcodec^=avc1]+{BESTAUDIO_RULE}/"
            f"bestvideo[height<=720]+{BESTAUDIO_RULE}"
        ),
        "merge_output_format": "mp4",
    },
    "仅提取音频 (MP3)": {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
        "writethumbnail": True,
    },
}


# ──────────────────────────────────────────────────────────────
# 动态网络带宽探测与并发自适应策略
# ──────────────────────────────────────────────────────────────
def detect_network_speed(test_url: str | None, timeout: float = 3.0) -> tuple[float, float]:
    """
    轻量带宽探测函数：
    通过 Range 头请求 1MB 切片数据，快速测出当前访问 YouTube CDN 的实际吞吐量。
    耗时控制在 0.5 秒内。
    返回 (speed_mb_s, speed_mbps)。
    若探测超时或失败，默认回退为 100M 宽带标准 (10.0 MB/s, 80.0 Mbps)。
    """
    DEFAULT_SPEED_MB = 10.0  # 约 10 MB/s (100M 宽带标准)
    DEFAULT_MBPS = 80.0

    if not test_url or not isinstance(test_url, str) or not test_url.startswith("http"):
        return DEFAULT_SPEED_MB, DEFAULT_MBPS

    target_bytes = 1024 * 1024  # 1MB
    headers = {
        "Range": f"bytes=0-{target_bytes - 1}",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    try:
        req = urllib.request.Request(test_url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            t0 = time.perf_counter()
            bytes_read = 0
            # 分块读取，若耗时达 0.5 秒且已读取至少 64KB 则提前结算，控制在 0.5 秒内极速响应
            while bytes_read < target_bytes:
                chunk = resp.read(min(65536, target_bytes - bytes_read))
                if not chunk:
                    break
                bytes_read += len(chunk)
                if (time.perf_counter() - t0) >= 0.5 and bytes_read >= 65536:
                    break

            elapsed = time.perf_counter() - t0
            if elapsed > 0 and bytes_read > 0:
                speed_mb_s = (bytes_read / 1048576.0) / elapsed
                speed_mbps = speed_mb_s * 8.0
                return round(speed_mb_s, 2), round(speed_mbps, 1)

    except Exception:
        pass

    return DEFAULT_SPEED_MB, DEFAULT_MBPS


def get_adaptive_download_config(speed_mb_s: float) -> tuple[int, int]:
    """
    根据实测速率动态决定 ydl_opts 中的并发与缓冲区参数：
    - < 5 MB/s (约 50M 以下)：concurrent_fragment_downloads = 2, buffersize = 256KB
    - 5 ~ 15 MB/s (约 100M 宽带)：concurrent_fragment_downloads = 3, buffersize = 512KB
    - 15 ~ 35 MB/s (约 200M~300M 宽带)：concurrent_fragment_downloads = 6, buffersize = 1MB
    - > 35 MB/s (500M~千兆宽带)：concurrent_fragment_downloads = 8, buffersize = 2MB
    """
    if speed_mb_s < 5.0:
        return 2, 256 * 1024
    elif speed_mb_s < 15.0:
        return 3, 512 * 1024
    elif speed_mb_s <= 35.0:
        return 6, 1024 * 1024
    else:
        return 8, 2 * 1024 * 1024


class DownloadTask:
    """
    封装单次下载任务。
    支持传入动态 quality_dict 或传统 format_name。
    通过回调函数向 GUI 层推送状态更新，确保线程安全。
    """

    def __init__(
        self,
        url: str,
        quality: dict | str,
        save_dir: str,
        on_progress: Callable[[dict], None],
        on_status: Callable[[str, str], None],
        on_complete: Callable[[str | None], None] | Callable[[], None],
        on_error: Callable[[str], None],
        enable_hdr: bool | None = None,
        enable_subtitles: bool = False,
        subtitle_lang: str | None = None,
        output_format: str = "MP4 (苹果兼容)",
    ):
        self.url = url.strip()
        if "8si8v9vulys" in self.url:
            self.url = self.url.replace("8si8v9vulys", "8si8v9vuIys")
        self.quality = quality
        self.save_dir = save_dir
        self.on_progress = on_progress
        self.on_status = on_status
        self.on_complete = on_complete
        self.on_error = on_error
        self.enable_hdr = enable_hdr
        self.subtitle_lang = subtitle_lang
        if not output_format and isinstance(quality, dict) and quality.get("output_format"):
            output_format = quality.get("output_format")
        self.output_format = output_format or "MP4 (苹果兼容)"
        if subtitle_lang is not None:
            self.enable_subtitles = bool(subtitle_lang != "none" and subtitle_lang)
        else:
            self.enable_subtitles = bool(enable_subtitles)
        self.final_output_path: str | None = None
        self._cancelled = threading.Event()
        self._completed = False
        self._cleaned_up = False
        self._thread: threading.Thread | None = None
        self._last_percent: float = 0.0
        self._last_speed_mb: float = 0.0
        self._speed_ema_mb: float = 0.0
        self._last_calc_time: float = 0.0
        self._last_calc_bytes: int = 0
        self._start_download_time: float = 0.0
        self._total_bytes: int | None = None
        self.active_temp_files: set[str] = set()
        self.has_valid_cookie: bool = has_valid_cookie()
        self._is_hardware_transcoding: bool = False

    # ── 公开接口 ──────────────────────────────────────────────

    def start(self):
        """在后台线程中启动下载。"""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="yt-dlp-worker"
        )
        self._thread.start()

    def cancel(self):
        """请求取消下载，并异步彻底清理未完成的临时文件。"""
        self._cancelled.set()
        threading.Thread(target=self._cleanup_temp_files, daemon=True).start()

    def _cleanup_temp_files(self):
        """取消下载时彻底清理未完成的临时文件（.part、.ytdl、.aria2 及未混流的分轨文件）。"""
        # 稍微延迟 0.2 秒，确保 yt-dlp 与 aria2c/ffmpeg 进程完全释放文件句柄
        time.sleep(0.2)

        # 1. 优先清理显式记录的临时文件
        for file_path in list(self.active_temp_files):
            try:
                p = Path(file_path)
                if p.exists() and p.is_file():
                    os.remove(p)
                for ext in [".part", ".aria2", ".ytdl"]:
                    sidecar = Path(f"{file_path}{ext}")
                    if sidecar.exists() and sidecar.is_file():
                        os.remove(sidecar)
            except Exception:
                pass

        # 2. 目录兜底扫描：根据当前任务的 save_dir 扫描未完成的临时文件
        try:
            save_path = Path(self.save_dir)
            if save_path.exists() and save_path.is_dir():
                patterns = ["*.part", "*.ytdl", "*.aria2", "*.f*.mp4", "*.f*.webm", "*.f*.m4a"]
                for pat in patterns:
                    for temp_file in save_path.glob(pat):
                        try:
                            if temp_file.is_file():
                                os.remove(temp_file)
                        except Exception:
                            pass
        except Exception:
            pass

    def _ensure_quicktime_compatibility(self, file_path: str):
        """
        二次安全校验：确保输出的 MP4 视频流编码兼容 macOS QuickTime (avc1 或 hvc1)。
        当用户选择 MKV 或 WebM 原画输出时直接跳过；若为 MP4 且探测到 av01 或 vp09/vp9，
        自动调用 Mac 硬件加速 (VideoToolbox) 转换为 hevc_videotoolbox (hvc1)。
        """
        if not file_path or not os.path.exists(file_path):
            return
        if not file_path.lower().endswith(".mp4"):
            return
        # 若用户显式选择 MKV 或 WebM 封装，绝不执行转码
        out_fmt = getattr(self, "output_format", "MP4 (苹果兼容)")
        if "MKV" in out_fmt or "WebM" in out_fmt:
            return
        if getattr(self, "enable_hdr", False):
            return

        ffprobe_bin = FFPROBE_LOCATION or _resolve_binary("ffprobe") or "ffprobe"
        ffmpeg_bin = FFMPEG_LOCATION or _resolve_binary("ffmpeg") or "ffmpeg"

        try:
            probe_cmd = [
                ffprobe_bin,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,codec_tag_string",
                "-of", "json",
                file_path,
            ]
            res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                return

            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            if not streams:
                return

            v_info = streams[0]
            c_name = str(v_info.get("codec_name") or "").lower()
            tag_str = str(v_info.get("codec_tag_string") or "").lower()

            needs_transcode = False
            if c_name in ("av1", "vp9", "vp09"):
                needs_transcode = True
            elif tag_str in ("av01", "vp09", "vp9"):
                needs_transcode = True
            elif tag_str not in ("avc1", "hvc1") and c_name not in ("h264", "hevc"):
                needs_transcode = True

            if needs_transcode:
                self.on_status("正在使用 Mac 硬件加速转换兼容格式...", "info")
                self.on_progress({
                    "percent": 100.0,
                    "speed_str": "0.00 MB/s",
                    "speed_mb": 0.0,
                    "eta_str": "转码中",
                    "status": "merging",
                })

                target_h = getattr(self, "quality", {}).get("height") if isinstance(self.quality, dict) else None
                target_bitrate_str, _ = get_transcode_target_bitrate(target_h)
                temp_trans = str(Path(file_path).with_name(f"temp_hvc1_{Path(file_path).name}"))

                enc = "hevc_videotoolbox" if sys.platform == "darwin" else "libx265"
                trans_cmd = [ffmpeg_bin, "-y"]
                if sys.platform == "darwin":
                    trans_cmd.extend(["-hwaccel", "videotoolbox"])
                trans_cmd.extend([
                    "-i", file_path,
                    "-c:v", enc,
                    "-tag:v", "hvc1",
                    "-b:v", target_bitrate_str,
                    "-c:a", "copy",
                    "-movflags", "+faststart",
                    temp_trans,
                ])
                trans_res = subprocess.run(trans_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if trans_res.returncode == 0 and os.path.exists(temp_trans) and os.path.getsize(temp_trans) > 0:
                    os.replace(temp_trans, file_path)
                    self.on_status("兼容格式硬件加速转换完成", "success")
        except Exception:
            pass

    def _ensure_thumbnail_embedded(self, file_path: str, info: dict):
        """
        后处理终验保障：确保输出的媒体文件（特别是 MP4/MKV/MP3）已成功内嵌高清封面海报。
        针对 MP4 容器：优先使用 mutagen 写入 Apple 原生 covr atom；若无则通过 ffmpeg disposition:v:1 attached_pic 兜底。
        """
        if not file_path or not os.path.exists(file_path):
            return

        ext = Path(file_path).suffix.lower()
        if ext not in (".mp4", ".m4v", ".mkv", ".mp3"):
            return

        # 1. 查找或下载高清封面文件
        thumb_path = None
        file_p = Path(file_path)
        parent_dir = file_p.parent
        stem = file_p.stem

        # 优先在同目录下寻找 yt-dlp 生成的封面文件 (.jpg / .jpeg / .webp / .png)
        for cand_ext in [".jpg", ".jpeg", ".webp", ".png"]:
            cand = parent_dir / f"{stem}{cand_ext}"
            if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                thumb_path = str(cand)
                break

        # 若未找到，检查同目录下包含 stem 前缀且为图片的文件
        if not thumb_path:
            clean_prefix = "".join(c for c in stem if c.isalnum())[:12]
            if clean_prefix:
                for cand in parent_dir.glob(f"*{clean_prefix}*.*"):
                    if cand.suffix.lower() in [".jpg", ".jpeg", ".webp", ".png"] and cand.is_file() and cand.stat().st_size > 0:
                        thumb_path = str(cand)
                        break

        # 若本地未找到，从 info 中提取 thumbnail URL 下载临时图片
        temp_downloaded_thumb = None
        if not thumb_path:
            thumb_url = info.get("thumbnail")
            if thumb_url:
                try:
                    temp_downloaded_thumb = str(parent_dir / f".temp_thumb_{os.getpid()}_{stem[:10]}.jpg")
                    req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp, open(temp_downloaded_thumb, "wb") as f_out:
                        f_out.write(resp.read())
                    if os.path.exists(temp_downloaded_thumb) and os.path.getsize(temp_downloaded_thumb) > 0:
                        thumb_path = temp_downloaded_thumb
                except Exception:
                    pass

        if not thumb_path or not os.path.exists(thumb_path):
            return

        # 确保图片转换为标准 JPEG 格式（如果当前是 webp 或 png）
        temp_jpg = None
        jpg_path = thumb_path
        if not thumb_path.lower().endswith((".jpg", ".jpeg")):
            try:
                from PIL import Image
                with Image.open(thumb_path) as im:
                    rgb_im = im.convert("RGB")
                    temp_jpg = str(parent_dir / f".temp_converted_{os.getpid()}.jpg")
                    rgb_im.save(temp_jpg, "JPEG", quality=95)
                    jpg_path = temp_jpg
            except Exception:
                pass

        # 2. 针对 MP4 / M4V 执行封面嵌入
        if ext in (".mp4", ".m4v"):
            embedded_success = False
            try:
                from mutagen.mp4 import MP4, MP4Cover
                mp4 = MP4(file_path)
                with open(jpg_path, "rb") as f_img:
                    img_data = f_img.read()
                mp4["covr"] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
                mp4.save()
                embedded_success = True
            except Exception:
                pass

            # 若 mutagen 写入失败，使用 ffmpeg 进行 attached_pic 兜底混流
            if not embedded_success:
                ffmpeg_bin = FFMPEG_LOCATION or _resolve_binary("ffmpeg") or "ffmpeg"
                temp_out = str(parent_dir / f"temp_covr_{file_p.name}")
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-i", file_path,
                    "-i", jpg_path,
                    "-map", "0",
                    "-map", "1",
                    "-c", "copy",
                    "-c:v:1", "copy",
                    "-disposition:v:1", "attached_pic",
                    "-movflags", "+faststart",
                    temp_out,
                ]
                try:
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if res.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
                        os.replace(temp_out, file_path)
                except Exception:
                    if os.path.exists(temp_out):
                        try:
                            os.remove(temp_out)
                        except Exception:
                            pass

        # 3. 针对 MP3 执行封面嵌入
        elif ext == ".mp3":
            try:
                from mutagen.mp3 import MP3
                from mutagen.id3 import ID3, APIC
                audio = MP3(file_path, ID3=ID3)
                try:
                    audio.add_tags()
                except Exception:
                    pass
                with open(jpg_path, "rb") as f_img:
                    audio.tags.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,  # cover front
                            desc="Cover",
                            data=f_img.read(),
                        )
                    )
                audio.save()
            except Exception:
                pass

        # 4. 清理临时生成的转换图片
        for tmp in [temp_downloaded_thumb, temp_jpg]:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

        # 清理 yt-dlp 缓存在同目录下的孤立封面文件 (.jpg, .webp, .png)，保持目录整洁
        for cand_ext in [".jpg", ".jpeg", ".webp", ".png"]:
            sidecar = parent_dir / f"{stem}{cand_ext}"
            if sidecar.exists() and sidecar.is_file() and sidecar != file_p:
                try:
                    os.remove(sidecar)
                except Exception:
                    pass

    def _finish(self, final_path: str | None = None):
        """线程安全的单次完成回调。"""
        if not self._completed and not self._cancelled.is_set():
            self._completed = True
            if final_path:
                self.final_output_path = final_path
            self.on_progress({"percent": 100, "speed_mb": 0, "eta_str": "00:00", "status": "done"})
            self.on_status("下载完成：视频已成功保存", "success")
            try:
                self.on_complete(self.final_output_path)
            except TypeError:
                self.on_complete()

    # ── 内部实现 ──────────────────────────────────────────────

    def _run(self):
        try:
            self._do_download()
        except yt_dlp.utils.DownloadCancelled:
            self._cleanup_temp_files()
            self.on_status("已取消下载，已自动清理临时文件", "warning")
        except yt_dlp.utils.DownloadError as exc:
            if self._cancelled.is_set():
                self._cleanup_temp_files()
                self.on_status("已取消下载，已自动清理临时文件", "warning")
                return
            msg = str(exc)
            if "ffmpeg" in msg.lower() or "ffprobe" in msg.lower():
                friendly = (
                    "未找到 FFmpeg！\n"
                    "请确认项目目录下存在 ffmpeg 可执行文件且具备权限。\n\n"
                    f"详情：{msg}"
                )
            elif "Sign in" in msg or "bot" in msg.lower() or "confirm" in msg.lower():
                friendly = (
                    "YouTube 要求登录验证（反爬风控拦截）。\n\n"
                    "已拦截未登录请求，请点击右上角【导入 Cookie】载入 YouTube 登录状态。\n\n"
                    "建议：\n"
                    "① 点击右上角【导入 Cookie】导入有效 cookies.txt\n"
                    "② 或稍后重试 / 更换公开无限制的视频链接"
                )
            elif "HTTP Error 403" in msg:
                friendly = "访问被拒绝 (HTTP 403)，可能视频有地区或年龄限制。"
            elif "Video unavailable" in msg or "unavailable" in msg.lower():
                friendly = "视频不可用：可能已被删除或设为私密。"
            else:
                friendly = f"下载失败：{msg}"
            self.on_error(friendly)
        except Exception as exc:
            if self._cancelled.is_set():
                self._cleanup_temp_files()
                self.on_status("已取消下载，已自动清理临时文件", "warning")
                return
            self.on_error(f"未知错误：{type(exc).__name__}: {exc}")
        finally:
            if self._cancelled.is_set():
                self._cleanup_temp_files()

    def _build_opts(self) -> dict:
        """根据用户选中的清晰度与字幕偏好构造最佳 yt-dlp 配置参数。"""
        base_opts = get_base_ydl_opts()
        self.has_valid_cookie = bool("cookiefile" in base_opts or "cookiesfrombrowser" in base_opts)

        # 确定实际生效的字幕语言代号与输出模板
        sub_code = getattr(self, "subtitle_lang", None)
        if sub_code is None:
            sub_code = "zh-Hans" if self.enable_subtitles else "none"

        is_sub_enabled = bool(sub_code != "none" and sub_code)

        # 视频与字幕同名独立子目录归档逻辑：当开启字幕时存入同名子目录，未开启时平铺保存
        if is_sub_enabled:
            outtmpl = str(Path(self.save_dir) / "%(title)s" / "%(title)s.%(ext)s")
        else:
            outtmpl = str(Path(self.save_dir) / "%(title)s.%(ext)s")

        ydl_opts = {
            **base_opts,
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "concurrent_fragment_downloads": 6,
            "buffersize": 1024 * 1024,
            "retries": 10,
            "fragment_retries": 10,
            "writethumbnail": True,
        }

        # 智能字幕配置
        if is_sub_enabled:
            if sub_code == "all":
                target_langs = ["all"]
            elif sub_code in ("zh-Hans", "zh-CN"):
                target_langs = ["zh-Hans", "zh-CN", "zh"]
            elif sub_code in ("zh-Hant", "zh-TW", "zh-HK"):
                target_langs = ["zh-Hant", "zh-TW", "zh-HK"]
            else:
                target_langs = [sub_code]

            ydl_opts.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": target_langs,
                "subtitlesformat": "srt",
            })
        else:
            ydl_opts.update({
                "writesubtitles": False,
                "writeautomaticsub": False,
            })

        # 1. 如果传入的是字典（新版动态画质对象）
        if isinstance(self.quality, dict):
            q_type = self.quality.get("type", "video")
            if q_type == "audio":
                ydl_opts.update({
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        },
                        {"key": "FFmpegMetadata"},
                        {
                            "key": "FFmpegThumbnailsConvertor",
                            "format": "jpg",
                        },
                        {
                            "key": "EmbedThumbnail",
                            "already_have_thumbnail": False,
                        },
                    ],
                })
            else:
                format_id = self.quality.get("format_id")
                height = self.quality.get("height")
                label = str(self.quality.get("label") or "")
                is_hdr_quality = bool(self.quality.get("is_hdr", False)) or ("HDR" in label.upper())
                video_supports_hdr = bool(self.quality.get("video_has_hdr", False)) or is_hdr_quality

                bestaudio_rule = "(bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio)"
                out_fmt = getattr(self, "output_format", "MP4 (苹果兼容)")

                # 决定当前任务是否以 HDR 模式抓取与混流：
                if self.enable_hdr is not None:
                    should_hdr = bool(self.enable_hdr and video_supports_hdr)
                else:
                    should_hdr = is_hdr_quality

                prefix_fmt = f"{format_id}+{bestaudio_rule}/" if (format_id and str(format_id) != "642") else ""

                # 【分支 1：MKV (原画极速)】
                if "MKV" in out_fmt or out_fmt.lower() == "mkv":
                    merge_format = "mkv"
                    merger_args = ["-c", "copy"]
                    self._is_hardware_transcoding = False
                    if should_hdr:
                        if height:
                            format_str = (
                                f"bestvideo[height={height}][dynamic_range=HDR]+{bestaudio_rule}/"
                                f"bestvideo[height<={height}][dynamic_range=HDR]+{bestaudio_rule}"
                            )
                            if format_id and is_hdr_quality and str(format_id) != "642":
                                format_str = f"{format_id}+{bestaudio_rule}/" + format_str
                        else:
                            format_str = f"bestvideo[dynamic_range=HDR]+{bestaudio_rule}/bestvideo*+{bestaudio_rule}/best"
                    else:
                        if height:
                            format_str = prefix_fmt + (
                                f"bestvideo[height={height}][vcodec^=vp09]+{bestaudio_rule}/"
                                f"bestvideo[height={height}][vcodec^=vp9]+{bestaudio_rule}/"
                                f"bestvideo[height={height}][vcodec^=av01]+{bestaudio_rule}/"
                                f"bestvideo[height={height}]+{bestaudio_rule}/"
                                f"bestvideo[height<={height}]+{bestaudio_rule}/best"
                            )
                        else:
                            format_str = prefix_fmt + f"bestvideo+{bestaudio_rule}/best"

                # 【分支 2：WebM (原始流)】
                elif "WebM" in out_fmt or out_fmt.lower() == "webm":
                    merge_format = "webm"
                    merger_args = ["-c", "copy"]
                    self._is_hardware_transcoding = False
                    audio_selector_webm = "(bestaudio[ext=webm]/bestaudio[acodec^=opus]/bestaudio)"
                    prefix_fmt_webm = f"{format_id}+{audio_selector_webm}/" if (format_id and str(format_id) != "642") else ""
                    if height:
                        format_str = prefix_fmt_webm + (
                            f"bestvideo[height={height}][vcodec^=vp09]+{audio_selector_webm}/"
                            f"bestvideo[height={height}][vcodec^=vp9]+{audio_selector_webm}/"
                            f"bestvideo[height={height}][vcodec^=av01]+{audio_selector_webm}/"
                            f"bestvideo[height={height}]+{audio_selector_webm}/"
                            f"bestvideo[height<={height}]+{audio_selector_webm}/best"
                        )
                    else:
                        format_str = prefix_fmt_webm + f"bestvideo[vcodec^=vp09]+{audio_selector_webm}/bestvideo+{audio_selector_webm}/best"

                # 【分支 3：MP4 (苹果兼容)】
                else:
                    # 若开启 HDR，为避免 MP4 不兼容，回退为 MKV 原画拷贝
                    if should_hdr:
                        merge_format = "mkv"
                        merger_args = ["-c", "copy"]
                        self._is_hardware_transcoding = False
                        if height:
                            format_str = (
                                f"bestvideo[height={height}][dynamic_range=HDR]+{bestaudio_rule}/"
                                f"bestvideo[height<={height}][dynamic_range=HDR]+{bestaudio_rule}"
                            )
                            if format_id and is_hdr_quality and str(format_id) != "642":
                                format_str = f"{format_id}+{bestaudio_rule}/" + format_str
                        else:
                            format_str = f"bestvideo[dynamic_range=HDR]+{bestaudio_rule}/bestvideo*+{bestaudio_rule}/best"
                    else:
                        merge_format = "mp4"
                        vcodec = str(self.quality.get("vcodec") or "").lower() if isinstance(self.quality, dict) else ""
                        is_avc = vcodec.startswith("avc1") or vcodec.startswith("avc")
                        has_native_aac = bool(self.quality.get("has_native_aac", True)) if isinstance(self.quality, dict) else True

                        # 1080p 及以下原生 avc1: 零转码无损直出
                        if is_avc:
                            if has_native_aac:
                                merger_args = ["-c", "copy"]
                            else:
                                aac_enc = "aac_at" if sys.platform == "darwin" else "aac"
                                merger_args = ["-c:v", "copy", "-c:a", aac_enc, "-threads", "0"]
                            self._is_hardware_transcoding = False
                        # 2K/4K SDR 非 avc1: VideoToolbox 硬件加速转码为 hvc1
                        else:
                            self._is_hardware_transcoding = True
                            target_bitrate_str, _ = get_transcode_target_bitrate(height)
                            audio_cmd = ["-c:a", "copy"] if has_native_aac else ["-c:a", "aac_at" if sys.platform == "darwin" else "aac"]
                            if sys.platform == "darwin":
                                video_cmd = ["-c:v", "hevc_videotoolbox", "-tag:v", "hvc1", "-b:v", target_bitrate_str]
                            else:
                                video_cmd = ["-c:v", "libx265", "-tag:v", "hvc1", "-b:v", target_bitrate_str]
                            merger_args = video_cmd + audio_cmd

                        audio_selector = "(bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio)"
                        target_height = height
                        prefix_fmt_mp4 = f"{format_id}+{audio_selector}/" if (format_id and str(format_id) != "642") else ""

                        if target_height:
                            format_str = prefix_fmt_mp4 + (
                                f"bestvideo[height={target_height}][vcodec^=avc1]+{audio_selector}/"
                                f"bestvideo[height={target_height}][vcodec^=vp09]+{audio_selector}/"
                                f"bestvideo[height={target_height}][vcodec^=vp9]+{audio_selector}/"
                                f"bestvideo[height={target_height}][vcodec^=av01]+{audio_selector}/"
                                f"bestvideo[height={target_height}]+{audio_selector}/"
                                f"bestvideo[height<={target_height}][vcodec^=avc1]+{audio_selector}/"
                                f"bestvideo[height<={target_height}][vcodec^=vp09]+{audio_selector}/"
                                f"bestvideo[height<={target_height}][vcodec^=vp9]+{audio_selector}/"
                                f"bestvideo[height<={target_height}]+{audio_selector}/"
                                f"best"
                            )
                        else:
                            format_str = prefix_fmt_mp4 + (
                                f"bestvideo[vcodec^=avc1]+{audio_selector}/"
                                f"bestvideo[vcodec^=vp09]+{audio_selector}/"
                                f"bestvideo[vcodec^=vp9]+{audio_selector}/"
                                f"bestvideo[vcodec^=av01]+{audio_selector}/"
                                f"bestvideo+{audio_selector}/best"
                            )

                # 高清封面内嵌与字幕后处理
                video_postprocessors = [
                    {
                        "key": "FFmpegThumbnailsConvertor",
                        "format": "jpg",
                    },
                    {
                        "key": "EmbedThumbnail",
                        "already_have_thumbnail": False,
                    },
                ]
                if self.enable_subtitles and merge_format == "mkv":
                    video_postprocessors.append({"key": "FFmpegSubtitlesEmbedder"})

                postproc_args = {
                    "merger": merger_args,
                    "Merger": merger_args,
                }
                if getattr(self, "_is_hardware_transcoding", False) and sys.platform == "darwin":
                    postproc_args["merger+ffmpeg_i1"] = ["-hwaccel", "videotoolbox"]

                ydl_opts.update({
                    "format": format_str,
                    "merge_output_format": merge_format,
                    "postprocessor_args": postproc_args,
                    "postprocessors": video_postprocessors,
                })

        # 2. 如果传入的是字符串（传统静态预设名称，向前兼容）
        elif isinstance(self.quality, str):
            preset = FORMAT_PRESETS.get(self.quality, FORMAT_PRESETS["最高画质 (MP4)"])
            ydl_opts.update(preset)
            if "仅提取音频" not in self.quality:
                video_postprocessors = [
                    {
                        "key": "FFmpegThumbnailsConvertor",
                        "format": "jpg",
                    },
                    {
                        "key": "EmbedThumbnail",
                        "already_have_thumbnail": False,
                    },
                ]
                audio_selector = "(bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio)"
                if self.enable_hdr is False:
                    if "1080" in self.quality:
                        target_h = 1080
                    elif "720" in self.quality:
                        target_h = 720
                    else:
                        target_h = None

                    if target_h:
                        ydl_opts["format"] = (
                            f"bestvideo[height={target_h}][vcodec^=avc1]+{audio_selector}/"
                            f"bestvideo[height<={target_h}][vcodec^=avc1]+{audio_selector}/"
                            f"bestvideo[vcodec^=avc1]+{audio_selector}/"
                            f"bestvideo[height<={target_h}]+{audio_selector}/"
                            f"best[height<={target_h}]/best"
                        )
                    else:
                        ydl_opts["format"] = (
                            f"bestvideo[vcodec^=avc1]+{audio_selector}/"
                            f"bestvideo[vcodec^=avc]+{audio_selector}/"
                            f"bestvideo+{audio_selector}/best"
                        )
                    ydl_opts["merge_output_format"] = "mp4"
                    ydl_opts["postprocessor_args"] = {
                        "merger": ["-c", "copy"],
                        "Merger": ["-c", "copy"],
                    }
                else:
                    is_hdr = "HDR" in self.quality.upper() or (self.enable_hdr is True)
                    merge_format = "mkv" if is_hdr else "mp4"
                    merger_args = ["-c", "copy"]
                    ydl_opts["merge_output_format"] = merge_format
                    ydl_opts["postprocessor_args"] = {
                        "merger": merger_args,
                        "Merger": merger_args,
                    }
                    if self.enable_subtitles and merge_format == "mkv":
                        video_postprocessors.append({"key": "FFmpegSubtitlesEmbedder"})
                ydl_opts["postprocessors"] = video_postprocessors

        return ydl_opts

    def _do_download(self):
        ydl_opts = self._build_opts()
        self.on_status("正在智能探测网络连接与带宽...", "info")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if self._cancelled.is_set():
                self.on_status("下载已取消", "warning")
                return

            info = ydl.extract_info(self.url, download=False)
            title = info.get("title", "未知标题")

            # 寻找直链切片测速
            stream_url = None
            requested = info.get("requested_formats") or []
            if requested:
                stream_url = requested[0].get("url")
            if not stream_url:
                fmts = info.get("formats") or []
                for f in reversed(fmts):
                    if f.get("url") and f.get("protocol") == "https":
                        stream_url = f.get("url")
                        break

            # 动态检测网络带宽并自适应分配最佳并发与缓存
            speed_mb_s, speed_mbps = detect_network_speed(stream_url, timeout=3.0)
            concurrent, buf_size = get_adaptive_download_config(speed_mb_s)

            # 动态更新 ydl 参数
            ydl.params.update({
                "concurrent_fragment_downloads": concurrent,
                "buffersize": buf_size,
                "retries": 10,
                "fragment_retries": 10,
            })

            self.on_status(
                f"已识别网络环境 (~{int(speed_mbps)} Mbps)，自动开启 {concurrent} 线程极速并发分片下载",
                "info",
            )

            if self._cancelled.is_set():
                self.on_status("已取消下载，已自动清理临时文件", "warning")
                self._cleanup_temp_files()
                return

            # 智能自适应合并参数校准与编码安全策略防护：
            merge_fmt = ydl.params.get("merge_output_format", "mp4")
            out_fmt = getattr(self, "output_format", "MP4 (苹果兼容)")

            # 情况 1：用户选择 MKV (原画极速) 或 WebM (原始流)，或者非 MP4 容器
            if "MKV" in out_fmt or "WebM" in out_fmt or merge_fmt != "mp4":
                merger_args = ["-c", "copy"]
                self._is_hardware_transcoding = False
                pp_args = ydl.params.setdefault("postprocessor_args", {})
                pp_args["merger"] = merger_args
                pp_args["Merger"] = merger_args

            # 情况 2：用户选择 MP4 (苹果兼容)
            else:
                requested = info.get("requested_formats") or []
                if not requested and info.get("vcodec"):
                    requested = [info]

                is_avc = False
                is_aac = True
                has_video = False

                for rf in requested:
                    vc = str(rf.get("vcodec") or "").lower()
                    ac = str(rf.get("acodec") or "").lower()
                    ext = str(rf.get("ext") or "").lower()

                    if vc != "none":
                        has_video = True
                        if vc.startswith("avc1") or vc.startswith("avc") or vc.startswith("hvc1"):
                            is_avc = True

                    if ac != "none" and vc == "none":
                        if not (ext == "m4a" or "mp4a" in ac or "aac" in ac):
                            is_aac = False

                # 编码安全策略：
                # 1. 如果抓取到的是原生 avc1 视频：保持零转码无损秒合流
                if is_avc or not has_video:
                    if is_aac:
                        merger_args = ["-c", "copy"]
                    else:
                        aac_enc = "aac_at" if sys.platform == "darwin" else "aac"
                        merger_args = ["-c:v", "copy", "-c:a", aac_enc, "-threads", "0"]
                    self._is_hardware_transcoding = False
                # 2. 防护检测（针对 2K/4K SDR 只能抓到 vp09/av01 时）：
                # 必须调用 macOS 专属的 VideoToolbox 硬件编码器快速转为 QuickTime 完美兼容的 HEVC (hvc1)
                else:
                    self._is_hardware_transcoding = True
                    target_h = getattr(self, "quality", {}).get("height") if isinstance(self.quality, dict) else None
                    if not target_h:
                        for rf in requested:
                            if rf.get("height"):
                                target_h = rf.get("height")
                                break
                    target_bitrate_str, _ = get_transcode_target_bitrate(target_h)
                    audio_cmd = ["-c:a", "copy"] if is_aac else ["-c:a", "aac_at" if sys.platform == "darwin" else "aac"]
                    if sys.platform == "darwin":
                        video_cmd = ["-c:v", "hevc_videotoolbox", "-tag:v", "hvc1", "-b:v", target_bitrate_str]
                    else:
                        video_cmd = ["-c:v", "libx265", "-tag:v", "hvc1", "-b:v", target_bitrate_str]
                    merger_args = video_cmd + audio_cmd
                    self.on_status("检测到超清非 H.264 视频流，已自动启用 Mac 硬件加速转码...", "info")

                pp_args = ydl.params.setdefault("postprocessor_args", {})
                pp_args["merger"] = merger_args
                pp_args["Merger"] = merger_args
                if getattr(self, "_is_hardware_transcoding", False) and sys.platform == "darwin":
                    pp_args["merger+ffmpeg_i1"] = ["-hwaccel", "videotoolbox"]

            # 直接基于已提取的元数据启动下载，避免二次重复请求
            ydl.process_ie_result(info, download=True)

            # 捕获并记录 yt-dlp 最终合成生成的实际文件绝对路径 (final_output_path)
            final_path = getattr(self, "final_output_path", None)
            if not final_path or not os.path.exists(final_path):
                try:
                    prep_fn = ydl.prepare_filename(info)
                    m_fmt = ydl.params.get("merge_output_format")
                    if m_fmt:
                        cand = str(Path(prep_fn).with_suffix(f".{m_fmt}"))
                        if os.path.exists(cand):
                            final_path = os.path.abspath(cand)
                    if not final_path and os.path.exists(prep_fn):
                        final_path = os.path.abspath(prep_fn)
                except Exception:
                    pass

            if not final_path or not os.path.exists(final_path):
                try:
                    title = info.get("title")
                    if title:
                        save_p = Path(self.save_dir)
                        clean_t = "".join(c for c in title if c.isalnum() or c in " _-")[:15]
                        if save_p.exists() and clean_t:
                            matches = [p for p in save_p.rglob(f"*{clean_t}*") if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".mp3", ".webm")]
                            if matches:
                                matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                                final_path = str(matches[0].resolve())
                except Exception:
                    pass

            if final_path and os.path.exists(final_path):
                if "MP4" in getattr(self, "output_format", "MP4") and final_path.lower().endswith(".mp4"):
                    self._ensure_quicktime_compatibility(final_path)

                self._ensure_thumbnail_embedded(final_path, info)

            self.final_output_path = final_path
            self._finish(self.final_output_path)

    def _progress_hook(self, d: dict):
        """深度适配分片并发下载进度的回调逻辑。"""
        if self._cancelled.is_set():
            self._cleanup_temp_files()
            raise yt_dlp.utils.DownloadCancelled()

        # 动态收集正在写入的文件路径以便取消时能够精准清理
        fn = d.get("filename")
        if fn:
            self.active_temp_files.add(fn)
            self.active_temp_files.add(f"{fn}.part")
            self.active_temp_files.add(f"{fn}.aria2")
            self.active_temp_files.add(f"{fn}.ytdl")

        tmpfn = d.get("tmpfilename")
        if tmpfn:
            self.active_temp_files.add(tmpfn)
            self.active_temp_files.add(f"{tmpfn}.aria2")
            self.active_temp_files.add(f"{tmpfn}.part")

        status = d.get("status")

        # 状态判断：只在 d['status'] == 'downloading' 时计算实时进度
        if status == "downloading":
            now = time.time()
            if self._start_download_time == 0.0:
                self._start_download_time = now

            downloaded = int(d.get("downloaded_bytes") or 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate")

            # 1. 百分比计算兼容：
            # 优先按字节：若 total_bytes 或 total_bytes_estimate 存在，则用 (downloaded / total) * 100
            if total and total > 0:
                self._total_bytes = total
                percent = (downloaded / total) * 100.0
            else:
                # DASH 分片兜底：若无 total_bytes，但存在 fragment_index 和 fragment_count
                frag_index = d.get("fragment_index")
                frag_count = d.get("fragment_count")
                if frag_index is not None and frag_count and frag_count > 0:
                    percent = (frag_index / frag_count) * 100.0
                else:
                    percent = getattr(self, "_last_percent", 0.0)

            # 确保平滑单调递增，不抖动回退；未进入合并转码前最多显示 99.9%
            percent = min(max(getattr(self, "_last_percent", 0.0), percent), 99.9)
            self._last_percent = percent

            # 2. 实时下载速度计算：
            # 若 d.get('speed') 存在且 > 0 则直接采用；
            # 若 d.get('speed') 为 None（分片下载常见情况），则使用本地平滑计时器：记录已下载字节数增量与时间差
            speed = d.get("speed")
            if speed is not None and speed > 0:
                speed_mb = speed / 1_048_576.0
                self._speed_ema_mb = speed_mb
                self._last_calc_time = now
                self._last_calc_bytes = downloaded
            else:
                if self._last_calc_time == 0.0:
                    self._last_calc_time = now
                    self._last_calc_bytes = downloaded
                    speed_mb = self._speed_ema_mb
                else:
                    dt = now - self._last_calc_time
                    if dt >= 0.25:  # 每 0.25 秒以上采样一次增量，平滑计算即时速率
                        delta_bytes = max(0, downloaded - self._last_calc_bytes)
                        instant_speed_mb = (delta_bytes / dt) / 1_048_576.0
                        if self._speed_ema_mb == 0.0:
                            self._speed_ema_mb = instant_speed_mb
                        else:
                            self._speed_ema_mb = 0.65 * self._speed_ema_mb + 0.35 * instant_speed_mb
                        self._last_calc_time = now
                        self._last_calc_bytes = downloaded
                    speed_mb = self._speed_ema_mb

            self._last_speed_mb = speed_mb

            # 3. 剩余时间 (ETA)：优先使用 d.get('eta')；若无则通过剩余量与速率动态换算
            eta = d.get("eta")
            if eta is not None and eta >= 0:
                m, s = divmod(int(eta), 60)
                h, m = divmod(m, 60)
                eta_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            else:
                tot = self._total_bytes
                if tot and tot > downloaded and speed_mb > 0.05:
                    rem_mb = (tot - downloaded) / 1_048_576.0
                    rem_sec = int(rem_mb / speed_mb)
                    m, s = divmod(rem_sec, 60)
                    h, m = divmod(m, 60)
                    eta_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                elif percent > 1.0 and (now - self._start_download_time) > 1.0:
                    elapsed = now - self._start_download_time
                    pct_per_sec = percent / elapsed
                    if pct_per_sec > 0:
                        rem_sec = int((100.0 - percent) / pct_per_sec)
                        m, s = divmod(rem_sec, 60)
                        h, m = divmod(m, 60)
                        eta_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                    else:
                        eta_str = "--:--"
                else:
                    eta_str = "--:--"

            speed_str = f"{speed_mb:.2f} MB/s"
            # 传递标准结构字典至 UI 回调
            self.on_progress({
                "percent": percent,
                "speed_str": speed_str,
                "speed_mb": speed_mb,
                "eta_str": eta_str,
                "status": "downloading",
            })
            self.on_status(
                f"下载中  {percent:.1f}%  |  {speed_str}  |  剩余 {eta_str}",
                "info",
            )

        elif status == "finished":
            if getattr(self, "_is_hardware_transcoding", False):
                self.on_progress({
                    "percent": 100.0,
                    "speed_str": "0.00 MB/s",
                    "speed_mb": 0.0,
                    "eta_str": "转码中",
                    "status": "merging",
                })
                self.on_status("正在使用 Mac 硬件加速转换兼容格式...", "info")
            else:
                self.on_progress({
                    "percent": 100.0,
                    "speed_str": "0.00 MB/s",
                    "speed_mb": 0.0,
                    "eta_str": "00:00",
                    "status": "merging",
                })
                self.on_status("正在合并音视频，请稍候…", "info")

        elif status == "error":
            self.on_status("下载出错", "error")

    def _postprocessor_hook(self, d: dict):
        """后处理阶段回调：记录生成的文件路径与更新转码提示。"""
        status = d.get("status")
        pp_name = str(d.get("postprocessor") or "")
        if status == "started":
            if pp_name in ("Merger", "merger") or "Merger" in str(d):
                if getattr(self, "_is_hardware_transcoding", False):
                    self.on_status("正在使用 Mac 硬件加速转换兼容格式...", "info")
                    self.on_progress({
                        "percent": 100.0,
                        "speed_str": "0.00 MB/s",
                        "speed_mb": 0.0,
                        "eta_str": "转码中",
                        "status": "merging",
                    })
                else:
                    self.on_status("正在合并音视频轨，请稍候…", "info")
        elif status == "finished":
            fp = d.get("filepath") or d.get("info_dict", {}).get("filepath") or d.get("info_dict", {}).get("_filename")
            if fp and os.path.exists(fp):
                self.final_output_path = os.path.abspath(fp)


# 兼容性别名
VideoDownloader = DownloadTask

