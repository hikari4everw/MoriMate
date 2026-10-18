import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from open_llm_vtuber.companion.voice_service import VoiceService  # noqa: E402
from open_llm_vtuber.companion.voices import VoiceCatalog, VOICE_TONES  # noqa: E402


async def evaluate(args):
    catalog = VoiceCatalog(ROOT)
    catalog.engines["gpt-sovits"]["port"] = args.gpt_port
    catalog.engines["aivis"]["port"] = args.aivis_port
    destination = ROOT / ".runtime/voice-evaluations" / args.label
    destination.mkdir(parents=True, exist_ok=False)
    manager = VoiceService(ROOT, catalog=catalog, ready_timeout=300)
    report = {
        "text": args.text,
        "reference_transcripts_verified": catalog.voices["roxy"].get(
            "reference_transcripts_verified", False
        ),
        "loads": [],
        "samples": [],
        "rss_peak_mib": 0,
        "memory_scope": "0.5s sampled RSS of the owned voice process only",
    }

    async def sample_memory():
        while True:
            if manager.process and manager.process.poll() is None:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["ps", "-o", "rss=", "-p", str(manager.process.pid)],
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip().isdigit():
                    report["rss_peak_mib"] = max(
                        report["rss_peak_mib"], int(result.stdout.strip()) / 1024
                    )
            await asyncio.sleep(0.5)

    sampler = asyncio.create_task(sample_memory())
    try:
        for index, key in enumerate(["roxy", "mao", "roxy"]):
            started = time.monotonic()
            if index == 0:
                await manager.start(key)
            else:
                manager.request_switch(key)
                await manager.task
            report["loads"].append(
                {
                    "requested": key,
                    "active": manager.active_key,
                    "ready_seconds_including_warmup": round(
                        time.monotonic() - started, 3
                    ),
                    "error": manager.error,
                    "recovery": manager.recovery,
                }
            )
            print(json.dumps(report["loads"][-1], ensure_ascii=False), flush=True)
            if not manager.ready or manager.active_key != key:
                raise RuntimeError(
                    f"未按要求加载 {key}，不能把回退引擎的声音计为该角色验收"
                )
            tones = sorted(VOICE_TONES) if index < 2 else ["neutral"]
            for tone in tones:
                started = time.monotonic()
                path = await manager.synthesize(args.text, tone)
                elapsed = time.monotonic() - started
                output = destination / f"{index}-{key}-{tone}.wav"
                shutil.move(path, output)
                with wave.open(str(output)) as audio:
                    duration = audio.getnframes() / audio.getframerate()
                row = {
                    "voice": key,
                    "tone": tone,
                    "file": output.name,
                    "synthesis_seconds": round(elapsed, 3),
                    "audio_seconds": round(duration, 3),
                    "real_time_factor": round(elapsed / duration, 3),
                }
                report["samples"].append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)
        await manager.close()
        (destination / "metrics.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(f"结果：{destination}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="独立端口对照本地语音，不读取个人对话，不接管现有服务"
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--gpt-port", type=int, default=9881)
    parser.add_argument("--aivis-port", type=int, default=10102)
    parser.add_argument(
        "--text",
        default="今日は一緒に過ごせてうれしいです。少し休んでから、またお話ししましょう。",
    )
    args = parser.parse_args()
    if not args.label or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in args.label
    ):
        parser.error("label 只允许字母、数字、连字符与下划线")
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
