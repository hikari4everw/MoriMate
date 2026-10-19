"""Owns only the processes it starts; Ctrl-C shuts them down in reverse order."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from open_llm_vtuber.companion.model_service import model_launch  # noqa: E402, F401
from open_llm_vtuber.companion.voices import VoiceCatalog  # noqa: E402

MODELS = {
    "9b": "Qwen3.5-9B-4bit",
    "27b": "Qwen3.5-27B-4bit",
    "qwen3": "Qwen3-8B-4bit",
    "qwen38-rp": "Qwen3.8-27B-Heretic-JP-Roleplay-NSFW-Q4_K_M",
}


def ensure_observer(root=ROOT):
    source = root / "native/WindowObserver.swift"
    executable = root / ".runtime/window-observer"
    if (
        executable.exists()
        and executable.stat().st_mtime_ns >= source.stat().st_mtime_ns
    ):
        return
    executable.parent.mkdir(parents=True, exist_ok=True)
    cache = root / ".cache/swift"
    cache.mkdir(parents=True, exist_ok=True)
    temporary = executable.with_suffix(".building")
    try:
        subprocess.run(
            [
                "swiftc",
                "-parse-as-library",
                "-O",
                "-module-cache-path",
                str(cache),
                str(source),
                "-o",
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        temporary.replace(executable)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"窗口观察工具编译失败；未启动模型或语音服务：{exc.stderr}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


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
    parser = argparse.ArgumentParser(description="启动 Hiyori；云 API 由主人在界面配置")
    parser.add_argument("--model", choices=MODELS, default=None)
    parser.add_argument(
        "--voice",
        choices=["sayo", "mao", "roxy"],
        default=None,
        help="默认使用 config/voices.json 中的角色",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--web", dest="web", action="store_true", help="浏览器模式（默认）"
    )
    mode.add_argument(
        "--desktop",
        dest="web",
        action="store_false",
        help="原生模式，须通过 macOS 安全验证",
    )
    parser.set_defaults(web=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    if not args.web:
        desktop_app = ROOT / "desktop/node_modules/electron/dist/Electron.app"
        if not desktop_app.exists():
            parser.error("Electron 不存在；请使用 --web 启动浏览器版")
        assessment = subprocess.run(
            ["/usr/sbin/spctl", "--assess", "--type", "execute", str(desktop_app)],
            capture_output=True,
            text=True,
        )
        if assessment.returncode:
            parser.error(
                "Electron 未通过 macOS 安全验证；请使用 --web。未启动模型或语音服务。"
            )
    saved = ROOT / ".runtime/model-selection.json"
    saved_selection = json.loads(saved.read_text()).get("selection", "") if saved.exists() else ""
    cloud_start = args.model is None and saved_selection.startswith("cloud:")
    args_model = args.model or "27b"
    try:
        if cloud_start:
            model, backend_name, supports_vision = "cloud", "mlx", False
        else:
            _, model, backend_name, supports_vision = model_launch(ROOT, args_model)
    except RuntimeError as exc:
        parser.error(str(exc))
    voice_key = args.voice or VoiceCatalog(ROOT).default
    ports = [12393] if cloud_start else [18080, 12393]
    for port in ports:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(
                    f"端口 {port} 已占用；请关闭此前启动的桌宠服务后重试。不会终止其他程序。"
                )
    ensure_observer()
    processes = []
    logs = []
    env = os.environ.copy()
    env["HF_HOME"] = str(ROOT / ".cache/huggingface")
    env["COMPANION_MANAGED_MODELS"] = "1"
    env["COMPANION_MODEL_KEY"] = args_model
    if args.model is not None:
        env["COMPANION_FORCE_MODEL"] = "1"
    env["COMPANION_MODEL"] = model
    env["COMPANION_MODEL_BACKEND"] = backend_name
    env["COMPANION_MODEL_VISION"] = "1" if supports_vision else "0"
    env["COMPANION_MANAGED_VOICES"] = "1"
    env["COMPANION_VOICE"] = voice_key

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
        backend = launch([".venv/bin/python", "backend/run_companion.py"], "backend")
        wait_ready("http://127.0.0.1:12393/health", backend, 210)
        print(
            f"桌宠已启动：{saved_selection if cloud_start else MODELS[args_model]} / 语音首选 {voice_key}\nhttp://127.0.0.1:12393\n语音加载状态见界面；日志 .runtime/managed-voice.log\n按 Ctrl-C 关闭本次启动的服务。",
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
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
