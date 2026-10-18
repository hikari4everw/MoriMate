from pathlib import Path
import sys
import uvicorn

sys.path.insert(0, str(Path(__file__).parent / "src"))
from open_llm_vtuber.companion.app import create_app  # noqa: E402


class CompanionServer(uvicorn.Server):
    def handle_exit(self, sig, frame):
        super().handle_exit(sig, frame)
        # Uvicorn normally waits for lifespan startup before processing shutdown.
        # Cancel loading now, so the launcher never has to kill an owning backend.
        manager = self.config.app.state.model_service
        if not self.started and manager:
            manager.cancel_startup()


def run(app, port=12393):
    CompanionServer(
        uvicorn.Config(app, host="127.0.0.1", port=port, ws_max_size=65536)
    ).run()


if __name__ == "__main__":
    run(create_app())
