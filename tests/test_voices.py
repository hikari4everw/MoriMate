import asyncio
import io
import json
import wave

import httpx
import pytest

from open_llm_vtuber.companion.voices import VoiceCatalog, VoiceSynthesizer


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(32000)
        audio.writeframes(b"\x10\x00" * 320)
    return buffer.getvalue()


def catalog(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    spec = {
        "default_voice": "roxy",
        "fallback_voice": "mao",
        "engines": {
            "gpt-sovits": {
                "label": "GPT-SoVITS",
                "port": 9880,
                "directory": "gsv",
                "python": "gsv/python",
            },
            "aivis": {"label": "AivisSpeech", "port": 10101},
        },
        "voices": {
            "roxy": {
                "engine": "gpt-sovits",
                "label": "Roxy",
                "gpt_weights": "gpt.ckpt",
                "sovits_weights": "sovits.pth",
                "references": {
                    "neutral": {"audio": "lazy.wav", "text": "こんにちは。"},
                    "shy": {"audio": "shy.wav", "text": "ありがとう。"},
                },
            },
            "mao": {
                "engine": "aivis",
                "label": "まお",
                "speaker_name": "まお",
                "style_name": "ノーマル",
            },
        },
    }
    path = tmp_path / "config/voices.json"
    path.write_text(json.dumps(spec))
    return VoiceCatalog(tmp_path)


async def test_sovits_selects_reference_and_validates_wav(tmp_path):
    voices = catalog(tmp_path)

    def handle(request):
        body = json.loads(request.content)
        assert request.method == "POST" and request.url.path == "/"
        assert body["refer_wav_path"] == str(tmp_path / "shy.wav")
        assert body["prompt_text"] == "ありがとう。"
        assert body["prompt_language"] == body["text_language"] == "ja"
        assert body["text"] == "おはよう。"
        return httpx.Response(200, content=wav_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        synth = VoiceSynthesizer(voices, "roxy", client=client)
        first = await synth.synthesize("おはよう。", "shy")
        second = await synth.synthesize("おはよう。", "shy")
    assert first != second
    with wave.open(first) as audio:
        assert audio.getnframes() == 320


@pytest.mark.parametrize(
    "data",
    [b'{"code":400}', b"RIFFbroken", b"RIFF\x00\x00\x00\x00WAVE", wav_bytes()[:-2]],
    ids=["json", "bad-header", "no-data", "truncated"],
)
async def test_bad_audio_never_creates_output(tmp_path, data):
    voices = catalog(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data))
    ) as client:
        with pytest.raises(ValueError, match="WAV"):
            await VoiceSynthesizer(voices, "roxy", client=client).synthesize(
                "はい。", "neutral"
            )
    assert not list((tmp_path / ".runtime/audio").glob("*.wav"))


async def test_missing_tone_uses_default_and_cancel_never_writes(tmp_path):
    voices = catalog(tmp_path)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def handle(request):
        assert json.loads(request.content)["refer_wav_path"] == str(
            tmp_path / "lazy.wav"
        )
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(
            VoiceSynthesizer(voices, "roxy", client=client).synthesize(
                "はい。", "combat"
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
    assert not list((tmp_path / ".runtime/audio").glob("*.wav"))


async def test_aivis_dynamic_style_and_missing_style_fallback(tmp_path):
    voices = catalog(tmp_path)
    seen = []

    def handle(request):
        if request.url.path == "/speakers":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "まお",
                        "styles": [
                            {"name": "ノーマル", "id": 111},
                            {"name": "あまあま", "id": 222},
                        ],
                    }
                ],
            )
        seen.append(request.url.params["speaker"])
        if request.url.path == "/audio_query":
            return httpx.Response(
                200, json={"kana": "そのまま", "tempoDynamicsScale": 1.0}
            )
        assert json.loads(request.content)["kana"] == "そのまま"
        return httpx.Response(200, content=wav_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        synth = VoiceSynthesizer(voices, "mao", client=client)
        await synth.synthesize("はい。", "shy")
        await synth.synthesize("はい。", "lazy")
    assert seen == ["222", "222", "111", "111"]


def test_config_rejects_duplicate_ports_and_missing_default_reference(tmp_path):
    voices = catalog(tmp_path)
    path = tmp_path / "config/voices.json"
    spec = voices.data
    spec["voices"]["roxy"]["references"].pop("neutral")
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="neutral"):
        VoiceCatalog(tmp_path)


def test_launch_checks_assets_and_uses_explicit_weight_pair(tmp_path):
    voices = catalog(tmp_path)
    with pytest.raises(RuntimeError, match="不存在"):
        voices.launch("roxy")
    for name in [
        "gsv/python",
        "gsv/api.py",
        "gpt.ckpt",
        "sovits.pth",
        "lazy.wav",
        "shy.wav",
    ]:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.touch()
    command, directory, env = voices.launch("roxy")
    assert directory == tmp_path / "gsv"
    assert command[command.index("-g") + 1] == str(tmp_path / "gpt.ckpt")
    assert command[command.index("-s") + 1] == str(tmp_path / "sovits.pth")
    assert "-fp" in command and command[command.index("-a") + 1] == "127.0.0.1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
