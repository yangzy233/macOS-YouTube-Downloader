"""
config.py — 用户偏好设置持久化模块
在 ~/.config/youtubedownloader/settings.json 存储并加载设置。
"""
import json
import os
from pathlib import Path

DEFAULT_SETTINGS = {
    "save_path": os.path.expanduser("~/Downloads"),  # 默认下载目录
    "enable_notification": True,  # 默认开启系统通知
    "enable_subtitles": False,    # 兼容老版
    "default_subtitle": "优先简体中文",  # 默认字幕策略: 无字幕 / 优先简体中文 / 优先英文 / 下载全部字幕
    "prefer_hdr": False,          # 默认普通 SDR (MP4)
}

# 应用内部独立托管的专属 Cookie 持久化存储路径
INTERNAL_COOKIE_PATH = os.path.expanduser("~/.config/youtubedownloader/cookies.txt")

def get_internal_cookie_path() -> Path:
    """获取并确保应用专属配置目录存在的内部 Cookie 路径。"""
    p = Path(INTERNAL_COOKIE_PATH)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p

def _get_settings_path() -> Path:
    config_dir = Path.home() / ".config" / "youtubedownloader"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "settings.json"
    except Exception:
        # 降级到当前目录
        return Path(os.path.dirname(os.path.abspath(__file__))) / "settings.json"

def load_settings() -> dict:
    """加载用户配置，若文件不存在或解析失败则返回默认设置。"""
    settings = dict(DEFAULT_SETTINGS)
    path = _get_settings_path()
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    settings.update(data)
        except Exception:
            pass

    # 安全性校验：若配置中的 save_path 依然存在且为有效目录，则使用该路径；若不存在则安全降级回 ~/Downloads
    saved_path = settings.get("save_path")
    if not saved_path or not os.path.isdir(str(saved_path)):
        default_dir = os.path.expanduser("~/Downloads")
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~")
        settings["save_path"] = default_dir

    return settings

def save_settings(settings: dict) -> bool:
    """保存用户配置到 settings.json。"""
    path = _get_settings_path()
    try:
        data = dict(DEFAULT_SETTINGS)
        data.update(settings)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

def update_setting(key: str, value) -> bool:
    """更新单个配置键值并持久化。"""
    current = load_settings()
    current[key] = value
    return save_settings(current)
