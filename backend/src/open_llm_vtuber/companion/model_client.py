"""Request-scoped model events; legacy text wrappers keep the speech loop small."""
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass
class GenerationRequest:
    messages: list[dict]
    max_tokens: int = 400
    purpose: str = "chat"
    reasoning_budget: int = 0


@dataclass
class ModelEvent:
    kind: str
    text: str = ""
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None


@dataclass
class GenerationResult:
    text: str
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None


class ModelClient(Protocol):
    def stream_events(self, request: GenerationRequest) -> AsyncIterator[ModelEvent]: ...
