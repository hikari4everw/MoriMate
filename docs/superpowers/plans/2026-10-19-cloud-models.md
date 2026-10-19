# 云模型接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development task-by-task.

**Goal:** 云模型接入，保持现有体验与取消链。
**Architecture:** 能力与编排分离；SQLite事实来源；不迁移底座。
**Tech Stack:** Python/FastAPI/httpx/SQLite/React/TypeScript/macOS Keychain。
**Spec:** docs/superpowers/specs/2026-10-19-cloud-memory-design.md

## Global Constraints
设计说明全部约束适用。保留现有未提交LAN功能。修改仅关联云接入与记忆，记录所有Ruling。

## Review Focus
- 访客伪造配置及撤销后继续消耗API。
- 断流/取消/切换后的迟到结果。
- 场景挤占和助手幻觉写入事实。
- 遗忘与后台整理并发导致复活。
- 迁移中断/重启后归档重新进入上下文。

### Task 1: 连接配置与密钥管理

**Files:** connections.py、credentials.py、tests/test_connections.py（后端模块在 backend/src/open_llm_vtuber/companion，前端在 desktop/src/renderer/src/companion）
**Interfaces:** ConnectionSpec / ConnectionStore.save/delete / ConnectionView（只返has_key）
**Behavior:** 配置、URL/代理验证、Keychain可注入；保存不联网，密钥不落文件/状态/异常。

- [ ] 编写可观察行为的失败测试（包括相关Review Focus）。
- [ ] 运行 `.venv/bin/pytest tests/test_connections.py -q`，Expected: 新行为未实现导致失败。
- [ ] 最小实现以上接口和行为，疑义按spec裁定并写ledger。
- [ ] 运行 `.venv/bin/pytest tests/test_connections.py -q`，Expected: PASS；任务结束运行后端全套回归。
- [ ] 提交任务、验证结果及说明。

### Task 2: 统一流式模型接口

**Files:** model_client.py、cloud_model.py、model.py、tests/test_cloud_model.py（后端模块在 backend/src/open_llm_vtuber/companion，前端在 desktop/src/renderer/src/companion）
**Interfaces:** GenerationRequest、ModelEvent、GenerationResult、ModelClient.stream；保留runtime兼容stream/complete入口
**Behavior:** 分离本地参数与三家参数，推理不输出，明确结束/截断/断流/取消，无自动重试。

- [ ] 编写可观察行为的失败测试（包括相关Review Focus）。
- [ ] 运行 `.venv/bin/pytest tests/test_cloud_model.py tests/test_model.py -q`，Expected: 新行为未实现导致失败。
- [ ] 最小实现以上接口和行为，疑义按spec裁定并写ledger。
- [ ] 运行 `.venv/bin/pytest tests/test_cloud_model.py tests/test_model.py -q`，Expected: PASS；任务结束运行后端全套回归。
- [ ] 提交任务、验证结果及说明。

### Task 3: 模型路由、共享权限与用量

**Files:** model_router.py、usage.py、app.py、runtime.py、model_service.py、scripts/run_local.py（后端模块在 backend/src/open_llm_vtuber/companion，前端在 desktop/src/renderer/src/companion）
**Interfaces:** ModelRouter.select/snapshot；UsageStore.record
**Behavior:** 主人切换、访客授权和撤销，全回合取消、释放自有本地进程、恢复选择，不接管外部服务。

- [ ] 编写可观察行为的失败测试（包括相关Review Focus）。
- [ ] 运行 `.venv/bin/pytest tests/test_model_router.py tests/test_lan_app.py tests/test_model_service.py tests/test_runtime.py -q`，Expected: 新行为未实现导致失败。
- [ ] 最小实现以上接口和行为，疑义按spec裁定并写ledger。
- [ ] 运行 `.venv/bin/pytest tests/test_model_router.py tests/test_lan_app.py tests/test_model_service.py tests/test_runtime.py -q`，Expected: PASS；任务结束运行后端全套回归。
- [ ] 提交任务、验证结果及说明。

### Task 4: 配置界面与云端验收

**Files:** model-settings.tsx、protocol.ts、Companion.tsx及前端状态测试（后端模块在 backend/src/open_llm_vtuber/companion，前端在 desktop/src/renderer/src/companion）
**Interfaces:** 配置CRUD、测试/使用连接、授权、用量；保留turn_id/sentence_id
**Behavior:** 输入提交后清空密钥，图片能力与上传授权双检，固定无隐私连接测试，实网结果另记。

- [ ] 编写可观察行为的失败测试（包括相关Review Focus）。
- [ ] 运行 `npm --prefix desktop run build`，Expected: 新行为未实现导致失败。
- [ ] 最小实现以上接口和行为，疑义按spec裁定并写ledger。
- [ ] 运行 `npm --prefix desktop run build`，Expected: PASS；任务结束运行后端全套回归。
- [ ] 提交任务、验证结果及说明。

