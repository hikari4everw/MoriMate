import asyncio

import pytest

from open_llm_vtuber.companion.memory import MemoryStore


@pytest.mark.parametrize("password, accepted", [("abc", False), ("四个汉字", True), ("abcd", True), ("x" * 128, True), ("x" * 129, False)])
def test_local_test_password_bounds(tmp_path, password, accepted):
    from open_llm_vtuber.companion.sharing import GuestRegistry
    registry = GuestRegistry(tmp_path)
    try:
        if accepted:
            registry.set_password(password)
            assert registry.check_password(password)
        else:
            with pytest.raises(ValueError, match="4–128"):
                registry.set_password(password)
    finally:
        registry.close()


def test_guest_credentials_restore_only_their_own_store(tmp_path):
    from open_llm_vtuber.companion.sharing import GuestRegistry

    registry = GuestRegistry(tmp_path)
    registry.set_password("test-password")
    assert registry.check_password("wrong") is False
    assert registry.check_password("test-password") is True
    a, token_a = registry.identify(None)
    b, _ = registry.identify(None)
    store_a = MemoryStore(registry.database(a["id"]))
    store_b = MemoryStore(registry.database(b["id"]))
    store_a.add_source("user", "甲的秘密")
    assert store_b.sources() == []
    assert registry.identify(token_a)[0]["id"] == a["id"]
    assert (
        token_a
        not in registry.db.execute("SELECT identity_hash FROM guests").fetchone()[0]
    )
    store_a.close()
    store_b.close()
    registry.close()
    restored = GuestRegistry(tmp_path)
    assert restored.check_password("test-password")
    assert restored.identify(token_a)[0]["id"] == a["id"]
    restored.delete(a["id"])
    assert restored.identify(token_a)[0]["id"] != a["id"]
    assert len(restored.list()) == 2
    restored.close()


def test_persona_override_is_persistent_and_reset_does_not_clear_history(tmp_path):
    path = tmp_path / "owner.sqlite3"
    store = MemoryStore(path)
    store.add_source("user", "保留我的记录")
    store.save_persona("你是严肃的图书管理员。")
    store.close()
    store = MemoryStore(path)
    assert store.persona("默认人格") == "你是严肃的图书管理员。"
    for invalid in ["", " ", "字" * 16001, 123]:
        with pytest.raises(ValueError):
            store.save_persona(invalid)
    store.save_persona(None)
    assert store.persona("默认人格") == "默认人格"
    assert store.sources()[0]["text"] == "保留我的记录"
    store.close()


@pytest.mark.asyncio
async def test_queue_finishes_active_reply_then_owner_before_guests():
    from open_llm_vtuber.companion.scheduling import GenerationScheduler

    scheduler = GenerationScheduler()
    order = []
    started, release = asyncio.Event(), asyncio.Event()

    async def run(name, priority, hold=False):
        async with scheduler.slot(priority):
            order.append(name)
            if hold:
                started.set()
                await release.wait()

    active = asyncio.create_task(run("active", 1, True))
    await started.wait()
    guest_a = asyncio.create_task(run("guest-a", 1))
    guest_b = asyncio.create_task(run("guest-b", 1))
    owner = asyncio.create_task(run("owner", 0))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(active, guest_a, guest_b, owner)
    assert order == ["active", "owner", "guest-a", "guest-b"]


@pytest.mark.asyncio
async def test_chat_preempts_maintenance_and_cancelled_waiter_releases_queue():
    from open_llm_vtuber.companion.scheduling import GenerationScheduler

    scheduler = GenerationScheduler()
    started = asyncio.Event()

    async def maintenance():
        async with scheduler.slot(2):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(maintenance())
    await started.wait()
    async with scheduler.slot(0):
        assert task.cancelled()
    async with scheduler.slot(0):
        waiter = asyncio.create_task(maintenance())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    async with asyncio.timeout(1):
        async with scheduler.slot(1):
            pass


