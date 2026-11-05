"""Pure interaction policy over observed snapshots, never model-supplied risk levels."""

import re
import unicodedata

# Generic destructive/external semantics, independent of application or user phrasing.
_SENSITIVE_ACTION = re.compile(
    r"\b(?:оплат\w*|плат[еиё]\w*|удал\w*|отправ\w*|послать|купить|покуп\w*|"
    r"заказ\w*|сброс\w*|стереть|очист\w*|"
    r"pay\w*|delet\w*|remov\w*|send\w*|submit\w*|buy\w*|purchas\w*|"
    r"checkout|order\w*|discard\w*|erase\w*|publish\w*|"
    r"post|comment|опубликов\w*|публиков\w*)\b"
    r"|\b(?:не\s+сохранять|без\s+сохранения|do\s+not\s+save|don['’]t\s+save)\b"
)


def requires_confirmation(label: str) -> bool:
    """Recognize destructive/external action labels in Russian and English.

    Labels are a conservative heuristic, not proof of an application's semantics.
    Adapters still re-observe and match the entire target before issuing effects.
    """
    label = re.sub(r"([a-z])([A-Z])", r"\1 \2", label)
    label = unicodedata.normalize("NFKC", label).casefold().replace("&", "").replace("_", " ")
    label = "".join(char for char in label if unicodedata.category(char) != "Cf")
    return bool(_SENSITIVE_ACTION.search(label))
