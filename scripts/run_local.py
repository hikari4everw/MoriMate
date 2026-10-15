"""Owns only the processes it starts; Ctrl-C shuts them down in reverse order."""

import argparse
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {"9b": "Qwen3.5-9B-4bit", "27b": "Qwen3.5-27B-4bit", "qwen3": "Qwen3-8B-4bit"}


def wait_ready(url, process, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(
                f"服务退出（{process.returncode}），请查看 .runtime 日志"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except (OSError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"服务启动超时：{url}")


def main():
    parser = argparse.ArgumentParser(description="启动本地 Hiyori；不连接付费 API")
    parser.add_argument("--model", choices=MODELS, default="9b")
    parser.add_argument("--voice", choices=["sayo", "mao"], default="sayo")
    parser.add_argument(
        "--web", action="store_true", help="仅服务；手动打开 http://127.0.0.1:12393"
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    model = ".runtime/models/" + MODELS[args.model]
    if not Path(model, "config.json").exists():
        parser.error("模型未下载，请先运行 scripts/setup.sh --model " + args.model)
    ports = [8080, 12393, 50021 if args.voice == "sayo" else 10101]
    for port in ports:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(
                    f"端口 {port} 已占用；请关闭此前启动的桌宠服务后重试。不会终止其他程序。"
                )
    processes = []
    logs = []
    env = os.environ.copy()
    env["HF_HOME"] = str(ROOT / ".cache/huggingface")
    env["COMPANION_MODEL"] = model
    env["COMPANION_VOICE"] = args.voice

    def launch(command, name):
        log = open(ROOT / ".runtime" / f"{name}.log", "a")
        logs.append(log)
        process = subprocess.Popen(
            command,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        processes.append(process)
        return process

    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    try:
        if args.voice == "sayo":
            voice = launch(
                [
                    ".runtime/voicevox/macos-arm64/run",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "50021",
                ],
                "voicevox",
            )
        else:
            candidates = list((ROOT / ".runtime/aivis").rglob("run"))
            if not candidates:
                raise RuntimeError(
                    "AivisSpeech 未安装，请先运行 scripts/setup.sh --voice mao"
                )
            voice = launch(
                [
                    str(candidates[0]),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "10101",
                    "--disable_sentry",
                ],
                "aivis",
            )
        print(
            "正在启动语音引擎，首次 AivisSpeech 会下载模型，详情见 .runtime/aivis.log",
            flush=True,
        )
        wait_ready(
            f"http://127.0.0.1:{ports[2]}/version",
            voice,
            1800 if args.voice == "mao" else 120,
        )
        module = "mlx_lm.server" if args.model == "qwen3" else "mlx_vlm.server"
        model_process = launch(
            [
                ".venv-mlx/bin/python",
                "-m",
                module,
                "--model",
                model,
                "--host",
                "127.0.0.1",
                "--port",
                "8080",
            ]
            + (
                ["--chat-template-args", '{"enable_thinking":false}']
                if args.model == "qwen3"
                else []
            ),
            "mlx",
        )
        wait_ready("http://127.0.0.1:8080/v1/models", model_process, 180)
        backend = launch([".venv/bin/python", "backend/run_companion.py"], "backend")
        wait_ready("http://127.0.0.1:12393/health", backend)
        print(
            f"本地桌宠已启动：{MODELS[args.model]} / {args.voice}\nhttp://127.0.0.1:12393\n按 Ctrl-C 关闭本次启动的服务。",
            flush=True,
        )
        if not args.web:
            launch(
                ["desktop/node_modules/.bin/electron", "desktop/out/main/index.js"],
                "desktop",
            )
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
