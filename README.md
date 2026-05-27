# VideoMatrix 1.5.1

短视频矩阵自动化混剪工具，用于批量生成去重混剪视频。

本仓库提供 Python 源码，并通过 GitHub Actions 自动编译 Windows 单文件版。

## 下载

Windows 用户请到 [Releases](https://github.com/w2968066/VideoMatrix/releases/tag/v1.5.1) 下载：

```text
VideoMatrix1.5.1.exe
```

Release 中的 exe 由 GitHub Actions 自动构建，已内置 `ffmpeg.exe` 和 `ffprobe.exe`，不需要单独配置环境变量。

## 功能特性

- Tkinter 图形界面，免命令行操作。
- Hook 首段素材池按阈值切片并记录使用历史，支持不放回抽取去重。
- Body 后段素材池按片段随机组合，单条成片内部避免重复画面。
- 支持多文件夹矩阵路由：同名文件夹一一对应、同目录自产自销、共享后段素材池。
- 递归扫描素材目录，支持多层子文件夹。
- 使用 FFmpeg / FFprobe 处理视频，优先尝试 NVIDIA `h264_nvenc`，不支持时降级为 `libx264`。
- 针对常见批量导出素材做了视频轨时长探测、帧率对齐、音频重采样和静态水印循环处理。

## 源码运行

需要 Windows 和 Python 3：

```powershell
python AutoVideoMatrix1.5.1.py
```

如果使用源码运行，请确保 `ffmpeg.exe` 和 `ffprobe.exe` 可在以下任一位置找到：

- 与 Python 脚本同级目录
- 系统 `PATH`

## 云端打包

本项目包含 GitHub Actions workflow：

```text
.github/workflows/build-windows.yml
```

可以在 GitHub 的 Actions 页面手动运行 `Build Windows EXE`，它会自动：

- 下载 FFmpeg / FFprobe
- 安装 PyInstaller
- 编译 `VideoMatrix1.5.1.exe`
- 上传 workflow artifact
- 上传到指定 Release

## 本地打包

```powershell
pyinstaller -F -w AutoVideoMatrix1.5.1.py
```

如需把 `ffmpeg.exe` 和 `ffprobe.exe` 一起打入单文件 exe，请在 PyInstaller 参数或 spec 中加入对应 binaries 配置。

## 运行数据

程序可能在运行目录生成以下缓存或历史文件：

- `config.json`
- `usage_history.json`
- `media_cache.json`
- `ffmpeg_error_log.txt`

这些文件属于本地运行状态，不建议提交到版本库。

## 许可

本项目源码采用 MIT License。FFmpeg / FFprobe 相关二进制组件遵循其各自许可证，请在再分发时遵守 FFmpeg 项目的许可要求。
