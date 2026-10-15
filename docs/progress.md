# 执行记录 — docs/implementation-plan.md

2026-10-15：已读现有 project.md、已确认路线与两个上游 CLAUDE.md。

Ruling: 当前目录没有已有 Git 历史，直接初始化 feat/local-companion 并导入固定源码快照，不额外创建 worktree；避免给空项目制造两份工作目录。代价：上游升级需按 UPSTREAM.json 对比快照。
Ruling: 在上游后端包内增加专用本地入口，复用输出类型、音频协议和桌面渲染，不启动全量 ServiceContext；避免 V1 自动加载 ASR/MCP及无关云端SDK。代价：两种入口需明确文档，本地入口仅支持V1。
Pre-flight: 双语解析→回合运行器使用同一短句类型；记忆修改→每轮重新读取SQLite；截图→去重器传窗口会话标识；取消→前后端均检查turn_id。

状态：基线导入完成；实现与真机验证进行中。
