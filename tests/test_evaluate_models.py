"""Signal cleanup uses only isolated sleeping fixture processes, never model ports."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_sigterm_cleans_owned_model_and_benchmark(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "benchmark.py").write_text(
        "import os,time\nfrom pathlib import Path\n"
        "Path('benchmark.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n"
    )
    wrapper = tmp_path / "runner.py"
    wrapper.write_text(
        "import sys,contextlib\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path('scripts').resolve())!r})\n"
        "import evaluate_models as e\n"
        f"e.ROOT=Path({str(tmp_path)!r})\n"
        "e.port_open=lambda port: port==10101\n"
        "e.urllib.request.urlopen=lambda *a,**k: contextlib.nullcontext()\n"
        "e.wait_ready=lambda *a: None\n"
        "e.model_launch=lambda *a: ([sys.executable,'-c',"
        "\"import os,time;from pathlib import Path;Path('model.pid').write_text(str(os.getpid()));time.sleep(60)\"], 'fixture','mlx',False)\n"
        "sys.argv=['evaluate_models','--profile','old']\n"
        "e.main()\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(wrapper)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    children = []
    try:
        deadline = time.monotonic() + 10
        while not all(
            (tmp_path / f"{name}.pid").exists() for name in ("model", "benchmark")
        ):
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        children = [
            int((tmp_path / f"{name}.pid").read_text())
            for name in ("model", "benchmark")
        ]
        process.terminate()
        process.wait(timeout=15)
        deadline = time.monotonic() + 2
        while any(alive(pid) for pid in children) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not any(alive(pid) for pid in children)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in children:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
