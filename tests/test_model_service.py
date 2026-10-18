import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from open_llm_vtuber.companion.model_service import ModelService

FIXTURE = Path(__file__).parent / "fixtures/model_server.py"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def service(tmp_path, behavior=None):
    port = free_port()
    calls = []

    def launch(root, key):
        if behavior:
            mode = behavior(key, calls)
        else:
            mode = "ok"
        calls.append(key)
        if mode == "missing":
            raise RuntimeError("资源缺失")
        command = [
            sys.executable,
            str(FIXTURE.resolve()),
            str(port),
            key,
            str(tmp_path / "journal"),
            mode,
        ]
        return command, key, "mlx" if key == "27b" else "llama.cpp", True

    return (
        ModelService(
            tmp_path, launch_spec=launch, port=port, ready_timeout=0.6, stop_timeout=0.2
        ),
        calls,
        port,
    )


@pytest.mark.asyncio
async def test_switch_reaps_old_before_start_and_cleans_owned_process(tmp_path):
    manager, calls, _ = service(tmp_path)
    try:
        await manager.start("27b")
        old = manager.process.pid
        manager.request_switch("qwen38-rp")
        with pytest.raises(RuntimeError, match="切换"):
            manager.request_switch("27b")
        await manager.task
        assert not alive(old)
        assert manager.snapshot()["active_model_key"] == "qwen38-rp"
        assert manager.snapshot()["thinking_mode"] == "limited"
        assert calls == ["27b", "qwen38-rp"]
        assert all(
            not row["live_prior"]
            for row in map(json.loads, (tmp_path / "journal").read_text().splitlines())
        )
        candidate = manager.process.pid
        manager.set_thinking("full")
        assert manager.process.pid == candidate
        manager.request_switch("27b")
        await manager.task
        assert not alive(candidate)
        assert manager.snapshot()["thinking_mode"] == "off"
        assert manager.snapshot()["model_phase"] == "ready"
    finally:
        pid = manager.process.pid if manager.process else None
        await manager.close()
        assert not pid or not alive(pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exit", "hang", "missing", "wrong-identity"])
async def test_failed_switch_restores_previous_model_and_thinking(tmp_path, failure):
    manager, _, _ = service(
        tmp_path, lambda key, calls: failure if key == "27b" and calls else "ok"
    )
    if failure == "wrong-identity":
        original = manager.launch_spec

        def wrong(root, key):
            command, identity, backend, vision = original(root, key)
            return (
                command,
                identity + "-wrong" if key == "27b" else identity,
                backend,
                vision,
            )

        manager.launch_spec = wrong
    try:
        await manager.start("qwen38-rp")
        manager.set_thinking("full")
        manager.request_switch("27b")
        await manager.task
        state = manager.snapshot()
        assert state["active_model_key"] == "qwen38-rp"
        assert state["thinking_mode"] == "full"
        assert state["model_phase"] == "ready"
        assert state["model_error"]
        assert state["failed_model_key"] == "27b"
        assert "恢复" in state["model_recovery"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_recovery_failure_stays_available_for_retry(tmp_path):
    manager, _, _ = service(tmp_path, lambda key, calls: "exit" if calls else "ok")
    try:
        await manager.start("27b")
        manager.request_switch("qwen38-rp")
        await manager.task
        assert manager.snapshot()["model_phase"] == "error"
        assert manager.snapshot()["active_model_key"] is None
        assert manager.process is None
        original = manager.launch_spec

        def healthy(root, key):
            command, identity, backend, vision = original(root, key)
            command[-1] = "ok"
            return command, identity, backend, vision

        manager.launch_spec = healthy
        manager.request_switch("27b")
        await manager.task
        assert manager.snapshot()["model_phase"] == "ready"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_foreign_port_is_never_adopted_or_stopped(tmp_path):
    manager, calls, port = service(tmp_path)
    foreign = await asyncio.create_subprocess_exec(
        sys.executable,
        str(FIXTURE),
        str(port),
        "foreign",
        str(tmp_path / "foreign"),
        "ok",
    )
    try:
        await asyncio.sleep(0.15)
        with pytest.raises(RuntimeError, match="占用"):
            await manager.start("27b")
        assert foreign.returncode is None
        assert manager.process is None
    finally:
        await manager.close()
        foreign.terminate()
        await foreign.wait()


@pytest.mark.asyncio
async def test_shutdown_cancels_loading_and_leaves_no_process(tmp_path):
    manager, _, _ = service(tmp_path, lambda key, calls: "hang" if calls else "ok")
    await manager.start("27b")
    manager.request_switch("qwen38-rp")
    await asyncio.sleep(0.1)
    await manager.close()
    assert manager.process is None
    assert all(
        not alive(row["pid"])
        for row in map(json.loads, (tmp_path / "journal").read_text().splitlines())
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["ok", "hang"])
async def test_backend_sigterm_reaps_child_even_during_initial_loading(
    tmp_path, behavior
):
    import shutil

    for folder in ["config", "backend"]:
        (tmp_path / folder).mkdir()
    for name in ["companion.json", "persona.md"]:
        shutil.copy(Path("config") / name, tmp_path / "config" / name)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("backend/src").resolve())
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(FIXTURE.with_name("managed_backend.py")),
        str(tmp_path),
        str(free_port()),
        str(free_port()),
        behavior,
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if (tmp_path / "journal").exists():
                break
            if process.returncode is not None:
                raise AssertionError("backend exited before spawning model")
            await asyncio.sleep(0.02)
        child = json.loads((tmp_path / "journal").read_text().splitlines()[0])["pid"]
        process.terminate()
        await asyncio.wait_for(process.wait(), 3)
        assert not alive(child)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if (tmp_path / "journal").exists():
            for row in map(json.loads, (tmp_path / "journal").read_text().splitlines()):
                if alive(row["pid"]):
                    os.kill(row["pid"], 9)


@pytest.mark.asyncio
async def test_uncooperative_owned_model_is_force_killed(tmp_path):
    manager, _, _ = service(tmp_path, lambda key, calls: "ignore-term")
    await manager.start("27b")
    pid = manager.process.pid
    await asyncio.wait_for(manager.close(), 2)
    assert not alive(pid)


@pytest.mark.asyncio
async def test_same_identity_foreign_listener_winning_startup_race_is_not_adopted(
    tmp_path,
):
    import subprocess
    import time

    manager, _, port = service(tmp_path, lambda key, calls: "hang")
    foreign = None
    original = manager.port_busy

    def race():
        nonlocal foreign
        assert not original()
        foreign = subprocess.Popen(
            [
                sys.executable,
                str(FIXTURE),
                str(port),
                "27b",
                str(tmp_path / "foreign"),
                "ok",
            ]
        )
        deadline = time.monotonic() + 2
        while not original() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert original()
        return False  # Port was free at the earlier check.

    manager.port_busy = race
    try:
        with pytest.raises(RuntimeError, match="归属"):
            await manager.start("27b")
        assert manager.process is None
        assert foreign.poll() is None
    finally:
        await manager.close()
        if foreign:
            foreign.terminate()
            foreign.wait()
