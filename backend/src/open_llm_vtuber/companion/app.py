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
from .observation import StableText, CommentGate
from .runtime import CompanionRuntime
from ..tts.voicevox_tts import TTSEngine

ROOT = Path(__file__).resolve().parents[4]


def allowed_origin(origin):
    if origin in (None, "null", "file://"):
        return True  # Electron file renderer / native client.
    parsed = urlparse(origin)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.port in {12393, 5173}
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


def create_app(root=ROOT, model=None, voice=None, capture=None):
    config = json.loads((root / "config/companion.json").read_text())
    config["model"] = os.environ.get("COMPANION_MODEL", config["model"])
    config["voice_label"] = "VOICEVOX:小夜/SAYO"
    if os.environ.get("COMPANION_VOICE") == "mao":
        config.update(
            tts_url="http://127.0.0.1:10101",
            speaker_name="まお",
            style_name="ノーマル",
            voice_label="AivisSpeech:まお",
        )

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = MemoryStore(root / ".runtime/companion.sqlite3")
        app.state.connected = False
        yield
        app.state.store.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "null",
            "file://",
            "http://127.0.0.1:12393",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET"],
    )
    model = model or LocalModel(config["model_url"], config["model"])
    voice = voice or TTSEngine(
        base_url=config["tts_url"],
        speaker_name=config["speaker_name"],
        style_name=config["style_name"],
        cache_dir=str(root / ".runtime/audio"),
    )
    capture = capture or WindowCapture(root / ".runtime/window-observer")

    @app.get("/health")
    async def health():
        return {"status": "ok", "local_only": True, "model": config["model"]}

    @app.websocket("/client-ws")
    async def websocket(ws: WebSocket):
        if not allowed_origin(ws.headers.get("origin")) or app.state.connected:
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
            model,
            voice,
            send,
            (root / "config/persona.md").read_text(),
        )
        gate, stable = CommentGate(), StableText()
        selected = None
        dialogue_region = None
        game_region = None
        observing = False
        playing = False
        last_activity = time.monotonic()
        latest = None
        observer_task = None

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
                    "model": config["model"],
                    "voice_label": config["voice_label"],
                    "metrics": runtime.metrics,
                    "summary": runtime.store.summary(runtime.scene),
                }
            )

        async def history():
            await send(
                {"type": "history", "items": runtime.store.sources(runtime.scene, 60)}
            )
            await send({"type": "memories", "items": runtime.store.memories()})

        async def observe():
            nonlocal observing, latest
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
                        runtime.store.add_source("observation", accepted, runtime.scene)
                        latest = (accepted, now)
                        await send({"type": "observation", "text": accepted})
                    # Newer stable dialogue replaces older pending commentary.
                    if latest and gate.allowed(
                        now,
                        latest[1],
                        runtime.busy or playing or now - last_activity < 5,
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
                    not runtime.busy
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

        maintenance_task = asyncio.create_task(maintenance_loop())
        try:
            await status()
            await history()
            while True:
                data = await ws.receive_json()
                kind = data.get("type")
                try:
                    if kind == "text-input":
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
                                await runtime.stop()
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
                        await send(
                            {"type": "windows", "items": await capture.windows()}
                        )
                    elif kind == "preview":
                        await stop_observing()
                        selected = int(data["window"])
                        dialogue_region = game_region = None
                        await send(
                            {"type": "preview", **await capture.capture(selected)}
                        )
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
                        if "Qwen3-8B" in config["model"]:
                            raise ValueError(
                                "Qwen3-8B 是纯文字对照模型，请切换到 Qwen3.5 后查看画面"
                            )
                        last_activity = time.monotonic()
                        await runtime.stop()
                        playing = False
                        snap = await capture.capture(selected, game_region)
                        await runtime.start(
                            "看看当前画面，结合已读到的对白说一句感受。",
                            image=snap["image"],
                        )
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
                    await stop_observing()
                    maintenance_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await maintenance_task
                    with contextlib.suppress(Exception):
                        await runtime.stop()
                finally:
                    app.state.connected = False

    assets = root / "backend/live2d-models"
    assets.mkdir(exist_ok=True)
    app.mount("/live2d-models", StaticFiles(directory=assets), name="models")
    frontend = root / "desktop/out/renderer"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="desktop")
    return app
