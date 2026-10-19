import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from open_llm_vtuber.companion.app import create_app, allowed_origin, region


class Model:
    async def stream(self, *args, **kwargs):
        yield '{"zh":"测试成功。","ja":"テスト成功。"}\n'


class Capture:
    calls = []

    async def windows(self):
        return [{"id": 123, "app": "test", "title": "fixture"}]

    async def capture(self, window, region=None):
        self.calls.append((window, region))
        return {"image": "data:image/jpeg;base64,AA", "lines": []}


def make_client(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "backend").mkdir()
    for name in ["companion.json", "persona.md"]:
        shutil.copy(Path("config") / name, tmp_path / "config" / name)
    return TestClient(
        create_app(tmp_path, model=Model(), voice=object(), capture=Capture())
    )


def until(ws, kind):
    for _ in range(12):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(kind)


def test_sharing_four_character_password_acknowledges_and_survives_reconnect(tmp_path):
    with make_client(tmp_path) as client:
        with client.websocket_connect("/client-ws") as ws:
            until(ws, "sharing-status")
            ws.send_json({"type": "sharing-password-set", "password": "abcd"})
            assert until(ws, "sharing-status")["password_set"] is True
            result = ws.receive_json()
            assert result["type"] == "sharing-result"
            assert result["operation"] == "sharing-password-set"
            ws.send_json({"type": "sharing-password-set", "password": "abc"})
            assert "4–128" in until(ws, "error")["message"]
        with client.websocket_connect("/client-ws") as ws:
            state = until(ws, "sharing-status")
            assert state["password_set"] and not state["enabled"]


def test_voice_switch_survives_reconnect_and_preserves_text_mode(tmp_path):
    import time
    from test_voice_service import service

    make_client(tmp_path)
    voices, _ = service(tmp_path)
    client = TestClient(
        create_app(tmp_path, model=Model(), capture=Capture(), voice_service=voices)
    )
    with client:
        with client.websocket_connect("/client-ws") as ws:
            state = until(ws, "status")
            while state["voice_phase"] != "ready":
                state = until(ws, "status")
            assert state["active_voice_key"] == "roxy"
            assert state["voice_tone_mode"] == "fixed"
            ws.send_json({"type": "voice-tone-policy", "mode": "fixed", "tone": "relieved"})
            assert until(ws, "status")["fixed_voice_tone"] == "relieved"
            ws.send_json({"type": "settings", "muted": True})
            until(ws, "status")
            ws.send_json({"type": "text-input", "text": "保留记录"})
            until(ws, "audio")
            ws.send_json({"type": "voice-switch", "voice_key": "mao"})
            state = until(ws, "status")
            assert state["voice_phase"] == "stopping"
            ws.send_json({"type": "text-input", "text": "不该写入"})
            assert "切换" in until(ws, "error")["message"]
        deadline = time.monotonic() + 4
        while not voices.ready and time.monotonic() < deadline:
            time.sleep(0.02)
        with client.websocket_connect("/client-ws") as ws:
            state = until(ws, "status")
            assert state["active_voice_key"] == "mao"
            assert state["fixed_voice_tone"] == "relieved"
            rows = until(ws, "history")["items"]
            assert rows[0]["text"] == "保留记录"
            assert "不该写入" not in str(rows)


def test_injected_voice_never_starts_managed_process_and_rejects_switch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_MANAGED_VOICES", "1")
    with make_client(tmp_path) as client, client.websocket_connect("/client-ws") as ws:
        assert until(ws, "status")["managed_voices"] is False
        ws.send_json({"type": "voice-switch", "voice_key": "roxy"})
        assert "托管" in until(ws, "error")["message"]


def test_observation_requires_selection_and_defaults_off(tmp_path):
    with make_client(tmp_path) as client, client.websocket_connect("/client-ws") as ws:
        assert not until(ws, "status")["observing"]
        ws.send_json({"type": "observe-start", "scene": "test"})
        assert "预览" in until(ws, "error")["message"]


def test_muted_websocket_chat_persists_on_reconnect(tmp_path):
    with make_client(tmp_path) as client:
        with client.websocket_connect("/client-ws") as ws:
            until(ws, "memories")
            ws.send_json({"type": "settings", "muted": True})
            assert until(ws, "status")["muted"]
            ws.send_json({"type": "text-input", "text": "你好"})
            reply = until(ws, "audio")
            assert reply["audio"] is None
            assert reply["display_text"]["text"] == "测试成功。"
        with client.websocket_connect("/client-ws") as ws:
            assert [row["kind"] for row in until(ws, "history")["items"]] == [
                "user",
                "assistant",
            ]


def test_origin_and_region_reject_untrusted_inputs():
    assert not allowed_origin("https://example.com")
    assert not allowed_origin("http://127.0.0.1.evil:12393")
    assert allowed_origin("http://127.0.0.1:12393")
    for value in [[0, 0, 0, 1], [0.9, 0, 0.2, 1], ["1", 0, 1, 1]]:
        with pytest.raises(ValueError):
            region(value)


