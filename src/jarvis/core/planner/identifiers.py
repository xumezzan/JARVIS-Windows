"""One rule for what counts as a model identifier.

Settings, the consent dialog and the provider all decide whether a name can be sent to the
Responses API. When each kept its own copy of the rule they could disagree, and the owner
paid for it: a name the dialog accepted was refused later by the provider, in a different
window, with a message that named neither the field nor the mistake.
"""

import re

MODEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,99}")


def valid_model(name: str) -> bool:
    """A name the Responses API can be called with: no spaces and nothing exotic."""
    return MODEL.fullmatch(name) is not None
