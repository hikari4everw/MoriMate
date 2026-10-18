import io
import json
import os
import tempfile
import wave
from pathlib import Path

import httpx

VOICE_TONES = {
    "neutral",
    "aggrieved",
    "shy",
    "lazy",
    "combat",
    "affectionate",
    "relieved",
    "self_deprecating",
    "reproachful",
}
AIVIS_STYLES = {
    "lazy": "おちつき",
    "shy": "あまあま",
    "affectionate": "あまあま",
    "aggrieved": "せつなめ",
    "relieved": "ふつー",
}


class VoiceCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = json.loads((self.root / "config/voices.json").read_text())
        self.engines = self.data["engines"]
        self.voices = self.data["voices"]
        self.default = self.data["default_voice"]
        self.fallback = self.data["fallback_voice"]
        if self.default not in self.voices or self.fallback not in self.voices:
            raise ValueError("默认或回退角色未登记")
        ports = [engine["port"] for engine in self.engines.values()]
        if any(
            type(port) is not int or not 1024 <= port <= 65535 for port in ports
        ) or len(set(ports)) != len(ports):
            raise ValueError("语音引擎端口无效或重复")
        for key, voice in self.voices.items():
            engine = voice["engine"]
            if engine not in self.engines or engine not in {
                "gpt-sovits",
                "aivis",
                "voicevox",
            }:
                raise ValueError(f"角色 {key} 的引擎无效")
            if engine == "gpt-sovits":
                references = voice["references"]
                if "neutral" not in references:
                    raise ValueError(f"角色 {key} 缺少 neutral 默认参考音频")
                for tone, reference in references.items():
                    if (
                        tone not in VOICE_TONES
                        or not reference.get("audio")
                        or not isinstance(reference.get("text"), str)
                        or not reference["text"].strip()
                    ):
                        raise ValueError(f"角色 {key} 的参考音频或文本无效：{tone}")
        if self.voices[self.fallback]["engine"] != "aivis":
            raise ValueError("回退角色必须属于 AivisSpeech")

    def path(self, value):
        return (self.root / value).resolve()

    def profile(self, key):
        if not isinstance(key, str) or key not in self.voices:
            raise ValueError("语音角色选择无效")
        return self.voices[key]

    def engine(self, key):
        return self.engines[self.profile(key)["engine"]]

    def label(self, key):
        profile = self.profile(key)
        return f"{self.engine(key)['label']}:{profile['label']}"

    def available(self, active=None):
        return [
            {
                "key": key,
                "label": voice["label"],
                "engine": voice["engine"],
                "engine_label": self.engine(key)["label"],
            }
            for key, voice in self.voices.items()
            if not self.engine(key).get("hidden") or key == active
        ]

    def launch(self, key):
        profile, engine = self.profile(key), self.engine(key)
        env = os.environ.copy()
        if profile["engine"] == "gpt-sovits":
            directory = self.path(engine["directory"])
            python = self.path(engine["python"])
            gpt, sovits = (
                self.path(profile["gpt_weights"]),
                self.path(profile["sovits_weights"]),
            )
            required = [python, directory / "api.py", gpt, sovits]
            required += [
                self.path(ref["audio"]) for ref in profile["references"].values()
            ]
            for path in required:
                if not path.is_file():
                    raise RuntimeError(f"GPT-SoVITS 资源不存在：{path}")
            env.update(
                PATH=str(python.parent) + os.pathsep + env.get("PATH", ""),
                DYLD_LIBRARY_PATH=str(directory / "runtime/lib")
                + os.pathsep
                + env.get("DYLD_LIBRARY_PATH", ""),
                PYTHONDONTWRITEBYTECODE="1",
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                MPLCONFIGDIR=str(self.root / ".runtime/matplotlib"),
                XDG_CACHE_HOME=str(self.root / ".runtime/tts-cache"),
            )
            return (
                [
                    str(python),
                    "api.py",
                    "-a",
                    "127.0.0.1",
                    "-p",
                    str(engine["port"]),
                    "-d",
                    "cpu",
                    "-fp",
                    "-g",
                    str(gpt),
                    "-s",
                    str(sovits),
                    "-sm",
                    "close",
                    "-mt",
                    "wav",
                ],
                directory,
                env,
            )
        if profile["engine"] == "aivis":
            candidates = sorted((self.root / ".runtime/aivis").rglob("run"))
            if not candidates:
                raise RuntimeError(
                    "AivisSpeech 不存在，请先运行 scripts/setup.sh --voice mao"
                )
            executable = candidates[0]
        else:
            executable = self.root / ".runtime/voicevox/macos-arm64/run"
            if not executable.is_file():
                raise RuntimeError(
                    "VOICEVOX 不存在，请先运行 scripts/setup.sh --voice sayo"
                )
        command = [
            str(executable),
            "--host",
            "127.0.0.1",
            "--port",
            str(engine["port"]),
        ]
        if profile["engine"] == "aivis":
            command.append("--disable_sentry")
        return command, self.root, env


