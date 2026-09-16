"""Shared constants for the API Engineer node package."""
from __future__ import annotations

from typing import Final, Set

RANK_POOL_SIZE: Final[int] = 10
MAX_SELECTION_CANDIDATES: Final[int] = 30
BEAM_K: Final[int] = 5
BEAM_G: Final[int] = 3
BEAM_P: Final[int] = 3

_QUERY_STOPWORDS: Final[Set[str]] = {
    "jaka", "jaki", "jakie", "była", "był", "było", "były", "jest", "są",
    "być", "w", "na", "dla", "po", "z", "do", "od", "roku", "lat",
    "latach", "ile", "wynosi", "wynosiła", "wyniosła", "wyniosły", "podaj",
    "pokaż", "chcę", "chciałbym", "chciałabym", "proszę",
}

_FOLLOWUP_STOPWORDS: Final[Set[str]] = _QUERY_STOPWORDS | {
    "what", "about", "how", "co", "z", "w", "a", "the", "for", "in", "and", "jak"
}