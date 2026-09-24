# 发布约定

- 用户说“GitHub 更新”时，默认包含提交并推送代码、递增应用版本号、更新 README 公告，以及发布 GitHub Release。
- Release 需要包含 Windows 安装包和 macOS DMG；确认两端构建成功、附件上传完成后再报告发布完成。
- 发布完成后，将两个安装包同步到 `D:\混剪软件测试\安装包`：Windows `VideoMatrix.Setup.<版本>.exe`，macOS `VideoMatrix.Setup.<版本>-mac.dmg`。
- 仅提交本次已完成的相关改动，不混入无关实验文件；保留用户素材和本地配置。
