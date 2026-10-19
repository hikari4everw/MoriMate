import asyncio
import anyio
import contextlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .capture import WindowCapture
from .guest_server import LanSharing
from .memory import MemoryStore
from .model import LocalModel
from .model_service import ModelService
from .model_router import ModelRouter
from .observation import StableText, CommentGate
from .runtime import CompanionRuntime
from .scheduling import GenerationScheduler
from .voice_service import BUSY_PHASES, VoiceService
from ..tts.voicevox_tts import TTSEngine

ROOT = Path(__file__).resolve().parents[4]

GUEST_COMMANDS = {
    "text-input",
    "interrupt-signal",
    "stop-speech",
    "audio-play-start",
    "frontend-playback-complete",
    "settings",
    "memory-delete",
    "memory-edit",
    "scene-clear",
    "memories",
    "memory-archive",
    "memory-retry",
    "memory-rebuild",
    "memory-confirm",
    "status",
    "persona-get",
    "persona-set",
    "persona-reset",
    "voice-switch",
}


def allowed_origin(origin, server_port=12393):
    if origin is None:
        return True  # Native loopback clients do not send Origin.
    if origin in {"null", "file://"}:
        return False
    parsed = urlparse(origin)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.port in {server_port, 5173}
    )


def region(value):
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in value)
    ):
        raise ValueError("框选区域无效")
    x, y, w, h = value
    if w < 0.01 or h < 0.01 or x + w > 1.001 or y + h > 1.001:
        raise ValueError("框选区域无效")
    return value


