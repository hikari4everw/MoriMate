# 本地桌宠实施计划

> 执行方式：superpowers:executing-plans；功能测试先失败再实现。用户已批准直接实施。

目标：以固定 OLV 后端/桌面源码实现个人 macOS 桌宠，中文显示、日语语音、可编辑记忆、只读游戏旁观。
规格：project.md 与本次用户批准路线；后者优先。

## 约束
- 默认只使用本地服务；不请求麦克风，不启用 MCP/命令执行，不自动观察。
- M5 Pro 48 GiB；先 Qwen3.5-9B 4bit，Qwen3-8B 对照；VOICEVOX 小夜。
- 保留上游许可、明确模型/音色独立许可；不公开发布。
- 停止观察/发言立即生效；旧任务不能在下一回合恢复播放。

## 任务与验收
1. 导入基线、安装限定依赖、配置本地运行脚本。验证：前端基线 typecheck/build，后端最小导入。
2. 双语输出和 VOICEVOX：`companion/bilingual.py` 将不完整流解析成中文/日语短句；`tts/voicevox_tts.py` 提供可取消的异步合成。测试：碎片、非法/缺字段、思考内容、HTTP错误、取消。
3. `companion/memory.py` SQLite 保存消息、来源记忆、剧情摘要及删除屏障。测试：重启、纠正/删除后旧源无法再次提取、用户事实与观察分离。
4. `companion/runtime.py` 复用 OLV 输出/音频协议，统一回合取消、主动性、静音、语音队列。测试：旧回合不发送、用户优先、静音保留文字、异常可恢复。
5. `native/Capture.swift` 指定窗口/区域捕获与 Vision OCR；`companion/observation.py` 文字稳定去重、场景会话、过期调度。测试：打字机、相同句、多窗口切换、停止后的在途结果；Swift 编译和原生 OCR 验证。
6. 桌面控制：保留原 Live2D，增加文字输入、独立控制、区域框选、记忆管理和状态错误提示。验证：TypeScript、生产构建、浏览器/Electron 冒烟。
7. 下载实际本地模型/音色，运行人格样例与声音测量，记录原始结果。真实虚拟机和用户主观试听没有实际完成前不宣称通过。

## 接口
- 模型短句：每行 JSON `{ "zh": "...", "ja": "...", "emotion": "neutral|joy|smirk|sadness|anger|surprise" }`；不完整行不发送 TTS。
- 继续使用 OLV `DisplayText`、`Actions`、音频 WebSocket 消息；扩展 `turn_id` 防止延迟包越过取消边界。
- 原生帮助程序：`list` 输出可捕获窗口；`capture WINDOW_ID [x y width height]` 输出裁剪图像与 OCR JSON，坐标归一化。
- SQLite 是唯一持久记忆源；模型每轮重建上下文，不缓存已删除记忆。

## 审查重点
- 取消后 HTTP/模型/原生截图迟到；删除后摘要复活；源窗口消失不可退回全屏。
- 畸形双语内容不朗读 JSON/思考；本地服务故障不静默改用云端。
- 日语与中文字幕的语义一致性及人格自然度必须人工听评，测试不能代替。
