import contextlib

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from test_app import make_client, until


@pytest.fixture
def lan(tmp_path, monkeypatch):
    from open_llm_vtuber.companion import guest_server

    monkeypatch.setattr(
        guest_server,
        "lan_addresses",
        lambda: [{"address": "192.168.1.20", "label": "测试网卡"}],
    )

    async def no_listener(self):
        pass

    monkeypatch.setattr(guest_server.LanSharing, "start_listener", no_listener)
    with make_client(tmp_path) as owner:
        sharing = owner.app.state.sharing
        guest = TestClient(sharing.app, base_url="http://192.168.1.20:12394")
        guest.portal = owner.portal
        with owner.websocket_connect("/client-ws") as ws:
            until(ws, "memories")
            until(ws, "sharing-status")
            ws.send_json({"type": "sharing-password-set", "password": "test-password"})
            until(ws, "sharing-status")
            ws.send_json({"type": "sharing-enable", "address": "192.168.1.20"})
            assert until(ws, "sharing-status")["enabled"]
            yield owner, ws, guest


def login(client, identity=None):
    if identity:
        client.cookies.set("companion_identity", identity)
    response = client.post(
        "/api/login",
        json={"password": "test-password"},
        headers={"origin": "http://192.168.1.20:12394"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def connect(client):
    return client.websocket_connect(
        "ws://192.168.1.20:12394/client-ws",
        headers={"origin": "http://192.168.1.20:12394"},
    )


def test_guest_login_requires_password_and_matching_origin(lan):
    _, _, guest = lan
    assert guest.get("/api/session").json()["authenticated"] is False
    with pytest.raises(WebSocketDisconnect):
        with connect(guest) as rejected:
            rejected.receive_json()
    assert (
        guest.post(
            "/api/login",
            json={"password": "test-password"},
            headers={"origin": "http://evil.test"},
        ).status_code
        == 403
    )
    assert (
        guest.post(
            "/api/login",
            json={"password": "wrong"},
            headers={"origin": "http://192.168.1.20:12394"},
        ).status_code
        == 401
    )
    assert login(guest)["role"] == "guest"
    assert guest.get("/api/session").json()["authenticated"] is True


def test_owner_guest_history_and_persona_are_isolated(lan):
    _, owner, guest = lan
    login(guest)
    with connect(guest) as ws:
        state = until(ws, "status")
        assert state["role"] == "guest"
        assert state["capabilities"]["observe"] is False
        until(ws, "memories")
        ws.send_json({"type": "settings", "muted": True})
        until(ws, "status")
        ws.send_json({"type": "text-input", "text": "访客私有记录"})
        until(ws, "audio")
        ws.send_json({"type": "persona-set", "text": "你是严肃的图书管理员。"})
        assert until(ws, "persona")["text"] == "你是严肃的图书管理员。"
        owner.send_json({"type": "memories"})
        assert until(owner, "history")["items"] == []
        owner.send_json({"type": "persona-get"})
        assert until(owner, "persona")["text"] != "你是严肃的图书管理员。"
    with connect(guest) as ws:
        assert until(ws, "history")["items"][0]["text"] == "访客私有记录"
        ws.send_json({"type": "persona-get"})
        assert until(ws, "persona")["text"] == "你是严肃的图书管理员。"


@pytest.mark.parametrize(
    "kind",
    [
        "windows",
        "preview",
        "observe-start",
        "observe-stop",
        "look",
        "daily",
        "sharing-enable",
        "guests-list",
        "guest-history",
        "guest-delete",
        "guests-clear",
    ],
)
def test_guest_cannot_invoke_screen_or_admin_commands(lan, kind):
    _, _, guest = lan
    login(guest)
    with connect(guest) as ws:
        until(ws, "memories")
        ws.send_json({"type": kind, "guest_id": "../../companion"})
        assert "权限" in until(ws, "error")["message"]


def test_password_change_revokes_session_but_identity_restores_history(lan):
    _, owner, guest = lan
    before = login(guest)["guest"]["id"]
    owner.send_json({"type": "sharing-password-set", "password": "changed-password"})
    until(owner, "sharing-status")
    assert guest.get("/api/session").json()["authenticated"] is False
    response = guest.post(
        "/api/login",
        json={"password": "changed-password"},
        headers={"origin": "http://192.168.1.20:12394"},
    )
    assert response.status_code == 200
    assert response.json()["guest"]["id"] == before


def test_owner_can_read_delete_and_reset_only_selected_guest(lan):
    _, owner, guest = lan
    identity = login(guest)["guest"]
    with contextlib.ExitStack() as stack:
        ws = stack.enter_context(connect(guest))
        until(ws, "memories")
        ws.send_json({"type": "settings", "muted": True})
        until(ws, "status")
        ws.send_json({"type": "text-input", "text": "需要主人查看的记录"})
        until(ws, "audio")
        owner.send_json({"type": "guest-history", "guest_id": identity["id"]})
        assert until(owner, "guest-history")["items"][0]["text"] == "需要主人查看的记录"
        owner.send_json({"type": "guest-delete", "guest_id": identity["id"]})
        assert until(owner, "guests-list")["items"] == []
        with pytest.raises(WebSocketDisconnect) as error:
            while True:
                ws.receive_json()
        assert error.value.code == 4401
    assert guest.get("/api/session").json()["authenticated"] is False
    assert login(guest)["guest"]["id"] != identity["id"]


def test_duplicate_guest_connection_is_rejected(lan):
    _, _, guest = lan
    login(guest)
    with connect(guest) as first:
        until(first, "memories")
        with pytest.raises(WebSocketDisconnect) as error:
            with connect(guest) as rejected:
                rejected.receive_json()
        assert error.value.code == 4409


def test_second_browser_cannot_access_first_browser_records(lan):
    owner_client, _, first = lan
    first_guest = login(first)["guest"]
    second = TestClient(
        owner_client.app.state.sharing.app, base_url="http://192.168.1.20:12394"
    )
    second.portal = owner_client.portal
    assert login(second)["guest"]["id"] != first_guest["id"]
    with connect(first) as a, connect(second) as b:
        until(a, "memories")
        until(b, "memories")
        a.send_json({"type": "settings", "muted": True})
        until(a, "status")
        a.send_json({"type": "text-input", "text": "甲的独有信息"})
        until(a, "audio")
        b.send_json({"type": "memories", "guest_id": first_guest["id"]})
        assert until(b, "history")["items"] == []


def test_guest_cannot_set_proactive_preferences(lan):
    _, _, guest = lan
    login(guest)
    with connect(guest) as ws:
        until(ws, "memories")
        ws.send_json({"type": "settings", "proactive": True, "muted": True})
        assert "权限" in until(ws, "error")["message"]


def test_guest_connections_are_rejected_during_revocation(lan):
    owner_client, _, guest = lan
    login(guest)
    sharing = owner_client.app.state.sharing
    owner_client.portal.call(sharing.lock.acquire)
    try:
        with pytest.raises(WebSocketDisconnect) as error:
            with connect(guest) as rejected:
                rejected.receive_json()
        assert error.value.code == 4401
    finally:
        owner_client.portal.call(sharing.lock.release)


def test_only_owner_switches_model_and_thinking_guest_can_switch_voice(tmp_path, monkeypatch):
    from open_llm_vtuber.companion import guest_server
    from open_llm_vtuber.companion.app import create_app
    from test_app import Capture
    from test_model_service import service as model_service
    from test_voice_service import service as voice_service

    make_client(tmp_path)
    manager, _, _ = model_service(tmp_path)
    voices, _ = voice_service(tmp_path)
    monkeypatch.setattr(
        guest_server,
        "lan_addresses",
        lambda: [{"address": "192.168.1.20", "label": "测试"}],
    )

    async def no_listener(self):
        pass

    monkeypatch.setattr(guest_server.LanSharing, "start_listener", no_listener)

    def ready(ws, field, value):
        for _ in range(40):
            message = ws.receive_json()
            if (
                message["type"] == "status"
                and message.get(field) == value
                and message.get("model_phase") == "ready"
                and message.get("voice_phase") == "ready"
            ):
                return message
        raise AssertionError((field, value))

    app = create_app(
        tmp_path, capture=Capture(), model_service=manager, voice_service=voices
    )
    with TestClient(app) as client, client.websocket_connect("/client-ws") as owner:
        ready(owner, "active_voice_key", "roxy")
        owner.send_json({"type": "sharing-password-set", "password": "test-password"})
        status = until(owner, "sharing-status")
        while not status["password_set"]:
            status = until(owner, "sharing-status")
        owner.send_json({"type": "sharing-enable", "address": "192.168.1.20"})
        assert until(owner, "sharing-status")["enabled"]
        guest = TestClient(app.state.sharing.app, base_url="http://192.168.1.20:12394")
        guest.portal = client.portal
        login(guest)
        with connect(guest) as ws:
            until(ws, "memories")
            ws.send_json({"type": "model-switch", "model_key": "qwen38-rp"})
            reply = ws.receive_json()
            assert reply["type"] == "error" and "权限" in reply["message"]
            ws.send_json({"type": "thinking-mode", "thinking_mode": "full"})
            reply = ws.receive_json()
            assert reply["type"] == "error" and "权限" in reply["message"]
            owner.send_json({"type": "model-switch", "model_key": "qwen38-rp"})
            ready(owner, "active_model_key", "qwen38-rp")
            ready(ws, "active_model_key", "qwen38-rp")
            owner.send_json({"type": "thinking-mode", "thinking_mode": "full"})
            ready(owner, "thinking_mode", "full")
            ready(ws, "thinking_mode", "full")
            ws.send_json({"type": "voice-switch", "voice_key": "mao"})
            assert ready(ws, "active_voice_key", "mao")["voice_label"]
            ready(owner, "active_voice_key", "mao")
        # Disconnecting the guest must not remove application-level callbacks.
        owner.send_json({"type": "thinking-mode", "thinking_mode": "off"})
        ready(owner, "thinking_mode", "off")


def test_sharing_status_refreshes_addresses_after_network_changes(lan, monkeypatch):
    owner_client, owner, _ = lan
    from open_llm_vtuber.companion import guest_server

    owner.send_json({"type": "sharing-disable"})
    assert until(owner, "sharing-status")["enabled"] is False
    monkeypatch.setattr(
        guest_server,
        "lan_addresses",
        lambda: [{"address": "192.168.2.30", "label": "新网卡"}],
    )
    owner.send_json({"type": "sharing-status"})
    snapshot = until(owner, "sharing-status")
    assert snapshot["addresses"] == [{"address": "192.168.2.30", "label": "新网卡"}]
    assert snapshot["address"] == "192.168.2.30"
