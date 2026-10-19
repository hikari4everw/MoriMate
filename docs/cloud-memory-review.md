# 云模型与新记忆：独立审查修复记录

审查基线23a5c34，初始实现6f95865；独立审查未修改工作区、读取真实聊天/密钥或调用云API。初始结论No，Critical 0 / Important 6 / Minor 0；Declined to judge为空。

| 发现 | 对应回归（均先RED后GREEN） | 修复 |
|---|---|---|
| 授权撤销保存窗口仍可生成 | test_configuration_update_closes_generation_until_authorization_is_committed | 配置忙状态+版本校验+取消已有任务 |
| 摘要遗漏人工记忆依赖 | test_summary_and_checkpoints_depend_on_manual_memories | 摘要和检查点保存memory ID依赖 |
| 场景清除保留跨场景复述 | test_clear_scene_invalidates_cross_scene_replies_but_preserves_unrelated_history | 统一依赖闭包 |
| 后台整理绕过输入预算 | test_maintenance_budget_limits_oldest_batch_and_leaves_remainder_pending | 预算批次和持久积压标记 |
| 连接测试阻塞控制接收 | test_connection_test_is_scheduled_cancellable_and_does_not_block_controls | 共享槽、可取消独立任务和总超时 |
| 列表同步读取Keychain | test_connection_views_never_wait_on_keychain | 持久化非敏感has_key元数据快照，使用时异步读凭据 |

补充实际TestClient WebSocket回归：test_saving_revoked_authorization_blocks_concurrent_guest_generation / test_slow_connection_test_does_not_block_websocket_stop。

最终完整回归199 passed / 2 skipped，前端10项通过，build/node类型检查通过；renderer既有585条诊断，新增companion没有诊断。未做第二轮独立审查，修复由失败回归及完整套件验证。真实三家API、真实Keychain授权交互、云视觉和首音延迟仍需填密钥后验收。

## 执行裁定

1. 在既有检出目录新建codex/cloud-memory分支，保留资源和LAN基线；代价是仅分支隔离，共享工作目录。
2. 保留runtime文本stream/complete适配层，与新的请求事件接口并存，避免改动已验证语音取消链；代价是维护一个兼容边界。
3. 保留本地request_switch同步发布phase/task的行为，维持断线/取消契约；代价是本地与云切换路径需要同步维护。
4. 构建仅作验证，不充当失败测试，另加Node权限/状态及图片授权回归；代价是仍需浏览器人工检查，已完成隔离环境基础检查。
5. 旧记忆作用域回归改读archive=True，符合用户确认的分代隔离；代价是旧偏好需要重新告知。
6. 紧预算整理优先容纳最旧来源，放不下旧摘要时保留旧检查点但不注入该摘要；代价是滚动摘要可能减少较早覆盖，可用有效来源重建。

无暂缓的Minor。
