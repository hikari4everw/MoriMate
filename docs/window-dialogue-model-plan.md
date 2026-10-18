# 窗口、对话与候选模型实施方案

2026-10-18，依据用户确认的设计实施。默认模型保持 Qwen3.5-27B，候选通过功能检查和用户试听后才切换。本轮不迁移 Codex 本地模型。

## 交付与验收

1. 原生观察：在主线程初始化 AppKit，再创建 ScreenCaptureKit filter；启动器按源码更新时间重编译。测试窗口验证预览、裁剪、OCR、失效窗口、取消及超时。
2. 对话链路：完整双语句生成后立即发文字，日语顺序合成。文字和音频共享回合及句子标识；停止语音保留文字，新消息取消旧回合。正常回复上限 8192 token，主动评论 400，记忆整理 800；检测截断。
3. 对话风格与时间：闲聊自然简短；默认故事约 500–1000 中文字且有结尾。共同梗低频使用，日常不朗读动作旁白。每轮注入本地日期、星期、时区和时段；历史附记录时间，摘要附来源时间范围，区分事件与记录时间。
4. 候选准备：llama.cpp v0.7.0 / Metal；Qwen3.8-27B-Heretic-JP-Roleplay-NSFW 普通 Q4_K_M 与 F16 projector 固定 revision、文件和 SHA256。上下文 32768，单并发，沿用 18080。旧模型和资源保留。
5. 思考策略：闲聊 128、叙事/详细分析 512、主动评论 64 token；记忆整理关闭。使用 reasoning_budget_tokens，思考独立于最终 content，不进入文字、语音或记忆。候选以作者建议 temperature 0.6、top_p 0.95、top_k 20 起步。
6. 体验：闲聊首音目标 5–8 秒，叙事/认真讨论约 20 秒。超出显示等待状态，继续生成并允许打断，不自动重试。正文先显示，字幕/口型/表情跟随播放。
7. 对照：旧模型与候选的关闭/有限/完整 thinking 顺序运行同题样本，记录冷启动与暖请求首字、首音、总耗时、内存、双语格式、内容完整性和图片理解。原始文字、音频只保存在 .runtime。技术记录可入库，project.md 的个人新增内容不入库。
8. 发布条件：先自动检查，后用户试听；真实游戏负载连续 30 分钟需单独验收。未执行的真机/用户步骤必须标为未验收，不能用单测或合成负载替代。

## 接口约定

- sentence 事件包含 turn_id、sentence_id、zh、ja、emotion、source_id；audio 事件使用相同句子标识。history 只由 sentence 追加。
- 模型能力在配置中明确声明，不按模型名字猜测是否支持图片。
- 模型推理、总生成预算、播放分句分别管理。预算截断保留已完整解析的句子，排空其语音队列后结束。
- 记忆来源沿用已有 UTC created；相对时间以原始记录时间解读，缺失事件时间不能用记录时间冒充。

## 来源

- 作者：https://huggingface.co/sss22213/Qwen3.8-27B-Heretic-JP-Roleplay-NSFW?not-for-all-audiences=true
- 量化：https://huggingface.co/mradermacher/Qwen3.8-27B-Heretic-JP-Roleplay-NSFW-GGUF?not-for-all-audiences=true
- 请求预算：https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.7.0/tools/server/server-common.cpp

本文记录目标与接口；实际结果见验证报告，不能把目标当作已通过结果。
