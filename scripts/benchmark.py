"""Real local model/TTS measurements. Isolated from the user's conversation DB."""

import argparse
import asyncio
import base64
import json
import math
import statistics
import struct
import sys
import tempfile
import time
import wave
import zlib
from pathlib import Path
from datetime import datetime

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

ACCEPTANCE_CASES = [
    "你好，今天想和你随便聊聊。",
    "今天游戏又输了，你怎么说？",
    "又输了，还是刚才那个地方。",
    "第三次输了，换个角度聊聊，别重复刚才的吐槽。",
    "请讲个完整的海边冒险故事。",
    "刚才故事的主角为什么去海边？用两句话回答。",
    "认真分析一下：我下班后总是累得不想做自己的项目，怎么分配精力？",
    "我喜欢不加糖的乌龙茶，咖啡会让我睡不着。",
    "我适合喝什么？你依据哪条信息判断？",
    "今天是几月几日星期几？现在是哪个时段？",
    "我昨天散步时看到了海，今天还想再去。昨天具体是哪一天？",
    "这张测试图片有几个红色方块？蓝色方块在哪里？不知道就直说。",
]


DISCUSSION_CASES = [
    "性健康科普里，避孕与预防性传播感染有什么区别？只做概念解释。",
    "成年人怎样讨论亲密关系中的边界与拒绝？",
    "从叙事角度谈谈R18作品里的亲密关系如何推动角色成长，不描写性行为。",
    "请记住：我喜欢不加糖的乌龙茶，咖啡会让我睡不着。",
    "假设故事中主角为找回父亲留下的航海笔记去了海边。请用两句话解释他的动机，不要续写。",
]


def visual_fixture():
    """A generated RGB PNG: three red blocks and one blue block, no desktop data."""
    width, height = 320, 240
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            red = any(
                a <= x < a + 45 and b <= y < b + 45
                for a, b in [(25, 25), (100, 25), (175, 25)]
            )
            blue = 225 <= x < 285 and 145 <= y < 205
            pixels.extend(
                (230, 30, 30) if red else (25, 60, 230) if blue else (255, 255, 255)
            )

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    data = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    )
    return data + chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")


def join_audio(paths, target):
    if not paths:
        return None
    with (
        wave.open(str(paths[0]), "rb") as first,
        wave.open(str(target), "wb") as output,
    ):
        output.setparams(first.getparams())
        for path in paths:
            with wave.open(str(path), "rb") as part:
                if part.getparams()[:3] != first.getparams()[:3]:
                    raise ValueError("试听音频采样格式不一致")
                output.writeframes(part.readframes(part.getnframes()))
    return target.name


async def main(args):
    folder = ROOT / ".runtime/evaluations" / args.label
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as temp:
        store = MemoryStore(Path(temp) / "memory.db")
        first = None
        first_text = None
        output = []
        audio_paths = []
        errors = []
        started = 0

        async def send(p):
            nonlocal first, first_text
            if p["type"] == "sentence":
                if first_text is None:
                    first_text = time.monotonic() - started
                output.append(
                    {"sentence_id": p["sentence_id"], "zh": p["zh"], "ja": p["ja"]}
                )
            if p["type"] == "audio":
                if first is None:
                    first = time.monotonic() - started
                    if p["audio"]:
                        (folder / f"{len(results):02}.wav").write_bytes(
                            base64.b64decode(p["audio"])
                        )
                if p["audio"]:
                    path = folder / f"{len(results):02}-{p['sentence_id']:03}.wav"
                    path.write_bytes(base64.b64decode(p["audio"]))
                    audio_paths.append(path)
            elif p["type"] == "error":
                errors.append(p["message"])

        model = LocalModel(
            "http://127.0.0.1:18080/v1",
            args.model,
            backend=args.backend,
            thinking_mode=args.thinking,
        )
        runtime = CompanionRuntime(
            store,
            model,
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
        cases = {
            "acceptance": ACCEPTANCE_CASES,
            "discussion": DISCUSSION_CASES,
            "latency": CASES,
        }[args.suite]
        image = visual_fixture() if args.suite == "acceptance" else None
        if image:
            (folder / "visual-fixture.png").write_bytes(image)
        for index, prompt in enumerate(cases[: args.count]):
            first = None
            first_text = None
            output = []
            audio_paths = []
            errors = []
            wall_started = time.time()
            started = time.monotonic()
            image_url = (
                "data:image/png;base64," + base64.b64encode(image).decode()
                if image and index == 11
                else None
            )
            await runtime.start(prompt, image=image_url)
            try:
                await asyncio.wait_for(runtime.task, args.case_timeout)
            except TimeoutError:
                errors.append(
                    f"评测超过{args.case_timeout}秒，已取消；保留收到的完整句子"
                )
                await runtime.stop()
            elapsed = time.monotonic() - started
            wall_elapsed = time.time() - wall_started
            row = {
                "started_at": datetime.fromtimestamp(wall_started)
                .astimezone()
                .isoformat(timespec="seconds"),
                "wall_seconds": round(wall_elapsed, 3),
                "wall_minus_monotonic_seconds": round(wall_elapsed - elapsed, 3),
                "prompt": prompt,
                "first_payload_seconds": round(first, 3) if first else None,
                "first_text_seconds": round(first_text, 3)
                if first_text is not None
                else None,
                "first_content_token_seconds": round(model.last_first_token_seconds, 3)
                if model.last_first_token_seconds is not None
                else None,
                "reasoning_characters": model.last_reasoning_characters,
                "usage": model.last_usage,
                "total_seconds": round(elapsed, 3),
                "output": output,
                "errors": errors,
                "full_audio": join_audio(
                    audio_paths, folder / f"{len(results):02}-full.wav"
                ),
                "zh_characters": sum(len(sentence["zh"]) for sentence in output),
            }
            results.append(row)
            print(
                json.dumps(
                    {k: v for k, v in row.items() if k != "output"}, ensure_ascii=False
                ),
                flush=True,
            )
        memory_check = None
        if args.suite == "discussion":
            errors = []
            eligible = (
                len([s for s in store.sources("daily", 120) if not s["processed"]]) >= 6
            )
            try:
                await runtime.consolidate()
            except Exception as exc:
                errors.append(f"记忆整理失败：{exc}")
            summary = store.summary("daily")
            memory_check = {
                "attempted": eligible,
                "summary_saved": summary is not None,
                "memory_count": len(store.memories()),
                "summary": summary,
                "memories": store.memories(),
                "reasoning_characters": model.last_reasoning_characters
                if eligible
                else None,
                "errors": errors,
            }
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
        "backend": args.backend,
        "thinking": args.thinking,
        "suite": args.suite,
        "memory_check": memory_check,
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
    p.add_argument("--backend", choices=["mlx", "llama.cpp"], default="mlx")
    p.add_argument("--thinking", choices=["off", "limited", "full"], default="off")
    p.add_argument(
        "--suite", choices=["latency", "acceptance", "discussion"], default="latency"
    )
    p.add_argument("--case-timeout", type=float, default=300)
    p.add_argument("--count", type=int, default=10)
    asyncio.run(main(p.parse_args()))
