import asyncio
import io
import wave

import httpx
import pytest

from open_llm_vtuber.tts.voicevox_tts import TTSEngine


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x10\x00" * 240)
    return buffer.getvalue()


async def test_selects_sayo_and_returns_playable_wav(tmp_path):
    def handle(req):
        if req.url.path == "/speakers":
            return httpx.Response(
                200,
                json=[
                    {"name": "小夜/SAYO", "styles": [{"id": 46, "name": "ノーマル"}]}
                ],
            )
        assert req.url.params["speaker"] == "46"
        if req.url.path == "/audio_query":
            assert req.url.params["text"] == "こんにちは。"
            return httpx.Response(200, json={"speedScale": 1.0})
        assert req.url.path == "/synthesis"
        return httpx.Response(200, content=wav_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = TTSEngine(client=client, cache_dir=tmp_path)
        path = await engine.async_generate_audio("こんにちは。")
        with wave.open(path) as audio:
            assert audio.getnframes() == 240


async def test_http_failure_does_not_create_fake_audio(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await TTSEngine(client=client, cache_dir=tmp_path).async_generate_audio(
                "こんにちは"
            )
    assert list(tmp_path.iterdir()) == []


async def test_cancel_closes_inflight_request_without_writing_audio(tmp_path):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(_):
        entered.set()
        try:
            await asyncio.sleep(100)
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(
            TTSEngine(client=client, cache_dir=tmp_path).async_generate_audio(
                "こんにちは"
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    assert list(tmp_path.iterdir()) == []
