# Hiyori 本地桌宠原型

macOS / Apple Silicon。以固定 Open-LLM-VTuber 后端和 Electron 前端源码为基线，新增仅供本地个人验证的入口。中文键盘输入与字幕、日语语音、Hiyori Live2D、可打断播放、选窗 OCR、剧情摘要和可编辑记忆。不启用麦克风、远程 API 或操作游戏的工具。

## 启动

本机依赖和资源准备完成后，在项目目录执行：

```bash
.venv/bin/python scripts/run_local.py --model 9b --voice sayo
```

可选 `--model 27b`（Qwen3.5-27B 4bit）、`--model qwen3`（Qwen3-8B纯文字对照）、`--voice mao`（AivisSpeech:まお）。每次只启动一个语言模型、一个语音引擎。更换前先在启动终端按 Ctrl-C 关闭服务。端口占用时脚本报错，不杀其他程序。

```bash
# 更强模型与替代音色
.venv/bin/python scripts/run_local.py --model 27b --voice mao
# 只运行服务，手动打开 http://127.0.0.1:12393
.venv/bin/python scripts/run_local.py --model 27b --voice mao --web
```

默认启动 Electron。点击右上角「桌宠」进入可拖动、透明置顶的小窗口；点「展开」恢复完整面板。只允许一个界面连接，避免不同窗口同时控制观察和播放。

## 首次安装 / 复现

需要 Xcode Command Line Tools（Swift）、uv、Node.js 22+、macOS 14+、Apple Silicon。实际验证环境为 M5 Pro / 48 GiB，模型权重占用不等于运行峰值内存。

```bash
./scripts/setup.sh --model 9b --voice sayo
# 下载附加候选，重复运行不会重建已有 Python 虚拟环境
./scripts/setup.sh --model 27b --voice mao
```

来源、模型 revision 和资源URL固定在 `config/assets.json`；两个上游源码 SHA 在 `UPSTREAM.json`。下载和构建均在本地，不上传对话。AivisSpeech首启会从官方自动下载まお、コハク和日语BERT，可能需要数分钟；引擎使用标准目录 `~/Library/Application Support/AivisSpeech-Engine`，启动脚本关闭 Sentry。VOICEVOX也使用其标准用户数据目录。主项目资源与数据库在 `.runtime/`。

AivisSpeech启动后可用 `.venv/bin/python scripts/install_mao.py` 检查音色；仅缺失时调用官方安装接口。名字和风格由 `/speakers` 动态解析，不硬编码声音ID。

## 验证顺序

1. 先聊天：闲聊、接梗、反驳、认真交流、连续多轮。评价是否愿意持续聊，不用测试通过来代替人格判断。
2. 再听声音：比较同句试听；发送新消息或点「停止当前语音」检查旧声音不会恢复。静音后仍显示中文。
3. 再玩游戏：虚拟机窗口化 →「观察」→刷新窗口→选择→预览→分别框选游戏画面和对白/说话人区域→填写游戏/存档名称→开始。首次需要自行授予 macOS 屏幕录制权限。没有选窗不会捕获桌面。
4. 最后连续约30分钟并重启：检查记忆、剧情接续、三档频率、快速翻页、窗口缩放和资源占用。未完成真实游戏测试前不视为游戏陪伴验收。

OCR每秒采样，文字至少稳定0.8秒后记录，低置信度内容过滤；快速翻页可能漏读。这里只能确认捕获到的对白文本，不能保证每次分离出说话人。点击「看一下当前画面」才额外调用视觉模型。Qwen3-8B对照不支持图像。

安静/自然/活跃的主动评论最小间隔分别180/45/15秒；用户输入优先，过期12秒的待发评论丢弃。停止观察、禁止主动发言、静音、停止当前语音相互独立。观察状态不跨重启自动开启；场景名一致则读取原有剧情。

## 人格与记忆

- `config/persona.md`：独立核心人格。自动记忆不改写这个文件。
- 短期情绪：独立运行时状态，约两分钟回落到 neutral。
- `.runtime/companion.sqlite3`：原始对话/观察、双语回答、带来源记忆、各场景摘要。画面不持久保存；预览只在界面内存。
- 空闲15秒后、积累至少6条新来源时尝试整理摘要与记忆。模型提取仍可能不准确，应在「记忆」面板纠正。
- 删除/纠正会清除关联来源、依赖摘要和助手复述，代价是相关历史上下文可能减少；不会把旧摘要悄悄放回下一轮。

## 测试与记录

```bash
.venv/bin/pytest -q
NATIVE_TEST=1 .venv/bin/pytest tests/test_native.py -q
node --experimental-strip-types --test desktop/tests/turn-player.test.ts
npm --prefix desktop run build
```

原生测试只识别生成的中文图片，不截图桌面。受限执行沙盒可能拒绝Apple Vision资源访问；普通终端实测可用。

运行同一组多轮样本（会使用当前8080端口模型，不要并发日常聊天测延迟）：

```bash
.venv/bin/python scripts/benchmark.py --model .runtime/models/Qwen3.5-9B-4bit --label qwen9-sayo
```

结果及首句WAV在 `.runtime/evaluations/<label>/`。服务端指标是音频准备就绪时间；界面显示基于音频 `playing` 事件的首句时间。首请求单列，少量样本P95不能代表长期性能。真实虚拟机/游戏负载需另测。

上游全量 `npm run typecheck` 存在大量 Cubism SDK 原有严格类型错误；不能声称全仓typecheck已通过。当前采用构建、增量类型错误对比和针对新增模块的测试。上游锁文件的 npm audit 风险尚未系统升级修复，原型只监听回环地址。

## 来源与范围

见 `docs/persona-sources.md`、`docs/aivis-integration-research.md`、`docs/progress.md`。Hiyori形象、Cubism代码、语言模型和音色各自保留来源与许可；音色许可不能替代形象许可。资源不进入Git。

本地入口为 `backend/run_companion.py`，不初始化上游全量ASR/MCP服务。前端保留上游原App以便对照，当前入口指向 `companion/Companion.tsx`；复用Cubism模型、缩放/拖动、WAV口型解码和音频管理。V1桌宠透明区域尚不点击穿透。
