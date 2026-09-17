"""Confirming a serious action by voice: the owner repeats one detail from the snapshot.

Voice is the weakest confirmation channel there is. Recognition misfires, a television
speaks, another person is in the room, a recording is played back. Accepting «да» would put
that channel in front of the only actions still expensive enough to be confirmed at all.

So the answer is never a general yes. The assistant names one control detail taken from the
snapshot itself — the file being thrown away, the subject of the letter being sent — and only
that word, said as a short answer, counts. Nothing here mints a token: this module reads a
transcript and returns a verdict, and the UI-owned authority still issues the approval.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from jarvis.permissions.approvals import Action
from jarvis.permissions.policies import Risk
from jarvis.voice.contracts import Transcript

# Recognition is a local Russian model, so a control detail is useful only when it is a word
# that model can return. A Latin address would fail every time and quietly strand the owner.
WORD = re.compile(r"[а-я][а-я-]{3,39}")
# An answer is short. The assistant's own prompt is not, so the prompt itself — picked up by
# the microphone, or repeated by whatever is playing in the room — cannot pass as the answer.
MAX_ANSWER_WORDS = 4
EDGES = "«»\"'`.,!?:;…()[]"


class Confirmation(StrEnum):
    CONFIRMED = "confirmed"
    REFUSED = "refused"
    MISMATCH = "mismatch"
    UNCERTAIN = "uncertain"
    UNHEARD = "unheard"
    STOPPED = "stopped"


CONFIRMATION_TEXT = {
    Confirmation.CONFIRMED: "Подтверждено голосом.",
    Confirmation.REFUSED: "Отменено голосом. Подтверждение не выдано.",
    Confirmation.MISMATCH: "Услышано другое слово. Произнесите контрольное слово или нажмите "
    "кнопку подтверждения.",
    Confirmation.UNCERTAIN: "Распознавание неуверенное; подтверждение не выдано. Повторите "
    "слово или нажмите кнопку.",
    Confirmation.UNHEARD: "Слово не услышано. Повторите или нажмите кнопку подтверждения.",
    Confirmation.STOPPED: "Подтверждение голосом остановлено. Кнопка по-прежнему доступна.",
}


@dataclass(frozen=True)
class ControlDetail:
    """One fact of this exact action, said aloud and shown on screen at the same time."""

    action: str  # The application's own description of the action, never tool output.
    label: str  # What the word is, so the owner hears where it came from.
    word: str  # The word itself, taken from the snapshot.

    @property
    def request(self) -> str:
        return f"{self.action}. Для подтверждения произнесите {self.label}: {self.word}."


def normalise(word: str) -> str:
    return word.strip(EDGES).casefold().replace("ё", "е")


def _text(payload: object, *path: str) -> str:
    """Read one string out of the snapshot; anything else reads as absent."""
    value = payload
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""


def _file_name(payload: object) -> str:
    path = _text(payload, "path").replace("\\", "/")
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


# A tool that is not here has no voice channel at all, and the button stays the only way to
# confirm it. That is the safe direction: a new CONFIRM tool gains voice only when someone
# decides which of its fields the owner should be repeating.
DETAILS: dict[str, tuple[str, str, Callable[[object], str]]] = {
    "files.recycle": (
        "Файл будет перемещён в корзину",
        "слово из имени файла",
        _file_name,
    ),
    "outlook.send": (
        "Письмо будет отправлено",
        "слово из темы письма",
        lambda payload: _text(payload, "message", "subject"),
    ),
    "calendar.create": (
        "Встреча будет создана, участники получат приглашение",
        "слово из темы встречи",
        lambda payload: _text(payload, "event", "subject"),
    ),
    "calendar.update": (
        "Встреча будет изменена, участники получат обновление",
        "слово из темы встречи",
        lambda payload: _text(payload, "event", "subject"),
    ),
    "calendar.cancel": (
        "Встреча будет отменена, участники получат уведомление",
        "слово из вашего комментария",
        lambda payload: _text(payload, "comment"),
    ),
    "local.append_message": (
        "Тестовое сообщение будет добавлено в локальный ящик",
        "слово из темы сообщения",
        lambda payload: _text(payload, "subject"),
    ),
}


def control_detail(action: Action) -> ControlDetail | None:
    """The detail of this snapshot the owner can repeat, or nothing when there is none.

    Nothing means the voice channel is simply not offered for that action; the dialog and
    its button are unchanged.
    """
    if action.risk is not Risk.CONFIRM:
        return None
    described = DETAILS.get(action.tool)
    if described is None:
        return None
    try:
        payload = json.loads(action.payload)
    except ValueError:  # pragma: no cover - the engine normalised this payload itself.
        return None
    description, label, pick = described
    candidates = [word.strip(EDGES) for word in pick(payload).split()]
    speakable = [word for word in candidates if WORD.fullmatch(normalise(word))]
    if not speakable:
        return None
    return ControlDetail(description, label, max(speakable, key=len))


def check(detail: ControlDetail, transcript: Transcript) -> Confirmation:
    """Judge one spoken answer. A verdict is not an approval; the authority still issues it."""
    if transcript.is_cancel:
        return Confirmation.REFUSED
    if transcript.uncertain:
        return Confirmation.UNCERTAIN
    words = [normalise(word) for word in transcript.text.split()]
    if len(words) > MAX_ANSWER_WORDS or normalise(detail.word) not in words:
        return Confirmation.MISMATCH
    return Confirmation.CONFIRMED
