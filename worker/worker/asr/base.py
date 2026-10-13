"""Shared types for speech-to-text providers.

Every provider returns a `TranscriptResult` in the same shape, so the
rest of the pipeline never depends on which vendor produced the text.
Word entries use the same keys the `transcripts.words` column already
stores: {text, start, end}.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol


class ASRError(Exception):
    """Raised when a provider cannot produce a usable transcript."""


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    text: str
    start: float
    end: float


@dataclass
class TranscriptResult:
    provider: str
    full_text: str
    words: List[Word] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0

    def words_for_db(self) -> List[Dict[str, Any]]:
        """Words in the shape stored in `transcripts.words`."""
        return [{"text": w.text, "start": w.start, "end": w.end} for w in self.words]


class ASRProvider(Protocol):
    name: str

    async def transcribe(self, video_path: str) -> TranscriptResult:
        ...
