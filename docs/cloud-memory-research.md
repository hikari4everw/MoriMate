# 云接入与记忆架构调研

调研日期：2026-10-19。以下区分源码已实现能力和本项目的采用决定，没有运行外部仓库代码。

## SillyTavern

源码基线 `06bde939fb1e9c4c8d8641d810f0a916b5bce127`（1.19.0），文档基线 `ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d`。

| 机制 | 实际作用 | 本项目决定 |
|---|---|---|
| Summarize | 内置扩展生成滚动摘要，支持查看、编辑和恢复；摘要可能遗漏或编造 | 引入检查点、来源和重建，摘要保持为派生资料 |
| World Info | 按条件激活设定条目，并控制作用域、预算和注入位置 | 采用作用域和预算，不加入递归设定触发或完整世界书编辑器 |
| Chat Vectorization | 用近期内容检索旧聊天，保留近期消息并补充相关过去记录 | 采用近期完整回合与相关记忆分开选择的结构；先用中文词项检索 |
| Data Bank | 管理用户附件及其检索作用域 | 暂不新增附件系统，不导入完整游戏剧本或攻略 |

依据：[摘要](https://github.com/SillyTavern/SillyTavern-Docs/blob/ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d/extensions/Summarize.md)、[World Info](https://github.com/SillyTavern/SillyTavern-Docs/blob/ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d/Usage/worldinfo.md)、[聊天召回](https://github.com/SillyTavern/SillyTavern-Docs/blob/ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d/extensions/Chat-vectorization.md)、[Data Bank](https://github.com/SillyTavern/SillyTavern-Docs/blob/ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d/Usage/Characters/data-bank.md)。

SillyTavern 的上述机制不提供本项目要求的统一来源依赖、可靠遗忘及游戏进度隔离。助手历史可以帮助回忆交流，但不能证明用户偏好或实际剧情。向量删除不等于派生摘要全部失效。本次自行实现接口与策略，没有复制其 AGPL-3.0 源码。

## Zerolan 四仓

| 仓库及固定提交 | 实际职责 | 借鉴决定 |
|---|---|---|
| ZerolanLiveRobot `aad29d093295eef351da03a0bbe6ffac5539716d` | 对话编排、事件、模型/语音客户端及展示桥 | 模型能力与对话流程分离 |
| ZerolanPlayground `fb23d1ed7345d51c73e82d72b1f27750c2e62961` | Unity AR/Live2D、字幕与音频播放 | 绑定字幕、音频与表现元数据，继续使用浏览器 |
| zerolan-core `baefa0f78abe756a91ca231fc2f6439b46ebe266` | 独立推理服务，含模型、语音、OCR、图像描述及 Milvus 接口 | 保留本地TTS/OCR与可替换LLM边界，不再部署重复服务 |
| zerolan-data `647157ddfe043ab89315c31902b0747158c1706d` | Pydantic 请求、响应和传输协议 | 集中声明模型请求/事件与前端新增配置类型 |

这些提交是分别查阅的版本，不代表四仓联合验证过的兼容发布。

源码限制影响了采用范围：

- [同步云客户端](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/pipeline/llm/llm_sync.py) 的流式适配仍有 TODO；[异步客户端](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/pipeline/llm/llm_async.py) 等待整体 JSON，不是本项目需要的逐句流。
- [历史管理](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/manager/llm_prompt_manager.py) 超限重置整段历史；[主流程](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/bot.py) 虽有摘要写入函数，查阅主链路未确认自动长期保存闭环，召回也缺少本项目的来源治理。
- [事件执行器](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/event/event_emitter.py) 的超时记录不等于取消；[Unity 语音处理](https://github.com/AkagawaTsurunaki/ZerolanPlayground/blob/fb23d1ed7345d51c73e82d72b1f27750c2e62961/Assets/Scripts/Handlers/SpeechHandler.cs) 未提供本项目的轮次失效链。继续保留用户优先、turn_id 和播放 epoch。
- [Screen 工厂](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/devices/screen/base_screen.py) 没有本项目所需的 macOS ScreenCaptureKit 路径。OCR 与图像理解继续互补使用。
- Playground 依赖 Unity/Vuforia 和额外资源；本次未确认其项目根许可证，不直接移植代码。其余三仓有 MIT 声明。本轮均仅借鉴设计。

## 对当前项目的直接改进

1. 模型连接、流式传输、全机模型选择分开；云连接不伪装成本地进程。
2. 近期对话、相关记忆和摘要由独立上下文组装器选择，并返回引用清单。
3. 旧资料归档，新资料使用独立分代；迁移和重启不能把旧历史重新带回。
4. 记忆保留变化和来源依赖；遗忘影响相关派生内容，不再清空全部助手历史。
5. 后台整理从最旧未处理来源推进，失败暂停，避免遗漏旧积压或反复消耗 API。

后续是否加入语义检索，应由真实中文昵称、改口和旧事召回的失败案例决定；当前没有依据引入独立向量数据库或整体更换底座。
