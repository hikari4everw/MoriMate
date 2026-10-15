# 执行记录 — docs/implementation-plan.md

2026-10-15：已读现有 project.md、已确认路线与两个上游 CLAUDE.md。

Ruling: 当前目录没有已有 Git 历史，直接初始化 feat/local-companion 并导入固定源码快照，不额外创建 worktree；避免给空项目制造两份工作目录。代价：上游升级需按 UPSTREAM.json 对比快照。
Ruling: 在上游后端包内增加专用本地入口，复用输出类型、音频协议和桌面渲染，不启动全量 ServiceContext；避免 V1 自动加载 ASR/MCP及无关云端SDK。代价：两种入口需明确文档，本地入口仅支持V1。
Pre-flight: 双语解析→回合运行器使用同一短句类型；记忆修改→每轮重新读取SQLite；截图→去重器传窗口会话标识；取消→前后端均检查turn_id。

状态：基线导入完成；实现与真机验证进行中。

已实现并验证：NDJSON双语解析、VOICEVOX动态音色发现与取消、SQLite来源记忆、观察去重／频率门限、回合隔离运行器、单用户WebSocket入口、独立桌面V1界面。后端22项通过，前端回合隔离2项通过，桌面build通过；上游全仓typecheck已有错误，新companion文件无新增错误。
真机：Qwen3.5-9B-4bit（revision 8b2b98c00a6b4d291155e4890773ca8f769aee53）和VOICEVOX0.25.2已运行。首次完整首句WAV生成4.700秒，第二句5.736秒；非扬声器指标、非统计结论。日语语义自然度仍需人工评价。
Hiyori来源：CubismWebSamples b1de66b0b1f1cb881d95fb6158622aeb6a2827bd，添加本地基础表情配置。Web UI实见Hiyori加载成功。
仍需：原生OCR权限排查、回合并发/过时评论补强、模型对照、启动/安装脚本、指标样本及用户验收文档、最终审查。
Ruling: V1桌宠使用380×620的可聚焦透明置顶窗口，而非上游覆盖所有屏幕的鼠标穿透窗；必须能直接打字和停止语音，且不依赖已移除的麦克风/全屏hover控制。代价：透明空白区域暂不鼠标穿透，可拖动标题栏移动整个桌宠。
2026-10-16 用户反馈：小夜音色不满意，要求对比AivisSpeechまお；硬盘已释放到约109GiB，允许更大本地模型。新增Qwen3.5-27B-4bit对照，仍逐个加载、不默认云端。
