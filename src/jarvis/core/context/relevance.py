"""Deciding which of the known entities have anything to do with this request.

Deterministic on purpose: the same command and the same store always select the same
entities, which makes the selection testable and keeps a second model call out of the
path before the first one. Matching is word-based rather than substring-based, so "John"
cannot claim "Johnson", and a shorter word may match a longer one only as a prefix of at
least four characters — enough to survive Russian inflection ("Джон" in "Джона", "проект"
in "проекта") without matching unrelated words that merely begin alike.

An entity that scores nothing is not included at all. Context is what the planner reads
as fact about the user's world; filling it with everything known would make it noise.
"""

import re
import unicodedata

from jarvis.knowledge.models import Entity, EntityDraft

MIN_WORD = 3
MIN_PREFIX = 4
MAX_TERMS = 16
WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def terms(text: str) -> tuple[str, ...]:
    """Words worth matching on: normalised, deduplicated, very short ones dropped."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    found = (word for word in WORDS.findall(normalized) if len(word) >= MIN_WORD)
    return tuple(dict.fromkeys(found))[:MAX_TERMS]


def matches(label_word: str, command_word: str) -> bool:
    if label_word == command_word:
        return True
    shorter, longer = sorted((label_word, command_word), key=len)
    return len(shorter) >= MIN_PREFIX and longer.startswith(shorter)


def score(entity: EntityDraft, command: tuple[str, ...]) -> int:
    """How much of one of this entity's labels the request actually names."""
    best = 0
    for label in (entity.name, *entity.aliases):
        words = terms(label)
        if not words:
            continue
        hits = sum(1 for word in words if any(matches(word, spoken) for spoken in command))
        if hits:
            # Naming every word of a label is stronger evidence than naming one of them.
            best = max(best, hits * 2 + int(hits == len(words)))
    return best


def rank(entities: tuple[Entity, ...], command: tuple[str, ...]) -> list[tuple[int, Entity]]:
    """Scored and ordered: best match first, then most recently seen, then by name."""
    scored = [(score(entity, command), entity) for entity in entities]
    found = [pair for pair in scored if pair[0] > 0]
    found.sort(key=lambda pair: (-pair[0], -pair[1].updated, pair[1].name))
    return found