class VoiceSynthesizer:
    def __init__(self, catalog, key, *, client=None):
        self.catalog = catalog
        self.profile = catalog.profile(key)
        self.base_url = f"http://127.0.0.1:{catalog.engine(key)['port']}"
        self.cache_dir = catalog.root / ".runtime/audio"
        self.client = client
        self.styles = None

    async def _audio(self, client, text, tone):
        if self.profile["engine"] == "gpt-sovits":
            reference = self.profile["references"].get(
                tone, self.profile["references"]["neutral"]
            )
            response = await client.post(
                self.base_url + "/",
                json={
                    "refer_wav_path": str(self.catalog.path(reference["audio"])),
                    "prompt_text": reference["text"],
                    "prompt_language": "ja",
                    "text": text,
                    "text_language": "ja",
                    "speed": 1.0,
                },
            )
        else:
            if self.styles is None:
                speakers = await client.get(self.base_url + "/speakers")
                speakers.raise_for_status()
                for speaker in speakers.json():
                    if speaker["name"] == self.profile["speaker_name"]:
                        self.styles = {
                            style["name"]: style["id"] for style in speaker["styles"]
                        }
                        break
                if self.styles is None:
                    raise ValueError(f"未安装音色 {self.profile['speaker_name']}")
            default = self.profile.get("style_name", "ノーマル")
            desired = (
                AIVIS_STYLES.get(tone, default)
                if self.profile["engine"] == "aivis"
                else default
            )
            speaker_id = self.styles.get(desired, self.styles.get(default))
            if speaker_id is None:
                raise ValueError(f"音色缺少默认风格 {default}")
            query = await client.post(
                self.base_url + "/audio_query",
                params={"text": text, "speaker": speaker_id},
            )
            query.raise_for_status()
            response = await client.post(
                self.base_url + "/synthesis",
                params={"speaker": speaker_id},
                json=query.json(),
            )
        response.raise_for_status()
        data = response.content
        try:
            with wave.open(io.BytesIO(data)) as audio:
                if (
                    audio.getnframes() <= 0
                    or audio.getnchannels() <= 0
                    or audio.getframerate() <= 0
                    or len(audio.readframes(audio.getnframes()))
                    != audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                ):
                    raise ValueError()
        except (wave.Error, EOFError, ValueError) as exc:
            raise ValueError("语音引擎未返回有效 WAV 音频") from exc
        return data

    async def synthesize(self, text, tone="neutral"):
        if not isinstance(tone, str) or tone not in VOICE_TONES:
            tone = "neutral"
        if self.client:
            data = await self._audio(self.client, text, tone)
        else:
            async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                data = await self._audio(client, text, tone)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".wav", dir=self.cache_dir, delete=False
        ) as output:
            output.write(data)
            return output.name

    async def async_generate_audio(self, text):
        return await self.synthesize(text)
