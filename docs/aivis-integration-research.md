# AivisSpeech まお本地接入核查

核查日期：2026-10-16（Asia/Hong_Kong）。范围为官方 release、1.2.0 源码及公开 API；未安装或启动引擎。本报告中的命令是待执行步骤，不能视为性能验证。

## 可直接使用的下载与安装

GitHub releases/latest 实时返回稳定版 **1.2.0**（2026-04-30 发布，非 dev/preview）。macOS arm64 包 203,306,908 bytes，SHA-256 `581a7f34afb2c5343ff8b27d6d88859083375868607657de95fad506939185df`。来源：[官方 release API](https://api.github.com/repos/Aivis-Project/AivisSpeech-Engine/releases/latest)、[固定 release](https://github.com/Aivis-Project/AivisSpeech-Engine/releases/tag/1.2.0)。

```sh
curl -fL --retry 2 'https://github.com/Aivis-Project/AivisSpeech-Engine/releases/download/1.2.0/AivisSpeech-Engine-macOS-arm64-1.2.0.7z.001' -o AivisSpeech-Engine-macOS-arm64-1.2.0.7z.001
```

解压后的 `run` 可按以下参数启动，工作目录应为解压目录；解压目录层级仍需实际包检查：

```sh
./run --host 127.0.0.1 --port 10101 --disable_sentry --cors_policy_mode localapps
```

`--disable_sentry` 确实关闭 Sentry 错误日志收集。安装模型阶段不要传 `--disable_mutable_api`，否则安装路由被拒绝。安装完成后可以用该参数重启。`--use_gpu` 在此版本仅实现 CUDA/DirectML，在 Mac 会退回 CPU，没有 Metal/CoreML 分支。来源：[CLI 源码](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/run.py)、[推理 provider 源码](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/tts_pipeline/style_bert_vits2_tts_engine.py)。

目标是 **まお（Oz Chat）**，不是「亜空マオ」。实时公开元数据：模型 UUID `a59cb814-0083-4369-8542-f51a29e72af7`，话者 UUID `e756b8e4-b606-4e15-99b1-3f9c6a1b2317`；AIVMX 版本1.2.0，258,037,076 bytes，SHA-256 `f87ccea2e8e2de0e0bfe52e803945af903b4086bf25621a015111628f00e4119`。来源：[模型页面](https://hub.aivis-project.com/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7)、[公开模型 API](https://api.aivis-project.com/v1/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7)。

推荐在引擎启动成功后，通过安装接口传页面 URL：

```sh
curl -f -X POST 'http://127.0.0.1:10101/aivm_models/install' \
  -F 'url=https://hub.aivis-project.com/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7'
curl -f 'http://127.0.0.1:10101/aivm_models/a59cb814-0083-4369-8542-f51a29e72af7'
curl -f 'http://127.0.0.1:10101/speakers'
```

源码确定安装成功返回204，表单字段也支持 `file=@...aivmx`。页面 URL 会由引擎解析为以下公开下载 API；该 API 已通过真实匿名 GET Range 请求验证 HTTP206、一个字节、application/octet-stream。HEAD 返回405，不能据此判断下载失效。

```sh
curl -fL 'https://api.aivis-project.com/v1/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7/download?model_type=AIVMX' -o mao-1.2.0.aivmx
```

该 URL 指向最新文件，下载后应核对上述版本和 checksum。来源：[安装路由](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/app/routers/aivm_models.py)、[URL 转换及下载实现](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/aivm_manager.py)。

## 数据目录与联网行为

发布包默认写入 `~/Library/Application Support/AivisSpeech-Engine/`，模型在 `Models/`，BERT缓存在 `BertModelCaches/`，日志在 `Logs/`。源码版目录名称后缀为 `-Dev`。1.2.0 CLI **没有用户数据根目录参数**；`--setting_file` 和 `--preset_file` 仅重定向对应文件，不会迁移模型/缓存。不要为隔离目录擅自覆盖 HOME。若要让包运行时使用项目目录，需要另外设计可审计的目录方案，不能编造 `--user_data_dir` 参数。来源：[路径实现](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/utility/path_utility.py)、[run.py](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/run.py)。

首次启动会自动取得默认模型配置、模型及 BERT 缓存；关闭 Sentry 不等于所有联网关闭。模型管理器还读取官方默认模型/移除规则并做后台扫描。不能承诺启动只下载まお。首次运行需记录实际新增文件、下载量、RAM与冷启动耗时。来源：[模型管理器初始化](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/aivm_manager.py)、[BERT初始化](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/tts_pipeline/style_bert_vits2_tts_engine.py)。

## 与现有 VOICEVOX 适配器的关系

复用 `/speakers` → `/audio_query?text=...&speaker=...` → `/synthesis?speaker=...` 是合适的第一轮集成。必须按名称 `まお` 和选中风格从运行中 `/speakers` 取得全局 style id；Hub显示的0–5是模型内局部id，不能直接用于兼容API。风格为ノーマル、ふつー、あまあま、おちつき、からかい、せつなめ。先用ノーマル比较，随后可试からかい，不能将“更强情绪参数”等同音质更好。来源：[兼容说明](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/README.md#voicevox-api-との互換性について)、[模型 API](https://api.aivis-project.com/v1/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7)。

保持 audio_query 返回体原样交给 synthesis：`kana` 是实际朗读文本；`intonationScale` 是风格情绪强度；新增 `tempoDynamicsScale` 控制速度变化；`pauseLength/pauseLengthScale` 被忽略；pitchScale非零可能劣化声音。不要把 VOICEVOX 的调音数值直接迁移。HTTP请求取消不能被宣称为引擎侧算力取消，CLI的 cancellable synthesis 参数在源码里被注释。首次合成可能较慢，可通过 `POST /aivm_models/{uuid}/load` 预热并单列冷启动。

## 来源和条款记录

引擎代码使用 LGPL-3.0（与声音模型的条款分开）。まお发布者为官方认证 Oz Chat，创作者列 Oz Chat / Trippy / ねゆたろ，©Oz Chat/Trippy；模型许可证 **ACML 1.0**。可在来源记录写 `AivisSpeech: まお`，这是作者建议的署名写法。ACML允许符合其限制的个人及商业使用、署名可选；再分发模型时需带许可文本。不得把音色许可当作Hiyori形象授权。来源：[引擎许可证](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/LICENSE)、[まお元数据](https://api.aivis-project.com/v1/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7)、[ACML1.0原文](https://github.com/Aivis-Project/ACML/blob/master/ACML-1.0.md)。

## 确证范围

已确证：官方稳定版和资源摘要、目标模型身份/版本/摘要/条款、匿名下载端点可访问、CLI参数、安装和兼容API源码。未确证：发布包解压目录、签名/Gatekeeper行为、M5 Pro上启动和合成延迟、实际内存、与项目前端音频链路联调；这些需要主任务执行实测。

### 首启下载的补充实证

实时 `GET https://api.aivis-project.com/v1/aivisspeech/default-models` 返回 **コハク1.1.0 + まお1.2.0**，并非Anneli；1.2.0源码的离线fallback也固定为这两个。コハク UUID `22e8ed77-94fe-4ef2-871f-a86f94e9a579`，公开元数据 AIVMX 为255,326,987 bytes。启动前仅放置まお仍会补装コハク：源码对每个默认UUID逐一检查，而不是“有任意模型就跳过”。因此正常联网首次启动可直接自动装好まお，随后先检查 `/speakers`，不必重复调用安装。来源：[默认清单 API](https://api.aivis-project.com/v1/aivisspeech/default-models)、[コハク元数据](https://api.aivis-project.com/v1/aivm-models/22e8ed77-94fe-4ef2-871f-a86f94e9a579)、[默认清单客户端及fallback](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/voicevox_engine/utility/aivishub_client.py)。

BERT固定仓库为 `tsukumijima/deberta-v2-large-japanese-char-wwm-onnx`，revision `d701ec67708287b20d2063270f6b535e6eed09ab`。引擎固定依赖的 Style-Bert-VITS2 加载代码选择 **model_fp16.onnx**，该文件653,075,699 bytes，另加tokenizer等约0.52MB；不是同仓库1.3GB的fp32文件。直接文件地址：[model_fp16.onnx](https://huggingface.co/tsukumijima/deberta-v2-large-japanese-char-wwm-onnx/resolve/d701ec67708287b20d2063270f6b535e6eed09ab/model_fp16.onnx)。证据：[固定依赖](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/1.2.0/pyproject.toml)、[实际加载文件名](https://github.com/tsukumijima/Style-Bert-VITS2/blob/1588a46595141a04652f9aae486f8a625c04ce55/style_bert_vits2/nlp/onnx_bert_models.py)、[固定revision文件大小 API](https://huggingface.co/api/models/tsukumijima/deberta-v2-large-japanese-char-wwm-onnx/tree/d701ec67708287b20d2063270f6b535e6eed09ab?recursive=true)。

由以上文件大小估算，首次新增核心权重约 **1.167 GB十进制**（两音色+BERT），加引擎下载包约203MB及解压后的库；实际磁盘占用需安装后测量。没有为了核查写入用户 Application Support。
