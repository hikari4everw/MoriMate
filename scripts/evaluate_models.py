"""Owns sequential model processes; artifacts stay outside the conversation DB."""

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from run_local import ROOT, model_launch, wait_ready


def port_open(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def stop(process):
    if process and process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def sample_rss(process, done, peak):
    while not done.wait(0.5):
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True
        )
        value = result.stdout.strip()
        if value.isdigit():
            peak[0] = max(peak[0], int(value) / 1024)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["old", "candidate", "all"], default="all")
    parser.add_argument(
        "--suite", choices=["acceptance", "discussion"], default="acceptance"
    )
    parser.add_argument("--label", default="candidate-comparison")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--case-timeout", type=float, default=300)
    args = parser.parse_args()
    os.chdir(ROOT)
    if port_open(18080):
        parser.error("模型端口18080被占用；不会终止现有模型服务，请先关闭后再评测")
    folder = ROOT / ".runtime/evaluations" / args.label
    folder.mkdir(parents=True, exist_ok=True)
    voice, voice_log = None, None
    previous_signal = signal.signal(signal.SIGTERM, interrupted)
    try:
        if not port_open(10101):
            engines = list((ROOT / ".runtime/aivis").rglob("run"))
            if not engines:
                parser.error("AivisSpeech未安装")
            voice_log = (folder / "voice.log").open("w")
            voice = subprocess.Popen(
                [
                    str(engines[0]),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "10101",
                    "--disable_sentry",
                ],
                stdout=voice_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            wait_ready("http://127.0.0.1:10101/version", voice, 1800)
        else:
            with urllib.request.urlopen("http://127.0.0.1:10101/version", timeout=5):
                pass
        profiles = ["old", "candidate"] if args.profile == "all" else [args.profile]
        for profile in profiles:
            command, model, backend, _ = model_launch(
                ROOT, "27b" if profile == "old" else "qwen38-rp"
            )
            model_process, benchmark_process = None, None
            done, peak = threading.Event(), [0.0]
            monitor = None
            boot_started = time.monotonic()
            with (folder / f"{profile}-server.log").open("w") as model_log:
                try:
                    env = {**os.environ, "HF_HOME": str(ROOT / ".cache/huggingface")}
                    model_process = subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env=env,
                        stdout=model_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    monitor = threading.Thread(
                        target=sample_rss, args=(model_process, done, peak), daemon=True
                    )
                    monitor.start()
                    wait_ready("http://127.0.0.1:18080/v1/models", model_process, 240)
                    startup = time.monotonic() - boot_started
                    modes = ["off"] if profile == "old" else ["off", "limited", "full"]
                    for mode in modes:
                        label = args.label + "/" + profile + "-" + mode
                        print(f"开始对照 {profile}/{mode}", flush=True)
                        with (folder / f"{profile}-{mode}.log").open("w") as log:
                            benchmark_process = subprocess.Popen(
                                [
                                    sys.executable,
                                    "scripts/benchmark.py",
                                    "--model",
                                    model,
                                    "--backend",
                                    backend,
                                    "--thinking",
                                    mode,
                                    "--suite",
                                    args.suite,
                                    "--voice-url",
                                    "http://127.0.0.1:10101",
                                    "--speaker",
                                    "まお",
                                    "--label",
                                    label,
                                    "--count",
                                    str(args.count),
                                    "--case-timeout",
                                    str(args.case_timeout),
                                ],
                                stdout=log,
                                stderr=subprocess.STDOUT,
                                start_new_session=True,
                            )
                            returncode = benchmark_process.wait()
                            if returncode:
                                raise subprocess.CalledProcessError(
                                    returncode, benchmark_process.args
                                )
                        report_path = (
                            ROOT / ".runtime/evaluations" / label / "report.json"
                        )
                        report = json.loads(report_path.read_text())
                        report.update(
                            server_startup_seconds=round(startup, 3),
                            sampled_peak_model_rss_mib=round(peak[0], 1),
                            memory_measurement="sampled cumulative profile process RSS (modes share this peak), not total unified GPU/system memory; 0.5s interval",
                            cold_measurement="first request after server startup for first mode; later modes reuse the loaded model; not a cold machine",
                        )
                        report_path.write_text(
                            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                        )
                        print(f"完成 {profile}/{mode}: {report_path}", flush=True)
                finally:
                    stop(benchmark_process)
                    stop(model_process)
                    done.set()
                    if monitor:
                        monitor.join(timeout=2)
    finally:
        stop(voice)
        if voice_log:
            voice_log.close()
        signal.signal(signal.SIGTERM, previous_signal)


if __name__ == "__main__":
    main()
