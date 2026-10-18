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
from .memory import MemoryStore
from .model import LocalModel
from .model_service import ModelService
from .observation import StableText, CommentGate
from .runtime import CompanionRuntime
from .voice_service import BUSY_PHASES, VoiceService
from ..tts.voicevox_tts import TTSEngine

ROOT = Path(__file__).resolve().parents[4]


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

    @asynccontextmanager
    async def lifespan(app):
        app.state.connected = False
        try:
            if manager:
                await manager.start(os.environ.get("COMPANION_MODEL_KEY", "27b"))
            app.state.store = MemoryStore(
                database_path or root / ".runtime/companion.sqlite3"
            )
            if voices:
                app.state.voice_startup = asyncio.create_task(
                    voices.start(os.environ.get("COMPANION_VOICE") or None)
                )
            yield
        finally:
            if voices:
                await voices.close()
            if manager:
                await manager.close()
            if hasattr(app.state, "store"):
                app.state.store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.model_service = manager
    app.state.voice_service = voices
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
            and (manager is None or manager.ready)
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

    @app.websocket("/client-ws")
    async def websocket(ws: WebSocket):
        nonlocal changing_thinking
        if (
            not allowed_origin(ws.headers.get("origin"), ws.url.port)
            or app.state.connected
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        app.state.connected = True
        lock = asyncio.Lock()

        async def send(payload):
            async with lock:
                await ws.send_json(payload)

        runtime = CompanionRuntime(
            app.state.store,
            manager.transport if manager and manager.transport else model,
            voice,
            send,
            (root / "config/persona.md").read_text(),
        )
        runtime.can_generate = model_ready
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
            await send(
                {
                    "type": "status",
                    "muted": runtime.muted,
                    "proactive": gate.enabled,
                    "intensity": gate.intensity,
                    "observing": observing,
                    "window": selected,
                    "scene": runtime.scene,
                    "busy": runtime.busy,
                    **model_state(),
                    **voice_state(),
                    "metrics": runtime.metrics,
                    "summary": runtime.store.summary(runtime.scene),
                }
            )

        async def model_changed():
            nonlocal latest, playing
            latest = None
            playing = False
            if manager.phase == "stopping":
                with contextlib.suppress(Exception):
                    await runtime.stop()
            runtime.metrics.clear()
            if manager.ready:
                runtime.model = manager.transport
            # Browser disconnection must not fail or cancel a service-level switch.
            with contextlib.suppress(Exception):
                await status()

        if manager:
            manager.on_change = model_changed

        async def voice_changed():
            nonlocal latest, playing
            latest, playing = None, False
            if voices.phase == "stopping":
                with contextlib.suppress(Exception):
                    await runtime.stop()
            runtime.metrics.clear()
            with contextlib.suppress(Exception):
                await status()

        if voices:
            voices.on_change = voice_changed

        async def history():
            await send(
                {"type": "history", "items": runtime.store.sources(runtime.scene, 60)}
            )
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
                    model_ready()
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
        try:
            await status()
            await history()
            while True:
                data = await ws.receive_json()
                kind = data.get("type")
                try:
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
                    if kind in {"model-switch", "thinking-mode"}:
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
                        playing, latest = False, None
                        runtime.metrics.clear()
                        last_activity = time.monotonic()
                        if kind == "model-switch":
                            manager.request_switch(data["model_key"])
                        else:
                            changing_thinking = True
                            try:
                                await runtime.stop()
                            finally:
                                changing_thinking = False
                            manager.set_thinking(data.get("thinking_mode"))
                    elif kind == "voice-switch":
                        if not voices:
                            raise ValueError("当前语音未启用托管，不能在界面切换")
                        if manager and manager.phase in BUSY_PHASES:
                            raise RuntimeError("模型正在切换，请稍候")
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
                    elif kind in {"memory-delete", "memory-edit", "scene-clear"}:
                        await runtime.stop()
                        playing = False
                        if kind == "memory-delete":
                            runtime.store.forget(int(data["id"]))
                        elif kind == "memory-edit":
                            runtime.store.correct(int(data["id"]), str(data["text"]))
                        else:
                            runtime.store.clear_scene(runtime.scene)
                        await history()
                    elif kind == "memories":
                        await history()
                    elif kind != "status":
                        raise ValueError("未知请求")
                    await status()
                except (ValueError, KeyError, RuntimeError, OSError) as exc:
                    await send({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
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
                    if manager and manager.on_change is model_changed:
                        manager.on_change = None
                    if voices and voices.on_change is voice_changed:
                        voices.on_change = None
                    app.state.connected = False

    assets = root / "backend/live2d-models"
    assets.mkdir(exist_ok=True)
    app.mount("/live2d-models", StaticFiles(directory=assets), name="models")
    frontend = root / "desktop/out/renderer"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="desktop")
    return app
