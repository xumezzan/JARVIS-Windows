"""Pure interaction policy over observed snapshots, never model-supplied risk levels."""

import re
import unicodedata

# Closing is conservative: desktop adapters cannot prove that a window is saved.
_SENSITIVE_ACTION = re.compile(
    r"\b(?:оплат\w*|плат[еиё]\w*|удал\w*|отправ\w*|послать|купить|покуп\w*|"
    r"заказ\w*|оформ\w*|закры\w*|сброс\w*|стереть|очист\w*|подтверд\w*|"
    r"pay\w*|delet\w*|remov\w*|send\w*|submit\w*|buy\w*|purchas\w*|"
    r"checkout|order\w*|close|exit|quit|discard\w*|erase\w*|confirm\w*)\b"
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
    return label.strip() in {"x", "×", "✕", "✖"} or bool(_SENSITIVE_ACTION.search(label))
