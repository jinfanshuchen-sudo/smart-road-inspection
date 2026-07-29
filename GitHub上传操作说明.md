# GitHub 上传与后续更新说明

仓库地址：<https://github.com/jinfanshuchen-sudo/smart-road-inspection>

本文件对应的是“智慧道路巡检与路灯环境感知系统”的源码仓库。仓库当前设置为 **Private（私有）**，只有你本人和你邀请的协作者可以查看。

## 当前已完成的准备

- 本工程已初始化为 Git 仓库，当前分支是 `main`。
- `origin` 已连接到上面的 GitHub 仓库地址。
- 完整的应用源码、Python 包、树莓派脚本、仪表盘、运行脚本和文档都已纳入 Git 管理。
- `.gitignore` 已排除虚拟环境、缓存、日志、运行产生的图片、密钥文件、历史备份、Word 生成文件和交付压缩包。

## 第一次上传：只需要推送

在本目录空白处按住 **Shift + 鼠标右键**，选择“在终端中打开”或“在 PowerShell 中打开”，依次执行：

```powershell
git push -u origin main
```

如果 GitHub 要求登录：

1. 浏览器会自动打开时，使用创建 `smart-road-inspection` 仓库的 GitHub 账号登录并授权。
2. 若终端要求输入用户名和密码：用户名填写 GitHub 用户名；密码位置不要填写 GitHub 登录密码，而是填写 GitHub Personal Access Token（个人访问令牌）。
3. 命令显示 `branch 'main' set up to track 'origin/main'` 后，第一次上传完成。

完成后刷新 GitHub 仓库页面，应能看到 `README.md`、`drone_mission_service.py`、`dashboard`、`pyhulax`、`raspberry_pi`、`docs` 等文件。

## 压缩包要不要上传？

**不需要，也不建议把压缩包推入 GitHub 的源码仓库。** GitHub 用 Git 保存每一次源码改动，本身就是工程的版本库；上传 ZIP 会造成重复、仓库膨胀，且单个文件超过 100 MB 会被 GitHub 拒绝。

本机已有的客户交付 ZIP（约 97 MB）和 `客户交付包_智慧道路巡检系统` 离线依赖目录将继续保留在电脑中，但会被 `.gitignore` 排除，不会上传到源码仓库。这是正确且安全的做法。

如果以后需要把 ZIP 发给客户，请通过网盘、U 盘或 GitHub 的 **Releases** 页面作为附件发布；不要用普通 `git add` 上传 ZIP。发布 Release 前先确认 ZIP 中没有账号、密码、Wi-Fi 密钥、Token 或其他个人信息。

## 以后每次更新工程的标准流程

在此工程目录打开 PowerShell，按顺序执行：

```powershell
git status
git add .
git commit -m "说明本次更新内容"
git push
```

示例：

```powershell
git add .
git commit -m "更新无人机巡检流程和仪表盘"
git push
```

`git status` 用来先检查哪些文件会被上传。看到 `.env`、日志、`客户交付包`、`.venv` 或 ZIP 没有出现在待提交列表中是正常的，它们被忽略规则保护。

## 上传前安全检查

不要提交以下内容：

- GitHub Token、API Key、数据库密码、Wi-Fi 密码、私钥。
- `.env` 和任何仅用于现场设备的机密配置文件。
- `.venv`、缓存、日志、录制视频、运行截图等可重新生成的文件。
- 客户姓名、联系方式、未脱敏的现场数据。

如发现已经把密钥上传到了 GitHub，应立刻在对应平台撤销并重新生成该密钥；仅删除仓库文件并不能让旧版本历史中的密钥失效。

## 常见问题

**`Authentication failed` / `Repository not found`**：确认登录的是 `jinfanshuchen-sudo` 账号，且仓库地址没有拼错；重新在浏览器登录 GitHub 后再执行推送。

**`rejected`**：先执行 `git pull --rebase origin main`，成功后再执行 `git push`。

**文件超过 100 MB**：不要强行上传，改为 Release 附件、Git LFS 或网盘；本工程的 ZIP 已经被自动排除。

**不想公开代码**：保持仓库为 Private 即可；邀请他人时进入 GitHub 仓库的 **Settings → Collaborators** 添加其 GitHub 用户名。
