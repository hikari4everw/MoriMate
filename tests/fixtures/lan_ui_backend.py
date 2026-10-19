"""Isolated browser acceptance fixture; never reads personal runtime data."""

import asyncio
import json
import math
import struct
import shutil
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
from open_llm_vtuber.companion.app import create_app  # noqa: E402
from open_llm_vtuber.companion import guest_server  # noqa: E402
from open_llm_vtuber.companion.sharing import GuestRegistry  # noqa: E402
import uvicorn  # noqa: E402
from starlette.responses import HTMLResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402


class Model:
    thinking_mode = "off"

    async def stream(self, messages, **kwargs):
        await asyncio.sleep(0.15)
        text = "这是隔离测试回复；没有使用主人的聊天和记忆。"
        if "图书管理员" in messages[0]["content"]:
            text = "我是你定制的图书管理员，正在使用独立人格。"
        yield (
            json.dumps(
                {"zh": text, "ja": "これは独立したテストです。", "emotion": "joy"}
            )
            + "\n"
        )


class Voice:
    def __init__(self, root):
        self.root = root

    async def async_generate_audio(self, text):
        import uuid

        path = self.root / (uuid.uuid4().hex + ".wav")
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(
                b"".join(
                    struct.pack(
                        "<h", int(500 * math.sin(2 * math.pi * 440 * sample / 16000))
                    )
                    for sample in range(1600)
                )
            )
        return str(path)


if __name__ == "__main__":
    # Loopback aliases let automated browsers exercise both origins without LAN exposure.
    guest_server.lan_addresses = lambda: [
        {"address": "127.0.0.1", "label": "隔离测试网卡"}
    ]
    guest_server.LanSharing.port = 12494
    with tempfile.TemporaryDirectory(prefix="companion-lan-ui-") as temporary:
        root = Path(temporary)
        (root / "config").mkdir()
        for name in ("companion.json", "persona.md"):
            shutil.copy(ROOT / "config" / name, root / "config" / name)
        (root / "backend").mkdir()
        (root / "backend/live2d-models").symlink_to(
            ROOT / "backend/live2d-models", target_is_directory=True
        )
        (root / "desktop/out").mkdir(parents=True)
        (root / "desktop/out/renderer").symlink_to(
            ROOT / "desktop/out/renderer", target_is_directory=True
        )
        registry = GuestRegistry(root / ".runtime")
        registry.set_password("fixture-password")
        registry.close()
        app = create_app(root, model=Model(), voice=Voice(root))

        async def mobile_preview(request):
            return HTMLResponse(
                '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body style="margin:0;background:#333"><iframe title="390px访客窄屏验收" src="http://127.0.0.1:12494" style="width:390px;height:844px;border:0"></iframe></body></html>'
            )

        app.router.routes.insert(0, Route("/mobile-preview", mobile_preview))
        uvicorn.run(app, host="127.0.0.1", port=12493, log_level="warning")
