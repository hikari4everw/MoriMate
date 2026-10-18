import importlib.util
import sys
from pathlib import Path

from open_llm_vtuber.companion.app import create_app
from open_llm_vtuber.companion.model_service import ModelService

root, model_port, backend_port, behavior = sys.argv[1:]
root = Path(root)
fixture = Path(__file__).with_name("model_server.py")


def launch(_, key):
    return (
        [
            sys.executable,
            str(fixture),
            model_port,
            key,
            str(root / "journal"),
            behavior,
        ],
        key,
        "mlx",
        True,
    )


manager = ModelService(root, launch_spec=launch, port=int(model_port), stop_timeout=0.2)
app = create_app(root, voice=object(), model_service=manager)
spec = importlib.util.spec_from_file_location(
    "runner", Path(__file__).resolve().parents[2] / "backend/run_companion.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.run(app, port=int(backend_port))
