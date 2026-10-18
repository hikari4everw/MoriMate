import asyncio
import contextlib
import os
import socket
import signal
import sys
from pathlib import Path

import httpx
import pytest

from open_llm_vtuber.companion.voice_service import VoiceService
from test_voices import catalog

FIXTURE = Path(__file__).with_name("fixtures") / "voice_server.py"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def service(tmp_path, behavior=None):
    voices = catalog(tmp_path)
    for engine in voices.engines.values():
        engine["port"] = free_port()
    calls = []
    manager = None

    def launch(key):
        assert manager.process is None
        calls.append(key)
        return (
            [
                sys.executable,
                str(FIXTURE),
                str(voices.engine(key)["port"]),
                (behavior or {}).get(key, "ok"),
            ],
            tmp_path,
            os.environ.copy(),
        )

    manager = VoiceService(
        tmp_path, catalog=voices, launch_spec=launch, ready_timeout=2, stop_timeout=0.3
    )
    return manager, calls


async def test_switch_serializes_and_reaps_process_before_loading(tmp_path):
    manager, calls = service(tmp_path)
    try:
        await manager.start()
        assert manager.active_key == "roxy" and manager.ready
        old = manager.process
        manager.request_switch("mao")
        with pytest.raises(RuntimeError, match="切换"):
            manager.request_switch("roxy")
        await manager.task
        assert old.poll() is not None
        assert calls == ["roxy", "mao"]
        assert manager.snapshot()["voice_label"] == "AivisSpeech:まお"
        manager.request_switch("roxy")
        await manager.task
        assert manager.ready and manager.active_key == "roxy"
        current = manager.process
    finally:
        await manager.close()
    assert current.poll() is not None


@pytest.mark.parametrize("behavior", ["exit", "bad-audio", "hang"])
async def test_startup_needs_actual_wav_and_falls_back(tmp_path, behavior):
    manager, calls = service(tmp_path, {"roxy": behavior})
    try:
        await manager.start()
        state = manager.snapshot()
        assert manager.active_key == "mao" and manager.ready
        assert state["failed_voice_key"] == "roxy"
        assert state["voice_error"] and state["voice_recovery"]
        assert calls == ["roxy", "mao"]
        assert not list((tmp_path / ".runtime/audio").glob("*.wav"))
    finally:
        await manager.close()


async def test_both_fail_leaves_text_mode_and_can_retry(tmp_path):
    behavior = {"roxy": "exit", "mao": "exit"}
    manager, _ = service(tmp_path, behavior)
    try:
        await manager.start()
        assert manager.phase == "error" and manager.process is None
        assert manager.active_key is None and not manager.ready
        behavior.clear()
        manager.request_switch("roxy")
        await manager.task
        assert manager.ready
    finally:
        await manager.close()


async def test_sentence_error_does_not_switch_engine_or_retry(tmp_path):
    manager, calls = service(tmp_path)
    try:
        await manager.start()
        pid = manager.process.pid
        with pytest.raises(httpx.HTTPStatusError):
            await manager.synthesize("FAIL", "shy")
        assert manager.active_key == "roxy" and manager.process.pid == pid
        path = await manager.synthesize("はい。", "neutral")
        assert Path(path).is_file() and calls == ["roxy"]
        Path(path).unlink()
    finally:
        await manager.close()


async def test_foreign_listener_never_adopted_or_closed(tmp_path):
    manager, calls = service(tmp_path)
    with socket.socket() as foreign:
        port = manager.catalog.engine("roxy")["port"]
        foreign.bind(("127.0.0.1", port))
        foreign.listen()
        try:
            await manager.start()
            assert calls == ["mao"] and manager.active_key == "mao"
        finally:
            await manager.close()
        assert foreign.getsockname()[1] == port


async def test_shutdown_during_load_cancels_without_fallback(tmp_path):
    manager, calls = service(tmp_path, {"roxy": "hang"})
    task = asyncio.create_task(manager.start())
    async with asyncio.timeout(2):
        while manager.process is None:
            await asyncio.sleep(0.01)
    child = manager.process
    await manager.close()
    await asyncio.gather(task, return_exceptions=True)
    assert child.poll() is not None and manager.process is None
    assert calls == ["roxy"]


async def test_invalid_key_does_not_interrupt_working_engine(tmp_path):
    manager, _ = service(tmp_path)
    try:
        await manager.start()
        pid = manager.process.pid
        with pytest.raises(ValueError):
            manager.request_switch(["roxy"])
        assert manager.ready and manager.process.pid == pid
    finally:
        await manager.close()


async def test_unknown_startup_voice_recovers_without_stuck_starting(tmp_path):
    manager, calls = service(tmp_path)
    try:
        await manager.start("removed-role")
        assert manager.startup_task is None
        assert manager.ready and manager.active_key == "mao"
        assert manager.error and manager.recovery
        assert calls == ["mao"]
    finally:
        await manager.close()


async def test_process_crash_notifies_error_and_same_role_can_retry(tmp_path):
    manager, calls = service(tmp_path)
    notifications = []

    async def changed():
        notifications.append(manager.snapshot())

    manager.on_change = changed
    try:
        await manager.start()
        os.kill(manager.process.pid, signal.SIGTERM)
        async with asyncio.timeout(2):
            while manager.phase != "error":
                await asyncio.sleep(0.01)
        assert manager.failed_key == "roxy" and calls == ["roxy"]
        assert notifications[-1]["voice_phase"] == "error"
        assert notifications[-1]["voice_error"]
        manager.request_switch("roxy")
        await manager.task
        assert manager.ready and calls == ["roxy", "roxy"]
    finally:
        await manager.close()


async def test_parent_exit_still_reaps_owned_process_group(tmp_path):
    import subprocess

    manager, _ = service(tmp_path, {"roxy": "orphan"})
    child_pid = None
    try:
        await manager.start()
        async with httpx.AsyncClient(trust_env=False) as client:
            reply = await client.get(
                f"http://127.0.0.1:{manager.catalog.engine('roxy')['port']}/openapi.json"
            )
        child_pid = reply.json()["child_pid"]
        os.kill(manager.process.pid, signal.SIGTERM)
        await asyncio.to_thread(manager.process.wait)
        await manager.close()
        async with asyncio.timeout(2):
            while True:
                state = subprocess.run(
                    ["ps", "-o", "stat=", "-p", str(child_pid)],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if not state or state.startswith("Z"):
                    break
                await asyncio.sleep(0.01)
    finally:
        await manager.close()
        if child_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


async def test_second_configured_gpt_role_restarts_pair_and_keeps_one_process(tmp_path):
    import copy

    manager, calls = service(tmp_path)
    second = copy.deepcopy(manager.catalog.voices["roxy"])
    second.update(
        label="Second test role", gpt_weights="second.ckpt", sovits_weights="second.pth"
    )
    manager.catalog.voices["second"] = second
    try:
        await manager.start()
        old = manager.process
        manager.request_switch("second")
        await manager.task
        assert old.poll() is not None and manager.process.pid != old.pid
        assert manager.ready and manager.active_key == "second"
        assert calls == ["roxy", "second"]
    finally:
        await manager.close()
