"""Own exactly one local model process; never adopt an occupied port."""

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx

from .model import LocalModel


def model_launch(root, key):
    assets = json.loads((root / "config/assets.json").read_text())
    spec = assets["models"][key]
    folder = root / ".runtime/models" / spec["directory"]
    if spec.get("backend") == "llama.cpp":
        binary = (
            root
            / ".runtime"
            / assets["llama_cpp"]["directory"]
            / "build/bin/llama-server"
        )
        if not binary.exists() or not all(
            (folder / item["name"]).exists() for item in spec["files"]
        ):
            raise RuntimeError(
                "候选资源未准备，请先运行 .venv/bin/python scripts/prepare_candidate.py"
            )
        weights, projector = [folder / item["name"] for item in spec["files"]]
        return (
            [
                str(binary),
                "--model",
                str(weights),
                "--mmproj",
                str(projector),
                "--alias",
                spec["alias"],
                "--ctx-size",
                str(spec["context"]),
                "--parallel",
                "1",
                "--n-gpu-layers",
                "99",
                "--jinja",
                "--reasoning-format",
                "deepseek",
                "--reasoning-budget",
                "-1",
                "--host",
                "127.0.0.1",
                "--port",
                "18080",
            ],
            spec["alias"],
            "llama.cpp",
            spec["vision"],
        )
    identity = ".runtime/models/" + spec["directory"]
    if not (folder / "config.json").exists():
        raise RuntimeError("模型未下载，请先运行 scripts/setup.sh --model " + key)
    command = [
        str(root / ".venv-mlx/bin/python"),
        "-m",
        "mlx_lm.server" if key == "qwen3" else "mlx_vlm.server",
        "--model",
        identity,
        "--host",
        "127.0.0.1",
        "--port",
        "18080",
    ]
    if key == "qwen3":
        command += ["--chat-template-args", '{"enable_thinking":false}']
    return command, identity, "mlx", spec["vision"]


MODEL_LABELS = {"27b": "Qwen3.5-27B（默认）", "qwen38-rp": "Qwen3.8-27B（候选）"}


