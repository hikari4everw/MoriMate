"""Real local model/TTS measurements. Isolated from the user's conversation DB."""

import argparse
import asyncio
import base64
import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from open_llm_vtuber.companion.memory import MemoryStore  # noqa: E402
from open_llm_vtuber.companion.model import LocalModel  # noqa: E402
from open_llm_vtuber.companion.runtime import CompanionRuntime  # noqa: E402
from open_llm_vtuber.tts.voicevox_tts import TTSEngine  # noqa: E402

CASES = [
    "你好，我们第一次见面。你想怎么介绍自己？",
    "今天我又拖延了，你可别像客服一样安慰我。",
    "我决定从明天开始不再拖延，你觉得这个计划怎么样？",
    "不许只附和我。我觉得每天只睡四小时也没关系。",
    "你刚才的笑话有点冷，我的电脑都不用风扇了。",
    "认真一点，今天工作搞砸了，我很沮丧。",
    "我平时喜欢不加糖的乌龙茶，咖啡会让我睡不着。",
    "你还记得我喜欢喝什么吗？",
    "我们去年是不是一起去过海边？",
    "我还没给你看游戏，你知道刚才女主说了什么吗？",
]


async def main(args):
    folder = ROOT / ".runtime/evaluations" / args.label
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as temp:
        store = MemoryStore(Path(temp) / "memory.db")
        first = None
        output = []
        errors = []
        started = 0

        async def send(p):
            nonlocal first
            if p["type"] == "audio":
                output.append({"zh": p["display_text"]["text"], "ja": p["tts_text"]})
                if first is None:
                    first = time.monotonic() - started
                    if p["audio"]:
                        (folder / f"{len(results):02}.wav").write_bytes(
                            base64.b64decode(p["audio"])
                        )
            elif p["type"] == "error":
                errors.append(p["message"])

        runtime = CompanionRuntime(
            store,
            LocalModel("http://127.0.0.1:8080/v1", args.model),
            TTSEngine(
                base_url=args.voice_url,
                speaker_name=args.speaker,
                style_name=args.style,
                cache_dir=str(ROOT / ".runtime/audio"),
            ),
            send,
            (ROOT / "config/persona.md").read_text(),
        )
        runtime.muted = args.text_only
        for prompt in CASES[: args.count]:
            first = None
            output = []
            errors = []
            started = time.monotonic()
            await runtime.start(prompt)
            await runtime.task
            row = {
                "prompt": prompt,
                "first_payload_seconds": round(first, 3) if first else None,
                "total_seconds": round(time.monotonic() - started, 3),
                "output": output,
                "errors": errors,
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        store.close()
    times = [
        r["first_payload_seconds"]
        for r in results[1:]
        if r["first_payload_seconds"] is not None and not r["errors"]
    ]
    report = {
        "model": args.model,
        "speaker": args.speaker,
        "text_only": args.text_only,
        "measurement": "server first payload ready, excludes browser playback; first request listed separately; VM not included",
        "first_request": results[0]["first_payload_seconds"],
        "warm_n": len(times),
        "warm_median": statistics.median(times) if times else None,
        "warm_p95_nearest_rank": sorted(times)[math.ceil(0.95 * len(times)) - 1]
        if times
        else None,
        "cases": results,
    }
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print("Saved", folder / "report.json", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--voice-url", default="http://127.0.0.1:50021")
    p.add_argument("--speaker", default="小夜/SAYO")
    p.add_argument("--style", default="ノーマル")
    p.add_argument("--text-only", action="store_true")
    p.add_argument("--count", type=int, default=10)
    asyncio.run(main(p.parse_args()))
