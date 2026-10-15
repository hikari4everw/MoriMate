import re


class StableText:
    def __init__(self, settle_seconds: float = 0.8):
        self.settle_seconds = settle_seconds
        self.reset()

    def reset(self):
        self.candidate = ""
        self.since = 0.0
        self.last = ""

    def feed(self, text: str, now: float, confidence: float = 1) -> str | None:
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text or confidence < 0.55:
            self.candidate = ""
            return None
        if text != self.candidate:
            self.candidate, self.since = text, now
            return None
        if text != self.last and now - self.since >= self.settle_seconds:
            self.last = text
            return text
        return None


class CommentGate:
    COOLDOWN = {"quiet": 180, "natural": 45, "active": 15}

    def __init__(self, intensity: str = "natural"):
        self.intensity = intensity
        self.enabled = True
        self.last_spoken = float("-inf")

    def allowed(self, now: float, observed_at: float, busy: bool) -> bool:
        return (
            self.enabled
            and not busy
            and 0 <= now - observed_at <= 12
            and now - self.last_spoken >= self.COOLDOWN[self.intensity]
        )

    def spoke(self, now: float):
        self.last_spoken = now
