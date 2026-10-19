from dataclasses import dataclass


@dataclass
class MemoryCandidate:
    kind: str
    text: str
    source_ids: list[int]
    evidence: str
    previous_id: int | None = None
    explicit_change: bool = False
    event_time: str | None = None
