import asyncio
import sys

import pytest

from open_llm_vtuber.companion.capture import WindowCapture


async def test_timeout_is_actionable_and_child_is_reaped(tmp_path):
    helper = tmp_path / "slow-capture"
    helper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    helper.chmod(0o755)
    capture = WindowCapture(helper)
    capture.timeout = 0.02
    with pytest.raises(RuntimeError, match="超时"):
        await asyncio.wait_for(capture.windows(), 1)


async def test_cancellation_stops_the_capture_child(tmp_path):
    marker = tmp_path / "finished"
    helper = tmp_path / "capture"
    helper.write_text(
        f"#!{sys.executable}\nimport time\nfrom pathlib import Path\ntime.sleep(0.4)\nPath({str(marker)!r}).touch()\n"
    )
    helper.chmod(0o755)
    task = asyncio.create_task(WindowCapture(helper).windows())
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    assert not marker.exists()
