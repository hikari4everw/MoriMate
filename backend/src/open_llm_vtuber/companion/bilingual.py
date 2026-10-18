import json
from dataclasses import dataclass

from .voices import VOICE_TONES

EMOTIONS = {"neutral", "joy", "smirk", "sadness", "anger", "surprise"}


@dataclass(frozen=True)
class Sentence:
    zh: str
    ja: str
    emotion: str = "neutral"
    voice_tone: str = "neutral"


class BilingualParser:
    """Only complete, validated NDJSON pairs can reach subtitles/TTS."""

    def __init__(self):
        self.buffer = ""

    def feed(self, chunk: str) -> list[Sentence]:
        self.buffer += chunk
        if len(self.buffer) > 16384:
            raise ValueError("双语回复过长或格式不完整，请重试")
        result = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                result.append(self._parse(line))
        return result

    def finish(self) -> list[Sentence]:
        remaining, self.buffer = self.buffer.strip(), ""
        return [self._parse(remaining)] if remaining else []

    @staticmethod
    def _parse(line: str) -> Sentence:
        try:
            obj = json.loads(line)
            zh, ja = obj["zh"], obj["ja"]
            if not all(
                isinstance(t, str) and 0 < len(t.strip()) <= 600 for t in (zh, ja)
            ):
                raise ValueError()
            if any(tag in (zh + ja).lower() for tag in ("<think", "</think", "```")):
                raise ValueError()
            emotion = obj.get("emotion", "neutral")
            tone = obj.get("voice_tone", "neutral")
            return Sentence(
                zh.strip(), ja.strip(),
                emotion if isinstance(emotion, str) and emotion in EMOTIONS else "neutral",
                tone if isinstance(tone, str) and tone in VOICE_TONES else "neutral",
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("模型未输出完整的中日双语短句，请重试") from exc
