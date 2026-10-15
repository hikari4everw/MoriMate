import asyncio
import tempfile
from pathlib import Path

import httpx

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    def __init__(
        self,
        base_url="http://127.0.0.1:50021",
        speaker_name="小夜/SAYO",
        style_name="ノーマル",
        cache_dir="cache",
        client=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.speaker_name = speaker_name
        self.style_name = style_name
        self.cache_dir = Path(cache_dir)
        self.client = client
        self.speaker_id = None

    async def _synthesize(self, client, text):
        if self.speaker_id is None:
            response = await client.get(f"{self.base_url}/speakers")
            response.raise_for_status()
            for speaker in response.json():
                if speaker["name"] == self.speaker_name:
                    for style in speaker["styles"]:
                        if style["name"] == self.style_name:
                            self.speaker_id = style["id"]
            if self.speaker_id is None:
                raise ValueError(
                    f"VOICEVOX 未安装音色 {self.speaker_name} / {self.style_name}"
                )
        query = await client.post(
            f"{self.base_url}/audio_query",
            params={"text": text, "speaker": self.speaker_id},
        )
        query.raise_for_status()
        response = await client.post(
            f"{self.base_url}/synthesis",
            params={"speaker": self.speaker_id},
            json=query.json(),
        )
        response.raise_for_status()
        if not response.content.startswith(b"RIFF"):
            raise ValueError("VOICEVOX 未返回 WAV 音频")
        return response.content

    async def async_generate_audio(self, text: str, file_name_no_ext=None) -> str:
        if self.client:
            data = await self._synthesize(self.client, text)
        else:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                data = await self._synthesize(client, text)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".wav", dir=self.cache_dir, delete=False
        ) as f:
            f.write(data)
            return f.name

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        return asyncio.run(self.async_generate_audio(text, file_name_no_ext))
