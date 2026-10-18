import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
from open_llm_vtuber.companion.app import create_app  # noqa: E402
from open_llm_vtuber.companion.voice_service import VoiceService  # noqa: E402
from open_llm_vtuber.companion.voices import VoiceCatalog  # noqa: E402
import uvicorn  # noqa: E402


class Model:
    async def stream(self, *args, **kwargs):
        yield '{"zh":"这是独立测试，不使用个人记录。","ja":"これはテストです。","emotion":"joy","voice_tone":"relieved"}\n'


catalog = VoiceCatalog(ROOT)
catalog.engines["gpt-sovits"]["port"] = 9882
catalog.engines["aivis"]["port"] = 10103


def launch(key):
    return (
        [
            sys.executable,
            str(Path(__file__).with_name("voice_server.py")),
            str(catalog.engine(key)["port"]),
            "ok",
        ],
        ROOT,
        os.environ.copy(),
    )


voices = VoiceService(ROOT, catalog=catalog, launch_spec=launch)
app = create_app(
    ROOT,
    model=Model(),
    voice_service=voices,
    database_path=ROOT / ".runtime/voice-ui-test.sqlite3",
)
uvicorn.run(app, host="127.0.0.1", port=12394)
