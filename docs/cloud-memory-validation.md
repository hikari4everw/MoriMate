# 云模型与记忆升级验证

日期：2026-10-19。

## 自动化
基线146 passed/2 skipped。实现后190 passed/2 skipped（需要本机fixture监听权限）；现有websockets/audioop弃用警告5条。
新增覆盖：凭据不落配置、三家兼容SSE、推理隔离、异常断流/限流/取消、截图授权、主人模型权限、访客授权/撤销、释放本地进程、未知用量、归档隔离、迁移幂等和中断回滚、持久回合、记忆变化/待确认、依赖遗忘和人工纠正、中文召回/预算、最旧积压、后台取消/失败暂停/重启/遗忘竞态。

前端：播放及模型权限状态10项通过；production build、主进程typecheck通过。全renderer仍存在上游Cubism诊断，本次新增companion文件诊断已处理，不能称全仓类型检查通过。

## 浏览器
使用 tests/fixtures/lan_ui_backend.py 的临时空间和假模型，未读取真实聊天或Keychain。云配置表单、三家预设、默认关闭图片/访客授权可见；测试消息的中文回复及本轮引用显示正常（估算1348/8000 token，来源ID1）。布局在窄窗可滚动。真实API密钥未提供，尚无三家账号、云视觉、人格音色或延迟验收结论。

## 来源
SillyTavern release 06bde939fb1e9c4c8d8641d810f0a916b5bce127（1.19.0），Docs ed5423d809c6b10b8c1e9ef4a0c8ddc7f2ef7f3d。借鉴上下文作用域、预算、摘要检查点和引用诊断；不复制AGPL源码、不采用Extras/Milvus/Vectra。
Zerolan主仓 aad29d093295eef351da03a0bbe6ffac5539716d，AR fb23d1ed7345d51c73e82d72b1f27750c2e62961，core baefa0f78abe756a91ca231fc2f6439b46ebe266，data 647157ddfe043ab89315c31902b0747158c1706d。借鉴模型能力与事件契约；未移植其不完整流式/记忆/取消链。AR许可证未确认，不拷贝代码。

实际虚拟机游戏及连续30分钟陪伴仍待人工验收。云端及本地首音指标不可从模拟测试推断。