class ModelService:
    def __init__(
        self,
        root,
        *,
        launch_spec=model_launch,
        port=18080,
        ready_timeout=180,
        stop_timeout=8,
    ):
        self.root = Path(root)
        self.launch_spec = launch_spec
        self.port = port
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.process = None
        self.task = None
        self.startup_task = None
        self.active_key = None
        self.target_key = None
        self.phase = "starting"
        self.error = ""
        self.recovery = ""
        self.failed_key = None
        self.candidate_thinking = "limited"
        self.transport = None
        self.vision = False
        self.on_change = None

    @property
    def ready(self):
        return self.phase == "ready" and self.active_key is not None

    def snapshot(self):
        return {
            "managed_models": True,
            "active_model_key": self.active_key,
            "target_model_key": self.target_key,
            "thinking_mode": self.candidate_thinking
            if self.active_key == "qwen38-rp"
            else "off",
            "candidate_thinking_mode": self.candidate_thinking,
            "available_models": [
                {"key": key, "label": label} for key, label in MODEL_LABELS.items()
            ],
            "model_phase": self.phase,
            "model_error": self.error,
            "model_recovery": self.recovery,
            "failed_model_key": self.failed_key,
            "model": self.transport.model
            if self.transport and self.active_key
            else None,
            "supports_vision": self.vision,
        }

    async def notify(self):
        if self.on_change:
            await self.on_change()

    def port_busy(self):
        with socket.socket() as sock:
            return sock.connect_ex(("127.0.0.1", self.port)) == 0

    async def stop_process(self):
        process = self.process
        if process:
            # The process group was created by us; never identify ownership by port.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + self.stop_timeout
            while process.poll() is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await asyncio.to_thread(process.wait)
            self.process = None

    async def load(self, key, spec):
        if self.port_busy():
            raise RuntimeError(f"模型端口 {self.port} 已占用；不会接管或终止未知服务")
        command, identity, backend, vision = spec
        logs = self.root / ".runtime"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "managed-model.log").open("a") as log:
            self.process = subprocess.Popen(
                command,
                cwd=self.root,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + self.ready_timeout
        async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"模型进程退出（{self.process.returncode}），请查看 .runtime/managed-model.log"
                    )
                try:
                    response = await client.get(
                        f"http://127.0.0.1:{self.port}/v1/models"
                    )
                    response.raise_for_status()
                    ids = [row["id"] for row in response.json()["data"]]
                    if identity not in ids:
                        raise RuntimeError(f"模型身份不符：期望 {identity}，实际 {ids}")
                    listeners = await asyncio.to_thread(
                        subprocess.run,
                        ["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN", "-t"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if (
                        set(listeners.stdout.split()) != {str(self.process.pid)}
                        or self.process.poll() is not None
                    ):
                        raise RuntimeError("模型端口归属校验失败；不会接管外部服务")
                    self.transport = LocalModel(
                        f"http://127.0.0.1:{self.port}/v1",
                        identity,
                        backend=backend,
                        thinking_mode=self.candidate_thinking,
                    )
                    self.active_key, self.vision = key, vision
                    return
                except (httpx.HTTPError, ValueError, KeyError):
                    await asyncio.sleep(0.05)
        raise RuntimeError(f"模型启动超时（{self.ready_timeout:g} 秒）")

    def cancel_startup(self):
        if self.startup_task and not self.startup_task.done():
            self.startup_task.cancel()

    async def start(self, key):
        self.startup_task = asyncio.current_task()
        try:
            await self.load(key, self.launch_spec(self.root, key))
            self.target_key = key
            self.phase = "ready"
        except BaseException:
            await self.stop_process()
            raise
        finally:
            self.startup_task = None

    def request_switch(self, key):
        if key not in MODEL_LABELS:
            raise ValueError("模型选择无效")
        if self.task and not self.task.done():
            raise RuntimeError("模型正在切换，请稍候")
        if key == self.active_key and self.ready:
            return
        self.target_key = key
        self.phase = "stopping"
        self.error = self.recovery = ""
        self.failed_key = None
        self.task = asyncio.create_task(self.switch(key))

    async def switch(self, key):
        previous, thinking = self.active_key, self.candidate_thinking
        try:
            await self.notify()
            spec = self.launch_spec(self.root, key)
            await self.stop_process()
            self.active_key = None
            self.phase = "loading"
            await self.notify()
            await self.load(key, spec)
            self.phase = "ready"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error, self.failed_key = str(exc), key
            self.candidate_thinking = thinking
            if (
                previous
                and self.active_key == previous
                and self.process
                and self.process.poll() is None
            ):
                self.phase, self.recovery = (
                    "ready",
                    "资源检查失败，原模型仍可用，无需恢复",
                )
            else:
                await self.stop_process()
                self.active_key = None
                if previous:
                    self.phase = "recovering"
                    await self.notify()
                    try:
                        await self.load(previous, self.launch_spec(self.root, previous))
                        self.phase, self.recovery = (
                            "ready",
                            "已恢复切换前的模型及思考设置",
                        )
                    except Exception as restore:
                        await self.stop_process()
                        self.active_key = None
                        self.phase, self.recovery = (
                            "error",
                            f"恢复失败：{restore}；可以重新选择或重试",
                        )
                else:
                    self.phase, self.recovery = (
                        "error",
                        "没有可恢复模型；可以重新选择或重试",
                    )
        await self.notify()

    def set_thinking(self, mode):
        if not self.ready or (self.task and not self.task.done()):
            raise RuntimeError("模型正在切换或不可用")
        if self.active_key != "qwen38-rp":
            raise ValueError("旧模型固定关闭思考")
        if mode not in {"off", "limited", "full"}:
            raise ValueError("思考档位无效")
        self.candidate_thinking = mode
        self.transport.thinking_mode = mode

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.stop_process()
