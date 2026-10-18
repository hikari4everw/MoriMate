# Codex Desktop 使用 MoriMate 本地模型
# 此文件以及相關的不用上傳至github
本机模型为 `Qwen3.5-27B-4bit`，通过 MLX 的 `http://127.0.0.1:18080/v1/responses` 接入。沿用现有权重，不需要重新下载模型或填写 API Key。

## 切换方法

在本项目文件夹中：

1. 双击 **Codex切换本地.command**。它会复用正在运行的 MoriMate 模型服务；未运行时自行启动模型，首次加载需要等待。看到“已配置为本地”后继续。
2. 等当前 App 任务结束，按 **⌘Q** 完全退出 App，再重新打开。
3. 在 **Codex 本地聊天**中新建聊天，模型应显示 **MoriMate Qwen3.5-27B（本地）**。可让它读取项目中的一个文本文件确认工具可用。
4. 要使用云端，双击 **Codex切换云端.command**，然后再次 **⌘Q → 重开 → 新建聊天**。恢复的是切到本地前保存的云端模型与推理强度，本次初始值为 `gpt-6.1-sol / medium`。

本机安装的 App 在系统中显示为 ChatGPT，应用标识为 `com.openai.codex`；这里接入的是它的 Codex 本地执行功能。ChatGPT 普通聊天和 Work Cloud 不会因此接入本地模型。

**本地与云端提供商通过双击入口切换。** 不要只在模型菜单中选择一个 GPT 名称来切回云端，因为提供商地址也必须恢复。切换后使用新聊天，旧聊天可能保留创建时的提供商；本次未验证旧聊天跨提供商继续对话。

无需注销账号。已有登录、插件和其他 App 设置保留。已经处于云端时重复点击云端入口，不会覆盖你后来在 App 中调整的云端设置。

## 模型服务与桌宠

- 独立模型由 macOS launchd 管理，关闭切换窗口、退出 Codex App 或切回云端后继续运行，占用内存。不会在登录 Mac 时自动加载；重启 Mac 后，下次双击本地入口再启动。
- 要同时使用 MoriMate，优先先按原来的方式启动桌宠，再运行“切换本地”，这样复用同一个模型服务。如果由桌宠启动，退出桌宠的启动程序也会关闭模型服务。
- 独立模型先启动时，原桌宠启动脚本会提示 18080 被占用；这是原有保护行为。先结束独立 MLX 服务，再启动桌宠。独立入口的服务配置在 `~/Library/LaunchAgents/com.morimate.codex-mlx.plist`，日志在 `~/Library/Logs/MoriMate/codex-mlx.log`。要停止独立模型，在终端执行 `launchctl bootout gui/$(id -u)/com.morimate.codex-mlx`；下次双击本地入口会重新注册并启动。
- 本地模型能力和速度与云端不同；默认上下文限制为 32768 tokens，接近 24576 tokens 时触发压缩。模型目录提供简短编码助手基础指令，项目的 AGENTS.md 仍单独加载；不会使用桌宠的人格和记忆。
- 模型生成在本机进行；App 自身的登录、插件、搜索和其他联网功能仍可能访问网络。本配置不等于整个 App 离线。

## 配置与恢复

用户级文件在 `~/.codex/`：

| 文件 | 用途 |
| --- | --- |
| `config.toml` | 当前生效配置 |
| `morimate-codex-backup.toml` | 首次切换前的完整备份，后续不覆盖 |
| `morimate-cloud.json` | 最近一次从云端切到本地前的模型设置 |
| `morimate-model-catalog.json` | 本地模型名称和能力目录 |

平时恢复云端使用双击入口即可。若配置文件被手动改坏，可先完全退出 App，再将 `morimate-codex-backup.toml` 复制回 `config.toml`；完整恢复会撤销备份之后的其他配置改动，恢复前先另存当前文件。

终端等效命令（在项目根目录运行）：

```bash
.venv/bin/python scripts/codex_model.py local
.venv/bin/python scripts/codex_model.py cloud
```

如果提示端口占用或模型启动失败，脚本不改当前模型配置。查看 `~/Library/Logs/MoriMate/codex-mlx.log`（由桌宠启动时查看项目内 `.runtime/mlx.log`）；不要为了释放 18080 而终止不明服务。

## 本次验证与限制

- 修复 App 退出后原有子进程被关闭的问题：改由 launchd 持有独立模型进程，启动程序退出后服务仍由系统管理。
- 已验证 MLX 模型列表、Responses SSE、函数调用和工具结果回传。
- 已使用 App 内置 Codex 引擎，通过本地模型调用 shell 读取测试文件并正确回复内容。
- 已验证云端返回 `CLOUD_OK`，并验证真实配置本地→云端→本地往返保留原有设置。
- App 禁止自动化工具操作自身，因此没有关闭当前 App，也没有在重启后的 GUI 中发送消息；这一步需要按上面的操作完成。
- 本机 Codex 版本为 `0.163.0-alpha.10.2`。模型绝对路径会触发遥测标签字符警告，已验证不影响本地回答和 shell 工具。云端验收遇到 WebSocket 403 后自动回退 HTTPS 并成功回答。

官方配置参考：

- [自定义提供商与配置位置](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Responses 协议与模型目录设置](https://learn.chatgpt.com/docs/config-file/config-reference)