def test_opaque_browser_origin_cannot_read_local_data(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    with make_client(tmp_path) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/client-ws", headers={"origin": "null"}):
                pass


def test_slow_preview_does_not_block_stop(tmp_path):
    import asyncio
    import threading

    entered, cancelled = threading.Event(), threading.Event()

    class SlowCapture(Capture):
        async def capture(self, *args):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    client = make_client(tmp_path)
    # Re-create with a blocking capture, keeping the same test configuration.
    client = TestClient(
        create_app(tmp_path, model=Model(), voice=object(), capture=SlowCapture())
    )
    with client, client.websocket_connect("/client-ws") as ws:
        until(ws, "memories")
        ws.send_json({"type": "preview", "window": 123})
        assert entered.wait(1)
        ws.send_json({"type": "observe-stop"})
        assert cancelled.wait(1), "stop must cancel preview without waiting for capture"


def test_image_capability_is_configuration_not_model_name(tmp_path):
    import json

    make_client(tmp_path)
    path = tmp_path / "config/companion.json"
    config = json.loads(path.read_text())
    config["supports_vision"] = False
    path.write_text(json.dumps(config))
    client = TestClient(
        create_app(tmp_path, model=Model(), voice=object(), capture=Capture())
    )
    with client, client.websocket_connect("/client-ws") as ws:
        until(ws, "memories")
        ws.send_json({"type": "preview", "window": 123})
        until(ws, "preview")
        ws.send_json({"type": "observe-start", "scene": "fixture"})
        until(ws, "status")
        ws.send_json({"type": "look"})
        assert "图片理解" in until(ws, "error")["message"]


def test_external_model_cannot_be_switched_even_with_managed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_MANAGED_MODELS", "1")
    with make_client(tmp_path) as client, client.websocket_connect("/client-ws") as ws:
        state = until(ws, "status")
        assert state["managed_models"] is False
        ws.send_json({"type": "model-switch", "model_key": "qwen38-rp"})
        assert "托管" in until(ws, "error")["message"]


def test_managed_switch_survives_disconnect_preserves_scene_and_history(tmp_path):
    from test_model_service import service

    make_client(tmp_path)
    manager, _, _ = service(tmp_path)
    client = TestClient(
        create_app(tmp_path, voice=object(), capture=Capture(), model_service=manager)
    )
    with client:
        with client.websocket_connect("/client-ws") as ws:
            state = until(ws, "status")
            assert state["active_model_key"] == "27b"
            until(ws, "memories")
            ws.send_json({"type": "settings", "muted": True})
            until(ws, "status")
            ws.send_json({"type": "preview", "window": 123})
            until(ws, "preview")
            until(ws, "status")
            ws.send_json(
                {
                    "type": "observe-start",
                    "scene": "fixture",
                    "dialogue_region": [0, 0, 0.5, 0.5],
                }
            )
            assert until(ws, "status")["observing"]
            ws.send_json({"type": "model-switch", "model_key": "qwen38-rp"})
            state = until(ws, "status")
            assert state["model_phase"] == "stopping"
            assert state["observing"] and state["scene"] == "game:fixture"
            ws.send_json({"type": "text-input", "text": "不应写入记录"})
            assert "切换" in until(ws, "error")["message"]
        # Disconnect must not cancel the service-level task.
        import time

        deadline = time.monotonic() + 3
        while not manager.ready and time.monotonic() < deadline:
            time.sleep(0.02)
        with client.websocket_connect("/client-ws") as ws:
            state = until(ws, "status")
            assert state["active_model_key"] == "qwen38-rp"
            assert state["model_phase"] == "ready"
            assert until(ws, "history")["items"] == []
            pid = manager.process.pid
            ws.send_json({"type": "thinking-mode", "thinking_mode": "full"})
            state = until(ws, "status")
            assert state["thinking_mode"] == "full"
            assert state["metrics"] == []
            assert manager.process.pid == pid
    assert manager.process is None


def test_disconnected_interrupt_notification_cannot_abort_accepted_switch(
    tmp_path, monkeypatch
):
    import time
    from starlette.websockets import WebSocketDisconnect
    import open_llm_vtuber.companion.app as app_module
    from open_llm_vtuber.companion.runtime import CompanionRuntime
    from test_model_service import service

    make_client(tmp_path)
    manager, _, _ = service(tmp_path)

    class DisconnectedNotification(CompanionRuntime):
        def __init__(self, store, model, voice, send, persona):
            async def transport(payload):
                if (
                    payload["type"] == "interrupt-signal"
                    and manager.phase == "stopping"
                ):
                    raise WebSocketDisconnect(code=1006)
                await send(payload)

            super().__init__(store, model, voice, transport, persona)

    monkeypatch.setattr(app_module, "CompanionRuntime", DisconnectedNotification)
    client = TestClient(
        create_app(tmp_path, voice=object(), capture=Capture(), model_service=manager)
    )
    with client, client.websocket_connect("/client-ws") as ws:
        until(ws, "memories")
        ws.send_json({"type": "model-switch", "model_key": "qwen38-rp"})
        until(ws, "status")
        deadline = time.monotonic() + 3
        while manager.task and not manager.task.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert manager.active_key == "qwen38-rp"
        assert manager.error == ""


def test_isolated_backend_origin_must_match_actual_loopback_port():
    assert allowed_origin("http://127.0.0.1:12394", server_port=12394)
    assert not allowed_origin("http://127.0.0.1:12394", server_port=12393)
    assert not allowed_origin("https://example.com:12394", server_port=12394)
