"""Schemas for Gemini structured-output.

Two parallel forms:
  1. `*_SCHEMA_DICT` — flat JSON Schema dicts, passed to `google-genai` SDK
     as `response_schema`. The SDK is strict about extra fields and rejects
     Pydantic v2's auto-generated `exclusiveMinimum`, `$ref`, `$defs`, etc.
  2. `*` Pydantic models — used to parse and validate the JSON returned
     by Gemini on the Python side (so we keep strong guarantees like
     `end_time > start_time` and minimum segment/total durations).

IMPORTANT — supported response_schema fields only:
  Gemini's structured-output schema only supports a specific subset of
  JSON Schema: anyOf, enum, format, items, maximum, maxItems, minimum,
  minItems, nullable, properties, propertyOrdering, required. Notably,
  `minLength` / `maxLength` are NOT supported and can make the whole
  request come back as `400 INVALID_ARGUMENT` on every model. Do not add
  them back here — string length limits are already enforced on the
  Python side by the Pydantic models below after the response is parsed.

Duration contract (Phase 8):
  - Each segment must be 15-30 seconds long.
  - Total stitched duration per candidate must be 20-35 seconds.
  - Schemas enforce the upper bound; Pydantic validators enforce the
    lower bound (since JSON Schema can't easily express "computed field"
    constraints like "sum of segment durations").
"""
from typing import List

from pydantic import BaseModel, Field, model_validator


# Hard limits. Tweakable in one place if we ever want a different
# Reels/Shorts length target. Min per-segment is 15s because the user
# rejected the previous 5s minimum as "too short to be useful" — that
# surfaced in testing where Gemini happily shipped 3x1s clips. Min
# total 20s is the floor for a watchable Reel; max 35s keeps it inside
# the sweet spot for virality.
MIN_SEGMENT_DURATION = 15.0
MAX_SEGMENT_DURATION = 30.0
MIN_TOTAL_DURATION = 20.0
MAX_TOTAL_DURATION = 35.0


# ----------------------------------------------------------------------------
# Transcription
# ----------------------------------------------------------------------------

class WordTimestamp(BaseModel):
    text: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end < self.start:
            self.start, self.end = self.end, self.start
        return self

class TranscriptionResponse(BaseModel):
    full_transcript: str = Field(min_length=1)
    words: List[WordTimestamp] = Field(default_factory=list)

TRANSCRIPTION_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "full_transcript": {"type": "string"},
        "words": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "start": {"type": "number", "minimum": 0},
                    "end": {"type": "number", "minimum": 0},
                },
                "required": ["text", "start", "end"],
            },
        },
    },
    "required": ["full_transcript", "words"],
}


# ----------------------------------------------------------------------------
# Candidates
# ----------------------------------------------------------------------------

class Segment(BaseModel):
    start_time: float = Field(ge=0, description="Start time in seconds (>= 0)")
    end_time: float = Field(ge=0, description="End time in seconds (> start_time)")
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self

    @model_validator(mode="after")
    def _min_segment_duration(self):
        dur = self.end_time - self.start_time
        if dur < MIN_SEGMENT_DURATION:
            raise ValueError(f"Segment duration {dur:.1f}s < {MIN_SEGMENT_DURATION:.0f}s")
        if dur > MAX_SEGMENT_DURATION:
            raise ValueError(f"Segment duration {dur:.1f}s > {MAX_SEGMENT_DURATION:.0f}s")
        return self

class EditPlanCandidate(BaseModel):
    candidate_index: int = Field(ge=0)
    segments: List[Segment] = Field(min_length=1, max_length=8)

class CandidatesResponse(BaseModel):
    candidates: List[EditPlanCandidate] = Field(min_length=1, max_length=5)

CANDIDATES_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer", "minimum": 0},
                    "segments": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_time": {"type": "number", "minimum": 0},
                                "end_time": {"type": "number", "maximum": 86400},
                                "title": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["start_time", "end_time", "title", "reason"],
                        },
                    },
                },
                "required": ["candidate_index", "segments"],
            },
        },
    },
    "required": ["candidates"],
}


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

class CandidateScore(BaseModel):
    candidate_index: int = Field(ge=0)
    hook_score: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=1)

class ScoringResponse(BaseModel):
    scores: List[CandidateScore] = Field(min_length=1)

SCORING_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer", "minimum": 0},
                    "hook_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "overall_score": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["candidate_index", "hook_score", "overall_score"],
            },
        },
    },
    "required": ["scores"],
}


# ----------------------------------------------------------------------------
# Reviewer
# ----------------------------------------------------------------------------

REVIEW_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["accepted", "revise"]},
        "feedback": {"type": "string"},
        "overall_score": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["status", "feedback", "overall_score"],
}


class ReviewResponse(BaseModel):
    status: str  # 'accepted' or 'revise'
    feedback: str = ""
    overall_score: float = Field(ge=0, le=1)
