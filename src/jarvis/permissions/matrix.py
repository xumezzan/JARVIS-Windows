"""Owner policy layered on top of trusted connector metadata.

A connector declares each capability's risk in code; that declaration is the trusted
source. This matrix lets the owner tighten the decision per service and per capability —
it can never loosen one. An unknown service or capability keeps its declared risk, and a
configuration that asks for less caution than the connector declared is ignored rather
than obeyed, so an edited file can never turn sending mail into a silent action.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jarvis.connectors.base import NAME, Capability
from jarvis.permissions.policies import Risk

ORDER = (Risk.SAFE, Risk.CONFIRM, Risk.CRITICAL, Risk.BLOCKED)
WILDCARD = "*"
FIELDS = {"allowed", "risk"}
MAX_BYTES = 65536


@dataclass(frozen=True)
class Rule:
    allowed: bool = True
    risk: Risk | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValueError("Permission must be a trusted boolean.")
        if self.risk is not None and not isinstance(self.risk, Risk):
            raise ValueError("Permission risk must be a trusted Risk value.")


class PermissionMatrix:
    def __init__(self, rules: Mapping[str, Mapping[str, Rule]] | None = None) -> None:
        validated: dict[str, dict[str, Rule]] = {}
        for service, capabilities in (rules or {}).items():
            if not NAME.fullmatch(service):
                raise ValueError("Invalid service name in permission matrix.")
            entries: dict[str, Rule] = {}
            for name, rule in capabilities.items():
                if name != WILDCARD and not NAME.fullmatch(name):
                    raise ValueError("Invalid capability name in permission matrix.")
                if not isinstance(rule, Rule):
                    raise ValueError("Permission matrix holds rules only.")
                entries[name] = rule
            validated[service] = entries
        self._rules = validated

    def rule(self, service: str, capability: str) -> Rule:
        entries = self._rules.get(service, {})
        found = entries.get(capability)
        return found if found is not None else entries.get(WILDCARD, Rule())

    def effective(self, service: str, capability: Capability) -> Risk:
        """The stricter of the declared and configured risk; a refusal becomes BLOCKED."""
        rule = self.rule(service, capability.name)
        if not rule.allowed:
            return Risk.BLOCKED
        if rule.risk is None:
            return capability.risk
        return max(capability.risk, rule.risk, key=ORDER.index)

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "service": service,
                "capability": name,
                "allowed": rule.allowed,
                "risk": rule.risk.value if rule.risk is not None else "",
            }
            for service, entries in self._rules.items()
            for name, rule in entries.items()
        ]


def load_matrix(path: Path) -> PermissionMatrix:
    """A missing file means no owner policy, not an empty allowlist."""
    if not path.exists():
        return PermissionMatrix()
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Permission matrix file is unusable.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise ValueError("Permission matrix is not readable JSON.") from None
    if not isinstance(data, dict):
        raise ValueError("Permission matrix must be an object.")
    rules: dict[str, dict[str, Rule]] = {}
    for service, capabilities in data.items():
        if not isinstance(capabilities, dict):
            raise ValueError("Each service needs an object of capabilities.")
        entries: dict[str, Rule] = {}
        for name, value in capabilities.items():
            if not isinstance(value, dict) or set(value) - FIELDS:
                raise ValueError("A rule holds only 'allowed' and 'risk'.")
            risk = value.get("risk")
            if risk is not None and (not isinstance(risk, str) or risk not in Risk.__members__):
                raise ValueError("Unknown risk in permission matrix.")
            entries[name] = Rule(
                allowed=value.get("allowed", True),
                risk=Risk[risk] if isinstance(risk, str) else None,
            )
        rules[service] = entries
    return PermissionMatrix(rules)
