# 云模型与记忆 V2 设计

已于 2026-10-19 经 grill-with-docs 逐轮确认。

## 约束
- OpenAI、DeepSeek、Gemini 首版采用兼容 Chat Completions 流式接口，支持自定义 HTTPS 网关和显式 HTTP 代理。
- 密钥仅主人管理，存 macOS Keychain，配置不含明文。全机模型/思考由主人选择；访客必须获得当前云连接授权。语音权限不变。
- 云端可接收人格、相关聊天、记忆、OCR、后台整理；图片单独开关默认关，访客仍无观察能力。
- 失败提示、保留完整输出，不自动重试或回退。用量按空间、连接、用途记录，未知不是零。
- 切云释放自己启动的本地模型，切回重新加载；记住最近成功选择。保留 TTS、OCR、分句与完整取消链。
- 新记忆从升级生效起建立。旧来源、摘要、记忆归档可看，任何生成/整理都不读取。人格保留，不重新上传旧资料。
- 长期记忆优先明确偏好、事实、约定、经历；来源可追溯、变化可保留、不确定冲突待确认。助手猜测不能成为事实。
- 普通遗忘保留原文但排除来源和派生依赖；彻底删除清除相应文本，不影响无关历史。后台写回必须重新核实有效性。
- SQLite 为主存储，中文词项检索先行；先过滤分代、有效性、空间和场景，再排名，再裁剪预算。近期最多10完整回合，相关记忆最多12条，默认文本预算8000token。
- 延续单生成/TTS槽、主人优先、后台低优先。记忆整理空闲15秒且至少6条，最旧未处理优先每批40条，失败等待手动重试。
- 浏览器为主，不加AR/QQ/麦克风/定时提醒/自主游戏控制，不迁移Unity/Milvus，不修改核心人格。

## 研究依据
SillyTavern 06bde939fb1e9c4c8d8641d810f0a916b5bce127：学习作用域、近期上下文与旧事分离、预算、检查点；不是事实治理保证。AGPL-3.0，不复制源码。
ZerolanLiveRobot aad29d093295eef351da03a0bbe6ffac5539716d，Playground fb23d1ed7345d51c73e82d72b1f27750c2e62961，core baefa0f78abe756a91ca231fc2f6439b46ebe266，data 647157ddfe043ab89315c31902b0747158c1706d：学习模型接口和消息契约。云流式有TODO、记忆保存闭环不足、播放缺轮次取消；保留自己的链路。Playground许可证未确认，不拷代码。

参考：https://github.com/SillyTavern/SillyTavern-Docs/blob/ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d/extensions/Chat-vectorization.md
https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/blob/aad29d093295eef351da03a0bbe6ffac5539716d/pipeline/llm/llm_sync.py
https://github.com/AkagawaTsurunaki/zerolan-data/blob/647157ddfe043ab89315c31902b0747158c1706d/src/zerolan/data/protocol/protocol.py
https://ai.google.dev/gemini-api/docs/openai
https://developers.openai.com/api/reference/resources/chat
https://api-docs.deepseek.com/

## 验收
三家模拟流与真实API待密钥验证分别记录。覆盖权限、撤销、取消、断流、资源释放、旧资料隔离、变化、遗忘竞态、积压。运行pytest、前端播放测试、build和主进程类型检查；全renderer已有诊断不冒充全绿。首音3–5秒仍需实测。
