"""
main.py — 程序入口
初始化并启动 YouTube 下载器桌面应用。
"""
import sys
import os


def get_bundle_dir() -> str:
    """获取应用程序运行时的资源根目录（兼容开发环境与 PyInstaller 打包环境）。"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))


_bundle_dir = get_bundle_dir()
_exec_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _bundle_dir

# 确保运行时资源目录与可执行文件所在目录处于 PATH 最高优先级
_paths_to_add = [
    _bundle_dir,
    _exec_dir,
    os.path.join(_bundle_dir, "_internal"),
]
for p in reversed(_paths_to_add):
    if os.path.isdir(p):
        if p not in sys.path:
            sys.path.insert(0, p)
        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")


def print_feature_manifest():
    """在终端格式化打印当前系统已实现的核心功能清单。"""
    manifest = """
================================================================================
                    YouTube 下载器 (macOS 增强版) — 功能清单盘点
================================================================================

[1] 核心下载与加速引擎
  • 多线程并发分片下载加速（基于 yt-dlp 原生并发切片引擎）
  • 原生 AAC (m4a / itag 140) 音轨精准锁定，免除 CPU 耗时转码
  • 秒级 -c copy 原生音视频无损封包合并（支持 MP4 / MKV）
  • 异常中断自动清理机制（实时清除 .part / .ytdl 碎片文件，防止磁盘残留）

[2] 批量解析与智能容错
  • 支持单视频、播放列表及多链接混排粘贴展开（自动去重与换行规整）
  • 链接输入框支持实时链接计数与奇偶行斑马纹高亮
  • 各视频下载异常独立隔离，单个失败自动跳过并不阻断队列任务
  • 遭遇网络抖动或 YouTube 限流时内置自动重试 3 次机制，并在 UI 展示友好摘要

[3] 全局与单视频独立画质联动
  • 顶部提供全局画质预设（最高画质 / 1080P / 720P / 仅音频 MP3）
  • 每个卡片内置独立画质下拉菜单（按视频真实规格渲染，支持独立覆盖）
  • 单卡片修改自动激活 custom_quality 保护锁，避免全局预设更改时被意外覆盖
  • 独立卡片预估体积胶囊 Badge 联动更新，顶部【预估总计】体积实时重新汇总

[4] 批量 HDR 动态开关
  • 队列控制栏配备【下载 HDR (若可用)】独立动态复选框
  • 勾选时智能捕获 10-bit BT.2020 HDR 视频流并自适应封装为 MKV
  • 未勾选时严格排除 HDR 流，输出 8-bit SDR MP4（完美支持 QuickTime 与 macOS 空格预览）
  • 视频无 HDR 流时智能降级为常规 SDR，杜绝解析或下载报错

[5] 任务队列微交互与动态管理
  • 卡片最右侧集成 ✕ 独立删除按钮（SF Pro Text 字体与 Apple 警告红悬浮视觉）
  • 卡片剔除后序号（1., 2., 3. ...）动态自适应重排，总计体积实时重新计算
  • 删除操作联动控制开始下载按钮与状态徽章，支持队列清空自动重置

[6] macOS 深度原生适配与风控拦截
  • 彻底解决 CustomTkinter 触控板失效难题：废除内部解绑，双重穿透 TouchpadScroll
  • macOS Retina 屏幕物理坐标 Bounding Box 精准判定，双指手势丝滑顺畅
  • 自动化 Cookie 探测机制（覆盖根目录、~/.config/yt-dlp/、~/Downloads/ 及本地浏览器）
  • 界面顶部集成 Cookie 状态徽章与快捷导入弹窗，从容应对 YouTube 反爬拦截

================================================================================
"""
    print(manifest.strip())
    print("\n[INFO] 正在启动 GUI 界面...\n")


def main():
    # 延迟导入，让错误信息更清晰
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print(
            "[ERROR] 未找到 customtkinter。\n"
            "请先运行：pip install customtkinter\n"
            "或激活虚拟环境后重试。"
        )
        sys.exit(1)

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print(
            "[ERROR] 未找到 yt-dlp。\n"
            "请先运行：pip install yt-dlp"
        )
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="YouTube 下载器")
    parser.add_argument(
        "--cookies-from-browser",
        dest="browser",
        default=None,
        help="从本地浏览器读取 YouTube Cookie（如 chrome, safari, edge, firefox）",
    )
    parser.add_argument(
        "--cookies",
        dest="cookies_file",
        default=None,
        help="指定 cookies.txt 文件路径",
    )
    args, _ = parser.parse_known_args()

    from downloader import set_browser_cookie, set_current_cookie_file

    if args.browser:
        set_browser_cookie(args.browser)
    if args.cookies_file:
        set_current_cookie_file(args.cookies_file)

    print_feature_manifest()

    from gui import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
