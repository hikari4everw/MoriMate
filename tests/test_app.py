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