def create_app(
    root=ROOT,
    model=None,
    voice=None,
    capture=None,
    *,
    model_service=None,
    voice_service=None,
    database_path=None,
    connection_store=None,
):
    config = json.loads((root / "config/companion.json").read_text())
    config["model"] = os.environ.get("COMPANION_MODEL", config["model"])
    config["backend"] = os.environ.get(
        "COMPANION_MODEL_BACKEND", config.get("backend", "mlx")
    )
    config["supports_vision"] = (
        os.environ.get(
            "COMPANION_MODEL_VISION",
            "1" if config.get("supports_vision", False) else "0",
        )
        == "1"
    )
    config["voice_label"] = config.get("voice_label", "VOICEVOX:小夜/SAYO")
    if os.environ.get("COMPANION_VOICE") == "mao":
        config.update(
            tts_url="http://127.0.0.1:10101",
            speaker_name="まお",
            style_name="ノーマル",
            voice_label="AivisSpeech:まお",
        )

    if os.environ.get("COMPANION_VOICE") == "sayo":
        config.update(
            tts_url="http://127.0.0.1:50021",
            speaker_name="小夜/SAYO",
            style_name="ノーマル",
            voice_label="VOICEVOX:小夜/SAYO",
        )

    # Explicitly injected models never start a real process, even in managed tests.
    manager = model_service
    if (
        manager is None
        and model is None
        and os.environ.get("COMPANION_MANAGED_MODELS") == "1"
    ):
        manager = ModelService(root)

    voices = voice_service
    if (
        voices is None
        and voice is None
        and os.environ.get("COMPANION_MANAGED_VOICES") == "1"
    ):
        voices = VoiceService(root)

    router = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal router
        app.state.connected = False
        app.state.sharing = LanSharing(root, websocket, revoke)
        mount_assets(app.state.sharing.app)
        if manager:
            manager.on_change = broadcast_model_change
        if voices:
            voices.on_change = broadcast_voice_change
        try:
            router = ModelRouter(root, manager, fallback=model, fallback_state=model_state(), connections=connection_store)
            router.on_change = broadcast_model_change
            app.state.model_router = router
            if manager or router.selection_path.exists():
                await router.start(os.environ.get("COMPANION_MODEL_KEY", "27b"), force=os.environ.get("COMPANION_FORCE_MODEL") == "1")
            app.state.store = MemoryStore(
                database_path or root / ".runtime/companion.sqlite3"
            )
            if voices:
                app.state.voice_startup = asyncio.create_task(
                    voices.start(os.environ.get("COMPANION_VOICE") or None)
                )
            yield
        finally:
            await app.state.sharing.close()
            if voices:
                await voices.close()
            if router:
                await router.close()
            if hasattr(app.state, "store"):
                app.state.store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.model_service = manager
    app.state.voice_service = voices
    connections = {}
    scheduler = GenerationScheduler()
    changed_by = ""

    async def broadcast_model_change():
        for connection in list(connections.values()):
            with contextlib.suppress(Exception):
                await connection["model_changed"]()

    async def broadcast_voice_change():
        for connection in list(connections.values()):
            with contextlib.suppress(Exception):
                await connection["voice_changed"]()

    async def revoke(guest_id, code, reason):
        connection = connections.get(guest_id)
        if connection:
            connection["close"] = (code, reason)
            connection["revoked"].set()
            await connection["done"].wait()

    async def stop_all():
        for connection in list(connections.values()):
            await connection["stop"]()

    async def share_status():
        sharing = app.state.sharing
        owner = connections.get("owner")
        if owner:
            with contextlib.suppress(Exception):
                await owner["send"](
                    sharing.snapshot(sum(key != "owner" for key in connections))
                )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:12393",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET"],
    )
    model = model or LocalModel(
        config["model_url"], config["model"], backend=config["backend"]
    )
    voice = (
        voices
        or voice
        or TTSEngine(
            base_url=config["tts_url"],
            speaker_name=config["speaker_name"],
            style_name=config["style_name"],
            cache_dir=str(root / ".runtime/audio"),
        )
    )
    capture = capture or WindowCapture(root / ".runtime/window-observer")

    def model_state():
        if router:
            return router.snapshot()
        if manager:
            return manager.snapshot()
        return {
            "managed_models": False,
            "active_model_key": None,
            "target_model_key": None,
            "thinking_mode": "off"
            if config["backend"] == "mlx"
            else model.thinking_mode,
            "available_models": [],
            "model_phase": "ready",
            "model_error": "",
            "model_recovery": "",
            "failed_model_key": None,
            "model": config["model"],
            "supports_vision": config["supports_vision"],
        }

    changing_thinking = False

    def voice_state():
        if voices:
            return voices.snapshot()
        return {
            "managed_voices": False,
            "available_voices": [],
            "voice_phase": "ready",
            "voice_label": config["voice_label"],
            "voice_error": "",
            "voice_recovery": "",
        }

    def model_ready():
        return (
            not changing_thinking
            and (router.ready if router else manager is None or manager.ready)
            and (voices is None or voices.phase not in BUSY_PHASES)
        )

    @app.get("/health")
    async def health():
        return {
            "status": "ok" if model_ready() else "unavailable",
            "local_only": True,
            **model_state(),
            **voice_state(),
        }

    @app.get("/api/session")
    async def session():
        return {"role": "owner", "authenticated": True}

    @app.websocket("/client-ws")
    async def owner_websocket(ws: WebSocket):
        await websocket(ws)

    async def websocket(ws: WebSocket, guest=None):
        nonlocal changing_thinking, changed_by
        owner = guest is None
        identity = "owner" if owner else guest["id"]
        if owner and not allowed_origin(ws.headers.get("origin"), ws.url.port):
            await ws.close(code=1008)
            return
        if identity in connections:
            await ws.accept()
            await ws.close(code=4409, reason="此身份已有聊天页面，请先关闭原页面")
            return
        connection = {
            "revoked": asyncio.Event(),
            "done": asyncio.Event(),
            "close": None,
        }
        connections[identity] = connection
        if owner:
            app.state.connected = True
        await ws.accept()
        lock = asyncio.Lock()

        async def send(payload):
            async with lock:
                await ws.send_json(payload)

        runtime = CompanionRuntime(
            app.state.store
            if owner
            else MemoryStore(app.state.sharing.registry.database(identity)),
            router.bind(identity),
            voice,
            send,
            (root / "config/persona.md").read_text(),
        )
        runtime.persona = runtime.store.persona(runtime.persona)
        runtime.scheduler = scheduler
        runtime.priority = 0 if owner else 1
        runtime.can_generate = lambda: model_ready() and router.allowed(identity)
        gate, stable = CommentGate(), StableText()
        selected = None
        dialogue_region = None
        game_region = None
        observing = False
        playing = False
        last_activity = time.monotonic()
        latest = None
        observer_task = None
        capture_task = None

        async def status():
            services = {**model_state(), **voice_state()}
            if not owner:
                services["available_models"] = []
                # Model process paths and diagnostic details belong to the host.
                services.pop("model", None)
                for key in (
                    "model_error",
                    "model_recovery",
                    "voice_error",
                    "voice_recovery",
                ):
                    if services.get(key):
                        services[key] = "共享服务发生错误，请联系主人或重试"
            await send(
                {
                    "type": "status",
                    "role": "owner" if owner else "guest",
                    "guest_label": guest["label"] if guest else None,
                    "capabilities": {"observe": owner, "admin": owner, "model_admin": owner},
                    "changed_by": changed_by,
                    "muted": runtime.muted,
                    "proactive": gate.enabled,
                    "intensity": gate.intensity,
                    "observing": observing,
                    **({"window": selected} if owner else {}),
                    "scene": runtime.scene,
                    "busy": runtime.busy,
                    **services,
                    "metrics": runtime.metrics,
                    "summary": runtime.store.summary(runtime.scene),
                }
            )

        async def model_changed():
            nonlocal latest, playing
            latest = None
            playing = False
            if router.phase == "stopping":
                with contextlib.suppress(Exception):
                    await stop_connection()
            runtime.metrics.clear()
            runtime.model = router.bind(identity)
            # Browser disconnection must not fail or cancel a service-level switch.
            with contextlib.suppress(Exception):
                await status()

        async def voice_changed():
            nonlocal latest, playing
            latest, playing = None, False
            if voices.phase == "stopping":
                with contextlib.suppress(Exception):
                    await stop_connection()
            runtime.metrics.clear()
            with contextlib.suppress(Exception):
                await status()

        async def history():
            await send(
                {"type": "history", "items": runtime.store.sources(runtime.scene, 60)}
            )
            await send({"type": "memory-history", "items": runtime.store.memories(include_history=True),
                        "checkpoints": runtime.store.checkpoints(runtime.scene),
                        "maintenance_error": getattr(getattr(runtime, "memory_worker", None), "error", "")})
            await send({"type": "memories", "items": runtime.store.memories()})

        async def observe():
            nonlocal observing, latest, playing
            try:
                while observing:
                    snap = await capture.capture(selected, dialogue_region)
                    lines = [
                        line for line in snap["lines"] if line["confidence"] >= 0.55
                    ]
                    text = "\n".join(line["text"] for line in lines)
                    now = time.monotonic()
                    accepted = stable.feed(
                        text,
                        now,
                        min((line["confidence"] for line in lines), default=0),
                    )
                    if accepted:
                        if runtime.is_proactive:
                            await runtime.stop()
                            playing = False
                        runtime.store.add_source("observation", accepted, runtime.scene)
                        latest = (accepted, now)
                        await send({"type": "observation", "text": accepted})
                    # Newer stable dialogue replaces older pending commentary.
                    if latest and gate.allowed(
                        now,
                        latest[1],
                        not model_ready()
                        or runtime.busy
                        or playing
                        or now - last_activity < 5,
                    ):
                        await runtime.start(
                            "根据最新已观察对白简短陪聊一句，别复述整段。只评论已知内容。",
                            proactive=True,
                            expires_at=latest[1] + 12,
                        )
                        gate.spoke(now)
                        latest = None
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                observing = False
                await send({"type": "error", "message": f"观察已停止：{exc}"})
                await status()

        async def maintenance_loop():
            while True:
                await asyncio.sleep(10)
                if (
                    runtime.can_generate()
                    and not runtime.busy
                    and not playing
                    and time.monotonic() - last_activity > 15
                ):
                    runtime.maintenance = asyncio.create_task(runtime.consolidate())
                    try:
                        await runtime.maintenance
                    except asyncio.CancelledError:
                        if asyncio.current_task().cancelling():
                            raise
                    except Exception as exc:
                        await send({"type": "error", "message": f"记忆整理失败：{exc}"})

        async def stop_observing():
            nonlocal observing, observer_task, latest
            observing = False
            latest = None
            stable.reset()
            if observer_task:
                observer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await observer_task
                observer_task = None

        async def cancel_capture():
            nonlocal capture_task
            if capture_task:
                capture_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await capture_task
                capture_task = None

        async def stop_connection():
            nonlocal latest, playing
            latest, playing = None, False
            await cancel_capture()
            with contextlib.suppress(Exception):
                await runtime.stop()

        async def persona():
            await send(
                {
                    "type": "persona",
                    "text": runtime.persona,
                    "default": (root / "config/persona.md").read_text(),
                }
            )

        async def guest_list():
            await send(
                {
                    "type": "guests-list",
                    "items": [
                        {**item, "online": item["id"] in connections}
                        for item in app.state.sharing.registry.list()
                    ],
                }
            )

        async def receive():
            pending = asyncio.create_task(ws.receive_json())
            revoked = asyncio.create_task(connection["revoked"].wait())
            try:
                done, _ = await asyncio.wait(
                    [pending, revoked], return_when=asyncio.FIRST_COMPLETED
                )
                if revoked in done:
                    return None
                return pending.result()
            finally:
                with anyio.CancelScope(shield=True):
                    for task in (pending, revoked):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(pending, revoked, return_exceptions=True)

        async def launch_capture(operation):
            nonlocal capture_task
            await cancel_capture()

            async def run():
                try:
                    await operation()
                    await status()
                except (ValueError, RuntimeError, OSError) as exc:
                    await send({"type": "error", "message": str(exc)})

            capture_task = asyncio.create_task(run())

        maintenance_task = asyncio.create_task(maintenance_loop())
        connection.update(
            send=send,
            stop=stop_connection,
            model_changed=model_changed,
            voice_changed=voice_changed,
        )
        try:
            await status()
            await history()
            await share_status()
            while True:
                data = await receive()
                if data is None:
                    break
                if not isinstance(data, dict):
                    await send({"type": "error", "message": "请求格式无效"})
                    continue
                kind = data.get("type")
                try:
                    if not owner:
                        if kind not in GUEST_COMMANDS or (
                            kind == "settings" and set(data) - {"type", "muted"}
                        ):
                            raise ValueError("没有权限执行此操作")
                        app.state.sharing.registry.touch(identity)
                    if kind in {"text-input", "look"} and not router.allowed(identity):
                        raise RuntimeError("主人尚未授权访客使用当前云模型")
                    if kind in {"text-input", "look"} and not model_ready():
                        raise RuntimeError(
                            "模型或语音正在切换，或模型不可用，请等待就绪后发送"
                        )
                    if kind in {
                        "model-switch",
                        "voice-switch",
                        "thinking-mode",
                        "text-input",
                        "interrupt-signal",
                        "observe-stop",
                        "observe-start",
                        "daily",
                        "memory-delete",
                        "memory-edit",
                        "scene-clear",
                    } or (kind == "settings" and data.get("muted")):
                        await cancel_capture()
                    if kind in {"cloud-list", "cloud-save", "cloud-delete", "cloud-test", "cloud-usage"}:
                        if not owner:
                            raise ValueError("没有权限执行此操作")
                        if router.task and not router.task.done():
                            raise RuntimeError("模型正在切换，请稍候")
                        if kind == "cloud-list":
                            await send({"type": "cloud-connections", "items": router.connections.views()})
                        elif kind == "cloud-usage":
                            await send({"type": "cloud-usage", "items": router.usage.rows()})
                        elif kind == "cloud-save":
                            await stop_all()
                            await asyncio.to_thread(router.connections.save, data.get("connection", {}), data.get("api_key"))
                            await send({"type": "cloud-connections", "items": router.connections.views()})
                            await broadcast_model_change()
                        elif kind == "cloud-delete":
                            key = data.get("connection_id")
                            if key == router.cloud_id:
                                raise ValueError("先切换到其他模型，再删除当前连接")
                            await asyncio.to_thread(router.connections.delete, key)
                            await send({"type": "cloud-connections", "items": router.connections.views()})
                        else:
                            await router.test_connection(data.get("connection_id"))
                            await send({"type": "cloud-test-result", "message": "基本调用成功；人格、视觉及语音仍需实际体验"})
                    elif kind in {"model-switch", "thinking-mode"}:
                        if not owner:
                            raise ValueError("没有权限执行此操作")
                        if router.task and not router.task.done():
                            raise RuntimeError("模型正在切换，请稍候")
                        if kind == "model-switch" and str(data.get("model_key", "")).startswith("cloud:"):
                            router.request_switch(data["model_key"])
                            continue
                        if changing_thinking or (
                            manager and manager.task and not manager.task.done()
                        ):
                            raise RuntimeError("模型正在切换，请稍候")
                        if voices and voices.task and not voices.task.done():
                            raise RuntimeError("语音正在切换，请稍候")
                        if voices and voices.phase in BUSY_PHASES:
                            raise RuntimeError("语音正在切换，请稍候")
                        if not manager:
                            raise ValueError(
                                "当前连接外部模型服务，未启用托管，不能在界面切换"
                            )
                        if manager.task and not manager.task.done():
                            raise RuntimeError("模型正在切换，请稍候")
                        if kind == "model-switch" and data.get("model_key") not in {
                            "27b",
                            "qwen38-rp",
                        }:
                            raise ValueError("模型选择无效")
                        if kind == "thinking-mode" and (
                            manager.active_key != "qwen38-rp"
                            or router.cloud_id
                            or data.get("thinking_mode")
                            not in {"off", "limited", "full"}
                        ):
                            raise ValueError("当前模型不支持此思考档位")
                        changed_by = "主人" if owner else guest["label"]
                        playing, latest = False, None
                        runtime.metrics.clear()
                        last_activity = time.monotonic()
                        if kind == "model-switch":
                            router.request_switch(data["model_key"])
                        else:
                            changing_thinking = True
                            try:
                                await stop_all()
                                manager.set_thinking(data.get("thinking_mode"))
                            finally:
                                changing_thinking = False
                            await broadcast_model_change()
                    elif kind == "voice-switch":
                        if not voices:
                            raise ValueError("当前语音未启用托管，不能在界面切换")
                        if manager and manager.phase in BUSY_PHASES:
                            raise RuntimeError("模型正在切换，请稍候")
                        if changing_thinking or (
                            manager and manager.task and not manager.task.done()
                        ):
                            raise RuntimeError("模型正在切换，请稍候")
                        if voices.task and not voices.task.done():
                            raise RuntimeError("语音正在切换，请稍候")
                        changed_by = "主人" if owner else guest["label"]
                        voices.request_switch(data.get("voice_key"))
                        playing, latest = False, None
                        last_activity = time.monotonic()
                    elif kind == "text-input":
                        text = str(data.get("text", "")).strip()
                        if not text or len(text) > 4000:
                            raise ValueError("消息需为 1–4000 字")
                        last_activity = time.monotonic()
                        playing = False
                        latest = None
                        await runtime.start(text)
                    elif kind == "interrupt-signal":
                        last_activity = time.monotonic()
                        playing = False
                        latest = None
                        await runtime.stop()
                    elif kind == "stop-speech":
                        await runtime.stop_speech()
                        playing = False
                    elif kind == "audio-play-start":
                        if data.get("turn_id") == runtime.turn_id:
                            playing = True
                            runtime.played(runtime.turn_id)
                    elif kind == "frontend-playback-complete":
                        if data.get("turn_id") == runtime.turn_id:
                            playing = False
                            await status()
                    elif kind == "settings":
                        if "muted" in data:
                            runtime.muted = bool(data["muted"])
                            if runtime.muted:
                                await runtime.stop_speech()
                                playing = False
                        if "proactive" in data:
                            gate.enabled = bool(data["proactive"])
                            latest = None
                            if not gate.enabled and runtime.is_proactive:
                                await runtime.stop()
                                playing = False
                        if "intensity" in data:
                            if data["intensity"] not in {"quiet", "natural", "active"}:
                                raise ValueError("发言档位无效")
                            gate.intensity = data["intensity"]
                    elif kind == "windows":

                        async def list_windows():
                            await send(
                                {"type": "windows", "items": await capture.windows()}
                            )

                        await launch_capture(list_windows)
                    elif kind == "preview":
                        await stop_observing()
                        await runtime.stop()
                        playing = False
                        requested_window = int(data["window"])
                        selected = None
                        dialogue_region = game_region = None

                        async def preview_window(window=requested_window):
                            nonlocal selected
                            snap = await capture.capture(window)
                            selected = window
                            await send({"type": "preview", **snap})

                        await launch_capture(preview_window)
                    elif kind == "observe-start":
                        if selected is None:
                            raise ValueError("请先选择并预览窗口")
                        await stop_observing()
                        await runtime.stop()
                        playing = False
                        dialogue_region, game_region = (
                            region(data.get("dialogue_region")),
                            region(data.get("game_region")),
                        )
                        scene = str(data.get("scene", "")).strip()
                        if not scene or len(scene) > 80:
                            raise ValueError("请输入 1–80 字的游戏／存档名称")
                        runtime.scene = "game:" + scene
                        observing = True
                        observer_task = asyncio.create_task(observe())
                        await history()
                    elif kind == "observe-stop":
                        await stop_observing()
                        if runtime.is_proactive:
                            await runtime.stop()
                            playing = False
                    elif kind == "daily":
                        await stop_observing()
                        await runtime.stop()
                        playing = False
                        runtime.scene = "daily"
                        await history()
                    elif kind == "look":
                        if not observing:
                            raise ValueError("先开启所选窗口的观察")
                        if not model_state()["supports_vision"]:
                            raise ValueError(
                                "当前模型未启用图片理解；OCR观察仍可用，请切换到支持图片的模型后查看画面"
                            )
                        last_activity = time.monotonic()
                        await runtime.stop()
                        playing = False

                        async def look_at_window(window=selected, crop=game_region):
                            snap = await capture.capture(window, crop)
                            if not model_ready():
                                return
                            await runtime.start(
                                "看看当前画面，结合已读到的对白说一句感受。",
                                image=snap["image"],
                            )

                        await launch_capture(look_at_window)
                    elif kind in {"memory-delete", "memory-edit", "scene-clear", "memory-confirm", "memory-rebuild", "memory-retry"}:
                        await runtime.stop()
                        playing = False
                        if kind == "memory-delete":
                            runtime.store.forget(int(data["id"]), mode=data.get("mode", "exclude"))
                        elif kind == "memory-edit":
                            runtime.store.correct(int(data["id"]), str(data["text"]))
                        elif kind == "memory-confirm":
                            runtime.store.confirm(int(data["id"]))
                        elif kind == "memory-rebuild":
                            runtime.store.rebuild_summary(runtime.scene)
                            if hasattr(runtime, "memory_worker"):
                                runtime.memory_worker.retry()
                        elif kind == "memory-retry":
                            if hasattr(runtime, "memory_worker"):
                                runtime.memory_worker.retry()
                        else:
                            runtime.store.clear_scene(runtime.scene)
                        runtime.context_bundle = None
                        await send({"type": "memory-invalidated"})
                        await history()
                    elif kind == "memory-archive":
                        before = data.get("before")
                        if before is not None and (type(before) is not int or before < 1):
                            raise ValueError("历史分页参数无效")
                        archive = runtime.store.archive(before)
                        await send({"type": "memory-archive", **archive,
                                    "next_before": archive['sources'][0]['id'] if len(archive['sources']) == 60 else None})
                    elif kind == "memories":
                        await history()
                    elif kind == "persona-get":
                        await persona()
                    elif kind in {"persona-set", "persona-reset"}:
                        value = data.get("text") if kind == "persona-set" else None
                        if kind == "persona-set" and not isinstance(value, str):
                            raise ValueError("人格提示词需为 1–16000 字")
                        # Validate before interrupting a valid in-flight reply.
                        if value is not None and (
                            not value.strip() or len(value) > 16000
                        ):
                            raise ValueError("人格提示词需为 1–16000 字")
                        await stop_connection()
                        runtime.store.save_persona(value)
                        runtime.persona = runtime.store.persona(
                            (root / "config/persona.md").read_text()
                        )
                        runtime.mood, runtime.mood_changed = "neutral", 0.0
                        await persona()
                    elif kind == "sharing-status":
                        app.state.sharing.refresh_addresses()
                        await share_status()
                    elif kind in {
                        "sharing-enable",
                        "sharing-disable",
                        "sharing-password-set",
                    }:
                        sharing = app.state.sharing
                        if kind == "sharing-enable":
                            await sharing.enable(data.get("address"))
                        elif kind == "sharing-disable":
                            await sharing.disable()
                        else:
                            await sharing.change_password(data.get("password"))
                        await share_status()
                    elif kind == "guests-list":
                        await guest_list()
                    elif kind in {"guest-history", "guest-memories"}:
                        guest_id = data.get("guest_id")
                        store = MemoryStore(
                            app.state.sharing.registry.database(guest_id)
                        )
                        try:
                            before = data.get("before")
                            if before is not None and (
                                not isinstance(before, int) or before < 1
                            ):
                                raise ValueError("历史分页参数无效")
                            rows = (
                                store.history_page(before)
                                if kind == "guest-history"
                                else store.memories()
                            )
                            await send(
                                {
                                    "type": kind,
                                    "guest_id": guest_id,
                                    "items": rows,
                                    "next_before": rows[0]["id"]
                                    if kind == "guest-history" and len(rows) == 60
                                    else None,
                                }
                            )
                        finally:
                            store.close()
                    elif kind in {"guest-delete", "guests-clear"}:
                        sharing = app.state.sharing
                        ids = (
                            [data.get("guest_id")]
                            if kind == "guest-delete"
                            else [item["id"] for item in sharing.registry.list()]
                        )
                        for guest_id in ids:
                            await sharing.delete(guest_id)
                        await guest_list()
                        await share_status()
                    elif kind != "status":
                        raise ValueError("未知请求")
                    await status()
                except (ValueError, KeyError, RuntimeError, OSError) as exc:
                    await send({"type": "error", "message": str(exc)})
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            # TestClient and ASGI shutdown can cancel the handler during cleanup.
            with anyio.CancelScope(shield=True):
                try:
                    await cancel_capture()
                    await stop_observing()
                    maintenance_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await maintenance_task
                    with contextlib.suppress(Exception):
                        await runtime.stop()
                finally:
                    connections.pop(identity, None)
                    if owner:
                        app.state.connected = False
                    else:
                        runtime.store.close()
                    if connection["close"]:
                        code, reason = connection["close"]
                        with contextlib.suppress(Exception):
                            await ws.close(code=code, reason=reason)
                    connection["done"].set()
                    await share_status()

    def mount_assets(target):
        assets = root / "backend/live2d-models"
        assets.mkdir(exist_ok=True)
        target.mount("/live2d-models", StaticFiles(directory=assets), name="models")
        frontend = root / "desktop/out/renderer"
        if frontend.exists():
            target.mount(
                "/", StaticFiles(directory=frontend, html=True), name="desktop"
            )

    mount_assets(app)
    return app
