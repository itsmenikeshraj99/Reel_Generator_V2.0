"""Does a sentence make a clean start for a reel?

A reel should not open mid-thought. Sentences that begin with a filler or
a joining word ("yeah", "so", "and", Hindi "तो", "और", ...) or with a
lowercase letter (a continuation) are not clean starts.
"""

_CONTINUATIONS = {
    # English fillers, joiners and answers
    "and", "but", "so", "because", "cause", "yeah", "yes", "yep", "no", "nope", "okay", "ok", "right", "well",
    "like", "which", "that", "then", "also", "or", "um", "uh", "hmm", "mm", "mhm", "oh", "anyway", "plus",
    "though", "however", "still", "actually",
    # Hindi fillers and joiners
    "तो", "और", "लेकिन", "पर", "क्योंकि", "हाँ", "हां", "अच्छा", "ठीक", "यानी", "फिर", "जो",
}
_PUNCTUATION = ".,!?;:\"'“”‘’()[]-—…"


def starts_cleanly(text: str) -> bool:
    """False if the sentence reads like the middle of a thought."""
    text = (text or "").strip().lstrip(_PUNCTUATION + " ")
    if not text:
        return True
    first = text.split()[0].strip(_PUNCTUATION).lower()
    if first in _CONTINUATIONS:
        return False
    head = text[0]
    return not (head.isascii() and head.isalpha() and head.islower())
