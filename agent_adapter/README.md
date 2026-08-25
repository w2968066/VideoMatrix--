# VideoMatrix Agent Adapter

这是独立适配层，只调用 VideoMatrix 已有 HTTP API，不修改混剪核心。

## CLI

在仓库根目录运行：

```powershell
python -m agent_adapter profiles
python -m agent_adapter prepare --hook "D:\Hook" --body "D:\Body"
python -m agent_adapter prepare --hook "D:\Hook" --body "D:\Body" --defaults --total-seconds 12 --hook-seconds 3
python -m agent_adapter preflight --profile finished-hook --hook "D:\Hook" --body "D:\Body" --bgm "D:\BGM" --count 20
python -m agent_adapter run --profile finished-hook --hook "D:\Hook" --body "D:\Body" --bgm "D:\BGM" --count 20
```

`prepare` 只打开 GUI 并合并明确提供的路径或参数，不会开始渲染。省略 `--defaults` 时保留用户上次设置；启用后使用竖屏 2K、8000k、24 帧和重叠率 `0/0.2/0.1`。所有命令只输出一行精简 JSON。

## MCP

一键接入当前电脑的 Codex：

```powershell
PowerShell -ExecutionPolicy Bypass -File agent_adapter/install_codex.ps1
```

脚本会创建适配层自己的虚拟环境、安装 MCP 依赖并注册工具，不会修改 VideoMatrix 后端环境。手工注册时使用：

```powershell
codex mcp add videomatrix --env "PYTHONPATH=C:\VideoMatrix" -- C:\VideoMatrix\agent_adapter\.venv\Scripts\python.exe -m agent_adapter.mcp_server
```

提供六个工具：

- `videomatrix_profiles`
- `videomatrix_prepare`
- `videomatrix_preflight`
- `videomatrix_render`
- `videomatrix_task`
- `videomatrix_stop`

可选环境变量：

- `VIDEOMATRIX_API_URL`：默认 `http://127.0.0.1:8765/api`
- `VIDEOMATRIX_BACKEND_EXE`：指定后端 exe 绝对路径
- `VIDEOMATRIX_APP_EXE`：指定桌面程序绝对路径
