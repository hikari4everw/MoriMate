import asyncio
import contextlib
import os
import signal
import socket
import subprocess
from pathlib import Path

import httpx

from .voices import VoiceCatalog, VoiceSynthesizer

BUSY_PHASES = {"starting", "stopping", "loading", "recovering"}


class VoiceService:
    def __init__(
        self, root, *, catalog=None, launch_spec=None, ready_timeout=180, stop_timeout=8
    ):
        self.root = Path(root)
        self.catalog = catalog or VoiceCatalog(root)
        self.launch_spec = launch_spec or self.catalog.launch
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.process = None
        self.synthesizer = None
        self.active_key = None
        self.target_key = None
        self.failed_key = None
        self.phase = "starting"
        self.error = ""
        self.recovery = ""
        self.task = None
        self.startup_task = None
        self.watch_task = None
        self.on_change = None
        self.lock = asyncio.Lock()
        self.closed = False

    @property
    def ready(self):
        return (
            self.phase == "ready"
            and self.process is not None
            and self.process.poll() is None
        )

    def snapshot(self):
        return {
            "managed_voices": True,
            "available_voices": self.catalog.available(self.active_key),
            "active_voice_key": self.active_key,
            "target_voice_key": self.target_key,
            "failed_voice_key": self.failed_key,
            "voice_phase": self.phase,
            "voice_error": self.error,
            "voice_recovery": self.recovery,
            "voice_label": self.catalog.label(self.active_key)
            if self.ready
            else "语音未就绪 · 文字模式",
        }

    async def notify(self):
        if self.on_change:
            with contextlib.suppress(Exception):
                await self.on_change()

    async def stop_process(self):
        if self.watch_task:
            self.watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.watch_task
            self.watch_task = None
        process = self.process
        self.synthesizer = None
        self.active_key = None
        if process is None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), self.stop_timeout)
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await asyncio.to_thread(process.wait)
        self.process = None

    async def owns_port(self, port):
        result = await asyncio.to_thread(
            subprocess.run,
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return (
            self.process is not None
            and self.process.poll() is None
            and set(result.stdout.split()) == {str(self.process.pid)}
        )

    async def load(self, key):
        port = self.catalog.engine(key)["port"]
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"语音端口 {port} 已占用；不会接管或终止外部服务")
        command, directory, env = self.launch_spec(key)
        logs = self.root / ".runtime"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "managed-voice.log").open("a") as output:
            self.process = subprocess.Popen(
                command,
                cwd=directory,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        synthesizer = VoiceSynthesizer(self.catalog, key)
        async with asyncio.timeout(self.ready_timeout):
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                while True:
                    if self.process.poll() is not None:
                        raise RuntimeError(
                            f"语音进程退出（{self.process.returncode}），请查看 .runtime/managed-voice.log"
                        )
                    try:
                        response = await client.get(
                            f"http://127.0.0.1:{port}/openapi.json"
                        )
                        response.raise_for_status()
                    except httpx.HTTPError:
                        await asyncio.sleep(0.1)
                        continue
                    if not await self.owns_port(port):
                        raise RuntimeError("语音端口归属校验失败；不会接管外部服务")
                    break
            path = await synthesizer.synthesize("こんにちは。", "neutral")
            Path(path).unlink(missing_ok=True)
            if self.process.poll() is not None or not await self.owns_port(port):
                raise RuntimeError("语音合成验证后服务已退出或端口归属发生变化")
        self.synthesizer = synthesizer
        self.active_key = key

    async def start(self, key=None):
        if self.closed:
            return
        self.startup_task = asyncio.current_task()
        key = key or self.catalog.default
        try:
            try:
                self.catalog.profile(key)
            except ValueError:
                self.target_key = self.catalog.fallback
                await self.transition(self.catalog.fallback)
                self.error = f"启动语音角色未登记：{key}" + (
                    f"；{self.error}" if self.error else ""
                )
                if self.ready:
                    self.recovery = f"已回退 {self.catalog.label(self.catalog.fallback)}，请检查启动参数或选择已登记角色"
                await self.notify()
                return
            self.target_key = key
            await self.transition(key)
        finally:
            self.startup_task = None

    def request_switch(self, key):
        self.catalog.profile(key)
        if self.closed:
            raise RuntimeError("语音服务已关闭")
        if (self.task and not self.task.done()) or (
            self.startup_task and not self.startup_task.done()
        ):
            raise RuntimeError("语音正在切换，请稍候")
        if self.ready and key == self.active_key:
            return
        self.target_key = key
        self.phase = "stopping"
        self.error = self.recovery = ""
        self.failed_key = None
        self.task = asyncio.create_task(self.transition(key))

    async def transition(self, key):
        await self.notify()
        async with self.lock:
            try:
                await self.stop_process()
                self.phase = "loading"
                await self.notify()
                await self.load(key)
                self.phase = "ready"
            except asyncio.CancelledError:
                await self.stop_process()
                raise
            except Exception as exc:
                self.failed_key = key
                self.error = f"{self.catalog.label(key)} 加载失败：{exc or '启动超时'}"
                await self.stop_process()
                if key != self.catalog.fallback:
                    self.phase = "recovering"
                    await self.notify()
                    try:
                        await self.load(self.catalog.fallback)
                        self.phase = "ready"
                        self.recovery = f"已回退 {self.catalog.label(self.catalog.fallback)}，可重试原角色"
                    except asyncio.CancelledError:
                        await self.stop_process()
                        raise
                    except Exception as fallback_error:
                        await self.stop_process()
                        self.phase = "error"
                        self.recovery = f"AivisSpeech 回退失败：{fallback_error or '启动超时'}；继续文字对话，可重试"
                else:
                    self.phase = "error"
                    self.recovery = "继续文字对话，可重试或选择其他角色"
        if self.ready:
            self.watch_task = asyncio.create_task(self.watch_process(self.process))
        await self.notify()

    async def watch_process(self, process):
        code = await asyncio.to_thread(process.wait)
        if self.process is process and self.phase == "ready":
            self.phase = "error"
            self.failed_key = self.active_key
            self.active_key = None
            self.synthesizer = None
            self.error = f"语音进程已退出（{code}），请查看 .runtime/managed-voice.log"
            self.recovery = "继续文字对话，可重试当前角色或切换引擎"
            await self.notify()

    async def synthesize(self, text, tone="neutral"):
        async with self.lock:
            if not self.ready:
                raise RuntimeError("语音服务未就绪，请重试或切换引擎；文字对话仍可使用")
            return await self.synthesizer.synthesize(text, tone)

    async def async_generate_audio(self, text):
        return await self.synthesize(text)

    async def close(self):
        self.closed = True
        for task in (self.startup_task, self.task):
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        async with self.lock:
            await self.stop_process()
        self.phase = "stopped"