@pytest.mark.asyncio
async def test_runtime_holds_queue_through_tts_and_replaces_own_waiting_turn(tmp_path):
    from open_llm_vtuber.companion.runtime import CompanionRuntime
    from open_llm_vtuber.companion.scheduling import GenerationScheduler

    voice_started, voice_release = asyncio.Event(), asyncio.Event()
    requests, sent = [], []

    class Model:
        async def stream(self, messages, **kwargs):
            requests.append(messages[-1]["content"])
            yield '{"zh":"回复。","ja":"返事。"}\n'

    class Voice:
        async def async_generate_audio(self, text):
            voice_started.set()
            await voice_release.wait()
            return None

    async def send(message):
        sent.append(message)

    scheduler = GenerationScheduler()
    runtimes = [
        CompanionRuntime(
            MemoryStore(tmp_path / f"{name}.sqlite3"), Model(), Voice(), send, "persona"
        )
        for name in ["a", "b"]
    ]
    for runtime in runtimes:
        runtime.scheduler, runtime.priority = scheduler, 1
    a, b = runtimes
    await a.start("正在播报")
    await voice_started.wait()
    await b.start("应该被替换")
    await asyncio.sleep(0)
    await b.start("最终提问")
    await asyncio.sleep(0)
    assert len(requests) == 1
    voice_release.set()
    await asyncio.gather(a.task, b.task)
    assert len(requests) == 2
    assert "最终提问" in requests[1]
    assert any(
        item["type"] == "queue-status" and item["state"] == "waiting" for item in sent
    )
    for runtime in runtimes:
        runtime.store.close()


@pytest.mark.asyncio
async def test_waiting_turn_cannot_generate_after_shared_model_becomes_unavailable(
    tmp_path,
):
    from open_llm_vtuber.companion.runtime import CompanionRuntime
    from open_llm_vtuber.companion.scheduling import GenerationScheduler

    requests = []

    class Model:
        async def stream(self, *args, **kwargs):
            requests.append(True)
            yield '{"zh":"不应出现。","ja":"不可。"}\n'

    async def send(message):
        pass

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.sqlite3"), Model(), object(), send, "persona"
    )
    runtime.muted = True
    runtime.scheduler = GenerationScheduler()
    async with runtime.scheduler.slot(0):
        await runtime.start("排队中的提问")
        await asyncio.sleep(0)
        runtime.can_generate = lambda: False
    await runtime.task
    assert requests == []
    assert runtime.busy is False
    runtime.store.close()


@pytest.mark.asyncio
async def test_real_guest_listener_releases_port_and_never_takes_occupied_port(
    tmp_path, monkeypatch
):
    import socket
    import httpx
    from open_llm_vtuber.companion import guest_server

    monkeypatch.setattr(
        guest_server,
        "lan_addresses",
        lambda: [{"address": "127.0.0.1", "label": "fixture"}],
    )

    async def handler(*args):
        pass

    async def revoke(*args):
        pass

    sharing = guest_server.LanSharing(tmp_path, handler, revoke)
    sharing.registry.set_password("test-password")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        sharing.port = reservation.getsockname()[1]
        with pytest.raises(OSError):
            await sharing.enable("127.0.0.1")
        assert sharing.enabled is False
        assert reservation.getsockname()[1] == sharing.port
    try:
        await sharing.enable("127.0.0.1")
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"{sharing.origin}/api/session")
            assert response.json() == {
                "role": "guest",
                "authenticated": False,
                "guest": None,
            }
            denied = await client.get(
                f"{sharing.origin}/api/session", headers={"host": "evil.test"}
            )
            assert denied.status_code == 403
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosedError

        with pytest.raises(ConnectionClosedError) as rejected:
            async with connect(
                sharing.origin.replace("http", "ws") + "/client-ws",
                origin=sharing.origin,
            ) as ws:
                await ws.recv()
        assert rejected.value.rcvd.code == 4401
        await sharing.disable()
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(("127.0.0.1", sharing.port))
    finally:
        await sharing.close()
