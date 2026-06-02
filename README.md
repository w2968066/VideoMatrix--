# VideoMatrix

短视频矩阵自动化混剪工具，用于批量生成去重混剪视频。项目保留 1.5.1 版成熟混剪核心，并新增 Electron 桌面界面、GitHub Actions 云端构建和更完整的任务反馈。

## 下载

Windows 用户请到 Releases 下载最新安装包：

[https://github.com/w2968066/VideoMatrix--/releases](https://github.com/w2968066/VideoMatrix--/releases)

当前新版安装包：

```text
VideoMatrix.Setup.2.0.0.exe
```

历史 1.5.1 单文件版：

```text
VideoMatrix1.5.1.exe
```

## 新版特性

- Electron 桌面界面，固定窗口尺寸，隐藏默认菜单栏，界面信息集中在单屏内显示。
- 保留醒目联系方式：`VX：18667026883`。
- 支持 Light / Dark 主题切换。
- 路径输入框可手动粘贴长路径，也可通过按钮选择文件夹或文件。
- 路径图标可直接打开已设置目录，方便检查素材位置。
- 预检产能、智能压测、启动渲染、停止渲染、清除记录等核心按钮完整保留。
- 智能压测提供进行中状态和测试结果提示。
- 自动渲染完成后显示更明显的完成提醒。
- 产出列表显示每条视频的耗时，并提供打开入口。
- 历史设置持久化，重新安装后尽量沿用之前的配置。
- GitHub Actions 自动构建 Windows 安装包，并上传到 Release。

## 1.5.1 核心能力

- Hook 首段素材池按阈值步长顺序切片，使用历史写入 `usage_history.json`。
- Hook 生成时会随机洗牌并不放回抽取，降低重复；去重阈值为 `1.0` 时退化为无限复用。
- Body 后段素材池按步长切片，单条成片内部随机抽样且不重复。
- Body 在跨视频调度中采用放回复用，后段素材池不会枯竭。
- 素材裁切遵循单文件独立切片原则，尾部不足设定时长的片段会丢弃。
- 支持 Hook / Body 智能矩阵路由：
  - 同一父路径：子文件夹内部自产自销。
  - 不同父路径且存在同名子文件夹：Hook 与 Body 一一对应。
  - Body 为普通文件夹：多个 Hook 子文件夹共享同一个 Body 素材池。
- 使用递归扫描素材目录，支持多层子文件夹。
- 使用 FFmpeg / FFprobe 处理视频。
- 优先尝试 NVIDIA `h264_nvenc` 硬件编码，不支持时自动降级到 `libx264`。
- 支持非整数帧率，例如 `29.94`、`29.97`、`30000/1001`。
- 使用视频轨时长而不是容器总时长，规避音频尾巴导致的黑屏片段。
- 拼接前统一帧率、音频采样率和音频格式，降低多源素材拼接异常。
- 静态图片水印自动循环，并在主视频结束时自动截断。

## 使用说明

1. 准备 Hook 首段、Body 后段、BGM、配音、字幕、水印等素材目录或文件。
2. 在界面中选择或粘贴对应路径。
3. 根据需要设置首段、后段、片段时长、数量、并发、重叠率、音量、分辨率、码率和帧率。
4. 可先点击预检产能或智能压测。
5. 点击启动渲染开始批量生成。

输出目录不是强制必填；未填写时程序会沿用原版默认输出逻辑。

## 本地运行

新版桌面端分为前端和后端：

```powershell
cd backend
python app.py

cd ../frontend
npm install
npm run dev
```

1.5.1 Python 版可直接运行：

```powershell
python AutoVideoMatrix1.5.1.py
```

源码运行时请确保 `ffmpeg.exe` 和 `ffprobe.exe` 可在程序目录或系统 `PATH` 中找到。

## 云端构建

仓库包含 GitHub Actions workflow：

```text
.github/workflows/build-windows.yml
```

可在 GitHub Actions 页面手动运行 Windows 构建。流程会自动安装依赖、构建前后端、打包 Windows 安装包并上传到指定 Release。

## 本地打包

新版 Electron 安装包：

```powershell
cd frontend
npm run dist
```

历史 1.5.1 单文件版：

```powershell
pyinstaller -F -w AutoVideoMatrix1.5.1.py
```

分发时请保留 `ffmpeg.exe` 和 `ffprobe.exe`，不需要 `ffplay.exe`。

## 运行数据

程序可能在本地生成以下配置、缓存或历史文件：

```text
config.json
usage_history.json
media_cache.json
ffmpeg_error_log.txt
```

这些属于本地运行状态，不建议提交到仓库。

## 许可

本项目源代码采用 MIT License。FFmpeg / FFprobe 相关二进制组件遵循其各自许可协议，二次分发时请遵守 FFmpeg 项目的许可要求。
