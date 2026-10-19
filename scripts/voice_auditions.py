"""Generate local Roxy comparisons against the already-running voice engine."""
import asyncio
import html
import json
import shutil
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from open_llm_vtuber.companion.voices import VoiceCatalog, VoiceSynthesizer
import httpx

TEXTS = {
    "greeting": ("普通问候", "你好，今天过得怎么样？", "こんにちは。今日はどんな一日でしたか。"),
    "serious": ("认真交流", "不用着急，慢慢说吧。我会好好听的。", "急がなくて大丈夫です。ゆっくり話してください。ちゃんと聞いています。"),
}
LABELS = {"lazy": "慵懒（当前默认同一参考）", "aggrieved": "委屈", "shy": "害羞", "combat": "战斗", "affectionate": "撒娇", "relieved": "欣慰", "self_deprecating": "自嘲", "reproachful": "责备"}


def render(samples, references):
    cards = []
    for tone, label in LABELS.items():
        ref = references[tone]
        clips = []
        for row in samples:
            if row["tone"] == tone:
                kind, zh, ja = TEXTS[row["kind"]]
                clips.append(f'<h3>{kind}</h3><p>{zh}<br>{ja}</p><audio controls preload="none" src="{row["file"]}"></audio><p>合成 {row["synthesis_seconds"]} 秒 · 音频 {row["audio_seconds"]} 秒</p>')
        cards.append(f'<section><h2>{label}</h2>{"".join(clips)}<details><summary>核对原始参考录音（文本尚待听校）</summary><audio controls preload="none" src="reference-{tone}.wav"></audio><p>{html.escape(ref["text"])}</p></details></section>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Roxy 日常语气试听</title><style>body{font:16px/1.65 system-ui;background:#181724;color:#eee;max-width:1000px;margin:32px auto;padding:0 20px}section{background:#272536;padding:24px;border-radius:16px;margin:20px 0}audio{width:100%;max-width:600px}h2{color:#c5b7ff}summary{cursor:pointer}a{color:#c5b7ff}</style><h1>Roxy 日常语气试听</h1><p>八类参考，每类都是同一组问候和认真交流文本，语速与采样参数一致；没有更换人物模型。默认 neutral 与慵懒 lazy 使用相同参考，不是第九种效果。参考文本尚未听校，不能把成功出声当作语气验收。</p><p>听完后回到桌宠的“模型与声音设置”，选择“日常参考”即可保存固定语气。当前页面只播放本机文件，不调用云端。</p><a href="/">返回桌宠</a>''' + ''.join(cards) + '''<script>document.addEventListener('play',e=>{for(const a of document.querySelectorAll('audio'))if(a!==e.target)a.pause()},true)</script></html>'''


async def main():
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        state = (await client.get("http://127.0.0.1:12393/health")).json()
    if state.get("voice_phase") != "ready" or state.get("active_voice_key") != "roxy":
        raise RuntimeError("请先在桌宠中切换到就绪的 Roxy；脚本不会切换或接管服务")
    catalog = VoiceCatalog(ROOT)
    voice = VoiceSynthesizer(catalog, "roxy")  # Bypass daily tone policy for comparisons only.
    destination = ROOT / ".runtime/voice-auditions"
    destination.mkdir(exist_ok=True, parents=True)
    references = catalog.profile("roxy")["references"]
    samples = []
    for tone in LABELS:
        shutil.copyfile(catalog.path(references[tone]["audio"]), destination / f"reference-{tone}.wav")
        for kind, (_, _, ja) in TEXTS.items():
            started = time.monotonic()
            path = Path(await voice.synthesize(ja, tone))
            elapsed = time.monotonic() - started
            name = f"{tone}-{kind}.wav"
            shutil.move(path, destination / name)
            with wave.open(str(destination / name)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            row = {"tone": tone, "kind": kind, "file": name, "synthesis_seconds": round(elapsed, 3), "audio_seconds": round(duration, 3)}
            samples.append(row)
            print(json.dumps(row), flush=True)
    (destination / "report.json").write_text(json.dumps({"samples": samples, "sampling": {"speed": 1.0, "top_k": 15, "top_p": 1.0, "temperature": 1.0}, "reference_transcripts_verified": False}, ensure_ascii=False, indent=2))
    (destination / "index.html").write_text(render(samples, references))
    print(str(destination / "index.html"), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
