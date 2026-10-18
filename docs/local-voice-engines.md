# 本地语音引擎

## 使用

正常运行 `.venv/bin/python scripts/run_local.py --web` 或双击“启动桌宠.command”。默认 Roxy，切换选择只保留本次运行。`--voice mao`、`--voice sayo` 可显式使用原音色；直接运行后端且未设置 `COMPANION_MANAGED_VOICES=1` 时仍连接旧配置的外部语音服务，不管理或切换它。

本机 GPT-SoVITS 源码版本 `1b47ca07ef43a284e0b8bd7f29d3e1e3ff76082a`，位于相邻 `TTS工具/GPT-SoVITS`。Roxy3.0 的实际 SoVITS 类型为 v2ProPlus，不能根据目录名 v2Pro 判断。启动显式传入权重对、CPU、`-fp`、loopback 地址和 WAV 非流式模式。无需升级或改写该安装。

后端管理自己创建的语音进程组，先释放旧服务再启动新服务，不能接管已占用的端口。GPT-SoVITS 的 `/openapi.json` 只用于等待监听；还须确认监听 PID，并成功合成完整 WAV 才就绪。加载超时默认180秒，单句 HTTP 超时120秒；加载失败尝试まお。两者不可用仍能文字聊天。详情见 `.runtime/managed-voice.log`。

## 登记角色

编辑 `config/voices.json`，所有资源路径相对项目根目录解析，也接受绝对路径。`engines` 登记安装位置、Python及唯一端口；`voices` 的键是稳定的 `voice_key`，界面按引擎分组。模型文件、参考原音及生成音频不提交到 Git。

新增 GPT-SoVITS 角色时复制 Roxy 配置，修改稳定键、label、两份权重及 references。`neutral` 必须存在；其他语气缺失时使用 neutral。每个参考条目包含 audio 和准确对应的日语 text，参考语言及播报语言均为 ja。更改默认启动角色用 default_voice；fallback_voice 必须是已登记的 AivisSpeech 角色。模型资源不会自动下载。

目前有一个真实 GPT-SoVITS 角色。多角色流程由测试配置验证，不代表第二个真实模型已验收。

## 自动语气

模型输出保留 emotion 控制原有六类表情，额外输出 voice_tone；缺少或非法 voice_tone 归 neutral，不拒绝旧回复。

| voice_tone | GPT-SoVITS 参考 | AivisSpeech 风格 |
| --- | --- | --- |
| neutral | 慵懒 | ノーマル |
| lazy | 慵懒 | おちつき |
| aggrieved | 委屈 | せつなめ |
| shy | 害羞 | あまあま |
| affectionate | 撒娇 | あまあま |
| relieved | 欣慰 | ふつー |
| combat | 战斗 | ノーマル |
| self_deprecating | 自嘲 | ノーマル |
| reproachful | 责备 | ノーマル |

AivisSpeech 每次加载按 `/speakers` 查风格 ID，不硬编码 ID；风格缺失则使用角色默认风格。GPT-SoVITS 每句显式传参考路径、文本及语言，不靠修改全局参考来实现语气。temperature、语速不作为情绪强度。

**参考文本待听校**：现有参考包文件名与训练文本有识别错误或冲突，配置中的文本是候选转写，不是已确认的逐字稿。害羞选择第一条录音；第二条识别更可疑，未启用。尤其战斗开头、自嘲中的“鍛えがい／期待がい”、撒娇及害羞的人名需要原音核验。全部听校前保留 reference_transcripts_verified=false；不能仅凭成功出 WAV 声称音质或八类情绪已验收。

## 接口与停止语义

WebSocket 发送 `{"type":"voice-switch","voice_key":"roxy"}`。status 与 `/health` 增加 managed_voices、available_voices、active_voice_key、target_voice_key、failed_voice_key、voice_phase、voice_error、voice_recovery、voice_label。phase 为 starting/stopping/loading/recovering/ready/error；界面展示实际生效角色，失败目标单独用于重试。浏览器断线不取消服务切换，重连读取当前状态。

切换时停止整个旧回合、取消播放和合成请求；保留已显示文字，不重答。静音或停止语音保留文字生成。异步 HTTP 的取消不保证服务端推理立即停止，旧结果不会重新进入播放队列。单句错误保持引擎、保留文字、继续后续句；两次加载失败后的文字模式可手动重试。

语音进程就绪后意外退出会进入可重试错误状态，保留文字模式，不自动改换引擎；未知启动角色则明确提示并尝试 AivisSpeech 回退。父进程提前退出时仍清理本次自有进程组。

## 验证与试听

2026-10-19 在独立端口9881/10102执行 Roxy→まお→Roxy，不接管原桌宠服务。输入为固定日语短句，不读取个人对话；测试期间一次只运行一个本次自有语音引擎。

| 项目 | 实测 |
| --- | --- |
| Roxy首次启动含预热 | 30.129秒 |
| 切至まお含预热 | 4.163秒 |
| 再切回Roxy含预热 | 6.114秒 |
| Roxy同文热合成 | 1.694–1.906秒 |
| まお同文热合成 | 0.790–0.834秒 |
| 单个自有语音进程采样RSS最大值 | 4814MiB，约4.70GiB |

内存以0.5秒间隔采样进程RSS，不含语言模型、其他进程，也不是整个系统的统一内存峰值。合成耗时从日语文本已就绪开始，到完整 WAV 返回为止，不含语言模型等待及浏览器播放等待。少量样本不能代表长期性能。没有硬速度门槛，实际听感与聊天体验需用户确认。

试听及完整指标位于 `.runtime/voice-evaluations/roxy-integration-20261019/`，其中 `0-roxy-neutral.wav` 使用慵懒参考，`1-mao-neutral.wav` 用于同文对照。每种 tone 均生成 WAV，但两引擎的实际语气不保证一一等价。

复跑时使用新的 label，避免覆盖已有结果：

```bash
.venv/bin/python scripts/evaluate_voices.py --label my-voice-comparison
```

脚本在 finally 中停止本次进程并保存 metrics.json。自动测试使用模拟语音服务覆盖回退、取消、切换、文本保留、风格映射和退出清理；前端播放队列继续覆盖迟到音频及旧回合隔离。

另在独立18081端口启动现有 Qwen3.5-27B，以固定、非个人输入验证新增格式，得到可解析的 `emotion=neutral` 与 `voice_tone=relieved`；测试后已关闭该自有模型服务。界面测试使用独立数据库及模拟语音服务，核验下拉框默认值、Roxy/まお往返切换和切换后聊天。截图 `.runtime/voice-ui-test.png` 不作为真实音质证明。前端构建通过；上游全量 typecheck 仍有原有 Cubism/未使用变量错误，本次 Companion.tsx 没有新增报告错误。
