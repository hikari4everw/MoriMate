import asyncio
import json


class WindowCapture:
    def __init__(self, executable, timeout=20):
        self.executable = str(executable)
        self.timeout = timeout

    async def run(self, *args):
        process = await asyncio.create_subprocess_exec(
            self.executable,
            *map(str, args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError as exc:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise RuntimeError(
                "窗口捕获超时：请检查屏幕录制权限和窗口状态，再重新预览"
            ) from exc
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(
                stderr.decode().strip() or "窗口捕获失败，请检查屏幕录制权限"
            )
        return json.loads(stdout)

    async def windows(self):
        return await self.run("list")

    async def capture(self, window, region=None):
        return await self.run("capture", window, *(region or []))
