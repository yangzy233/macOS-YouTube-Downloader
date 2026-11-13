<div align="center">

<img src="icon.png" alt="YouTube 下载器 Logo" width="128" height="128" />

# YouTube 下载器 (macOS 极简暗黑版)

*专为 macOS 深度定制的高颜值、高性能 YouTube 视频与音频下载桌面客户端*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)

</div>

---

## 📥 下载安装（推荐普通用户）

如果您**不需要**修改源码，可直接下载开箱即用的安装镜像：

1. 前往 **[Releases 发布页面](https://github.com/yangzy233/macOS-YouTube-Downloader/releases/latest)** 下载匹配您 Mac 芯片架构的最新安装包：
   - 🍏 **Apple Silicon 芯片**（M1 / M2 / M3 / M4 系列）：下载 **`YouTube下载器-macOS-arm64.dmg`**（原生运行，性能最佳）；
   - 💻 **Intel 处理器芯片**（老款 Mac）：下载 **`YouTube下载器-macOS-intel.dmg`**（原生 x86_64 指令集编译）。
2. 双击打开下载的 `.dmg` 磁盘映像，将左侧的 **YouTube 下载器** 图标拖拽入右侧的 **Applications**（应用程序）替身中即可完成安装。
3. **首次打开安全提示**：由于软件采用本地代码签名，若首次启动出现 macOS 安全提示「无法打开，因为无法验证开发者」：
   - 请在访达的「应用程序」文件夹中找到 **YouTube 下载器**；
   - **按住 Control 键点击（或右键点击）** 图标，在弹出菜单中选择 **「打开」**；
   - 在系统警告弹窗中再次点击 **「打开」**，之后即可永久双击直接运行。

---

## 🌟 核心亮点与深度定制

- 🍎 **原生 macOS 极简暗黑美学**
  - 基于 CustomTkinter 打造，遵循 Apple Human Interface Guidelines (HIG) 设计规范。
  - 精准支持深色/浅色自适应外观，采用原生质感卡片、半透明层级与高对比度操作指示。
- ⚡ **HDR / SDR 智能双轨混流与转码封装**
  - **SDR 模式**：严格锁定 H.264 (`avc1`) 视频流 + 原生 AAC (`mp4a`) 音频流，直接封装为标准 MP4，**彻底杜绝 VP9 编码，完美兼容 macOS 原生 QuickTime 与访达空格键预览**。
  - **HDR 模式**：智能嗅探 HDR10 / 杜比视界高动态范围视频流，自动封装为 MKV 容器，呈现沉浸式画质细节。
  - **纯音频模式**：支持一键提取 192kbps / 320kbps 高品质 MP3 或原生 M4A，自动内嵌歌曲海报与元数据。
- 🎯 **全局画质与单视频卡片独立联动**
  - 支持多视频批量解析，自动嗅探各清晰度选项与预估体积胶囊 Badge。
  - 顶部一键统一切换全局画质（4K / 2K / 1080P / 720P / 音频），单卡片可自由独立覆盖设定。
- 📜 **触控板双指惯性顺滑滚动**
  - 专门重构 macOS 底层手势响应事件与像素级增量解码，彻底杜绝 Tkinter 原生滚轮在触控板上的卡顿撕裂与边界跳闪。
- 🖼️ **官方高清缩略图封面自动内嵌**
  - 采用 FFmpeg 自动抓取 YouTube 官方高分辨率封面并内嵌至视频文件，文件在访达与播放器中直接展现精美海报图标。
- 🌐 **多语言中文字幕智能下载与独立目录归档**
  - 智能嗅探官方字幕与自动生成字幕（支持优先简体中文、繁体中文、英文或全部下载）。
  - 开启字幕时**自动创建同名独立文件夹**归档，将视频本体、封面海报与字幕文件（`.srt` / `.vtt`）统一存放，保持下载主目录整洁。
- 🔔 **macOS 系统级原生通知与一键访达定位**
  - 任务批量完成后触发 macOS 原生横幅通知与系统提示音。
  - 卡片右侧配备【在访达中定位】按钮，精确穿透至归档子文件夹并高亮选中视频文件。
- 🍪 **Cookie 可视化管理中心与反爬风控引导**
  - 顶部动态状态指示胶囊按钮（未配置灰橙色 / 已生效绿色）。
  - 内置暗黑风分步图文教学弹窗，提供 Chrome / Edge / Firefox / GitHub 官方扩展直达入口。
  - 智能捕获 429 请求过多、Sign-in 登录验证等风控异常，一键唤起凭据指引与导入。
- 💾 **下载目录永久持久化记忆**
  - 更改保存路径即时无感保存至 `~/.config/youtubedownloader/settings.json`，重启软件永不丢失，并具备目录失效安全降级机制。

---

## 🛠 环境要求

| 依赖项 | 推荐版本 | 说明 |
| :--- | :--- | :--- |
| **macOS** | >= 12.0 (Monterey) | 支持 Apple Silicon (M1/M2/M3/M4) 与 Intel 架构 |
| **Python** | >= 3.10 | 推荐使用 Python 3.12 |
| **FFmpeg** | 最新稳定版 | 混流、封面内嵌与字幕转码核心工具 |
| **Node.js** | 任意版本（可选） | 辅助 yt-dlp 解析新版 YouTube JavaScript challenge |

### 安装 FFmpeg（macOS）
推荐通过 Homebrew 快速安装：
```bash
brew install ffmpeg
```
> *注：本项目亦内置了 `static-ffmpeg` 自动探测机制，即便系统未安装 Homebrew，也能自动尝试完成媒体流无损封装。*

---

## 🚀 快速启动（源码运行）

### 方法一：使用 `setup.sh` 一键配置（推荐）

项目配备了自动化安装脚本，优先使用极速包管理器 `uv`（无需编译环境）：

```bash
# 1. 克隆仓库
git clone https://github.com/yangzy233/macOS-YouTube-Downloader.git
cd macOS-YouTube-Downloader

# 2. 一键配置虚拟环境并安装依赖
bash setup.sh

# 3. 启动程序
.venv/bin/python main.py
```

### 方法二：标准 Python 虚拟环境手动运行

```bash
# 1. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖包
pip install -r requirements.txt

# 3. 运行主程序
python main.py
```

---

## 📦 打包为独立应用与 .dmg 安装包

项目提供了完备的一键打包流水线（基于 PyInstaller + dmgbuild）：

```bash
bash build.sh
```

构建脚本将自动执行以下全套流水线：
1. 编译 1024×1024 高清图标资源并生成标准 `app.icns`；
2. 打包集成可执行文件与资源，配置 `Info.plist` 简体中文多语言支持；
3. 执行本地 Ad-hoc 代码签名，规避 macOS 启动拦截；
4. 生成独立应用程序：`dist/YouTube下载器.app`；
5. 自动封装为极简白底拖拽安装型磁盘映像：`dist/YouTube下载器-macOS-arm64.dmg`。

---

## 📁 项目结构

```text
macOS-YouTube-Downloader/
├── main.py             # 应用程序引导主入口与环境初始化
├── gui.py              # CustomTkinter 界面渲染、触控板手势响应与交互逻辑
├── downloader.py       # 下载核心引擎（流策略匹配、音视频混流、字幕与封面内嵌）
├── config.py           # 用户配置管理与持久化模块 (~/.config/youtubedownloader/)
├── icon.png            # 1024x1024 高分辨率主图标源文件
├── app.icns            # macOS 标准图标资源包
├── dmg_settings.py     # dmgbuild 磁盘映像布局与视觉配置文件
├── requirements.txt    # 核心 Python 依赖清单
├── setup.sh            # 自动化环境配置脚本
├── build.sh            # macOS 独立应用及 .dmg 打包流水线
├── LICENSE             # MIT 开源授权协议
└── README.md           # 项目详细说明文档
```

---

## ⚠️ 免责声明 (Disclaimer)

1. 本项目仅供个人学习、计算机技术研究以及合法自有媒体备份使用。
2. 请遵守 YouTube 服务条款及所在地区的法律法规。
3. 请勿将本项目用于任何侵犯版权或商业牟利的行为，开发者不对任何使用者的不当使用承担连带责任。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 许可协议开源。
