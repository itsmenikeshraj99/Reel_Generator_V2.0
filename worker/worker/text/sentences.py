"""Build a numbered sentence index from a word-timed transcript.

Later steps refer to parts of the video by sentence id ("S12"), never by
seconds, so a reel always starts and ends on whole sentences.

Rules (all pure Python, deterministic):
1. A word ending in . ? ! ... or the Hindi danda ends a sentence
   (closing quotes/brackets after it are ignored; common abbreviations
   such as "Dr." do not count).
2. A pause of PAUSE_SPLIT_S or more between two words also ends one, so
   transcripts without punctuation still split.
3. A stretch longer than MAX_SENTENCE_S is split at its longest pause.
4. Fragments under MIN_WORDS words are merged into the nearest neighbour
   (gap at most MERGE_MAX_GAP_S, result at most MAX_SENTENCE_S).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

PAUSE_SPLIT_S = 0.7
MAX_SENTENCE_S = 20.0
MIN_WORDS = 4
MERGE_MAX_GAP_S = 2.0

_TERMINATORS = ".?!…।॥"  # . ? ! ellipsis danda double-danda
_CLOSERS = "\"'”’)]}»›"
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "e.g", "i.e"}


@dataclass
class Sentence:
    id: str
    start: float
    end: float
    text: str
    first_word: int  # index into the input word list
    last_word: int   # inclusive

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "start": self.start, "end": self.end, "text": self.text,
            "first_word": self.first_word, "last_word": self.last_word,
        }


_Word = Tuple[str, float, float]  # (text, start, end)


def _normalize(w: Any) -> _Word:
    if isinstance(w, dict):
        return str(w.get("text", w.get("word", ""))), float(w["start"]), float(w["end"])
    return str(w.text), float(w.start), float(w.end)


def _ends_sentence(text: str) -> bool:
    t = text.rstrip(_CLOSERS)
    if not t or t[-1] not in _TERMINATORS:
        return False
    if t.endswith(".") and not t.endswith(".."):
        if t.lower().rstrip(".") in _ABBREVIATIONS:
            return False
    return True


def _gap(words: List[_Word], i: int) -> float:
    """Silence between word i and word i+1."""
    return words[i + 1][1] - words[i][2]


def _duration(words: List[_Word], group: Tuple[int, int]) -> float:
    return words[group[1]][2] - words[group[0]][1]


def _split_long(words: List[_Word], group: Tuple[int, int]) -> List[Tuple[int, int]]:
    first, last = group
    if last == first or _duration(words, group) <= MAX_SENTENCE_S:
        return [group]
    gaps = [(_gap(words, i), i) for i in range(first, last)]
    best = max(g for g, _ in gaps)
    middle = (first + last) / 2
    cut = min((i for g, i in gaps if best - g < 1e-9), key=lambda i: abs(i + 0.5 - middle))
    return _split_long(words, (first, cut)) + _split_long(words, (cut + 1, last))


def _merge_short(words: List[_Word], groups: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    groups = list(groups)
    while True:
        for k, (first, last) in enumerate(groups):
            if last - first + 1 >= MIN_WORDS:
                continue
            options = []
            if k > 0:
                prev = groups[k - 1]
                options.append((words[first][1] - words[prev[1]][2], k - 1, (prev[0], last)))
            if k + 1 < len(groups):
                nxt = groups[k + 1]
                options.append((words[nxt[0]][1] - words[last][2], k, (first, nxt[1])))
            options = [o for o in options
                       if o[0] <= MERGE_MAX_GAP_S and _duration(words, o[2]) <= MAX_SENTENCE_S]
            if not options:
                continue
            _, at, merged = min(options, key=lambda o: o[0])  # ties keep the earlier option
            groups[at:at + 2] = [merged]
            break
        else:
            return groups


def build_sentences(raw_words: Sequence[Any]) -> List[Sentence]:
    """Group word-timed transcript entries into numbered sentences.

    Accepts objects with text/start/end (worker.asr.Word) or dicts with
    the keys stored in `transcripts.words`.
    """
    words = [_normalize(w) for w in raw_words]
    if not words:
        return []

    groups: List[Tuple[int, int]] = []
    begin = 0
    for i in range(len(words)):
        last = i == len(words) - 1
        if last or _ends_sentence(words[i][0]) or _gap(words, i) >= PAUSE_SPLIT_S:
            groups.append((begin, i))
            begin = i + 1

    split: List[Tuple[int, int]] = []
    for g in groups:
        split.extend(_split_long(words, g))

    merged = _merge_short(words, split)

    return [
        Sentence(
            id=f"S{n}",
            start=words[first][1],
            end=words[last][2],
            text=" ".join(w[0] for w in words[first:last + 1]),
            first_word=first,
            last_word=last,
        )
        for n, (first, last) in enumerate(merged, start=1)
    ]
