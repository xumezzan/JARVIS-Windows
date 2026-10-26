"""Pure MCP name normalization shared by legacy and workspace clients."""

import re
import unicodedata


def capability_name(remote: str) -> str:
    folded = unicodedata.normalize("NFKC", remote).casefold()
    mapped = "".join(char if char.isascii() and char.isalnum() else "_" for char in folded)
    trimmed = re.sub(r"_+", "_", mapped).strip("_")
    return trimmed[:60] if trimmed and trimmed[0].isalpha() else ""
