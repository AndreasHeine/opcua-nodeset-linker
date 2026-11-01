"""Policy registry: mode name → policy object.

The registry is the seam where future conformance rules (Conformance Units, Profiles,
Facets, Feature Sets, Vendor Capabilities, Certification Rules, Metadata Policies) can
be plugged in as additional policies without touching the graph engine.
"""

from __future__ import annotations

from nodeset_linker.retention.engine import CustomRule, RetentionPolicy
from nodeset_linker.retention.policies import (
    CustomPolicy,
    OptionalAwarePolicy,
    RuntimePolicy,
    StrictPolicy,
)

MODES = ("STRICT", "OPTIONAL_AWARE", "RUNTIME", "CUSTOM")


def build_policy(mode: str, custom_rules: list[CustomRule] | None = None) -> RetentionPolicy:
    """Create the policy for a configuration mode."""
    match mode.upper():
        case "STRICT":
            return StrictPolicy()
        case "OPTIONAL_AWARE":
            return OptionalAwarePolicy()
        case "RUNTIME":
            return RuntimePolicy()
        case "CUSTOM":
            return CustomPolicy(rules=list(custom_rules or []))
        case other:
            raise ValueError(f"unknown retention mode: {other!r} (expected one of {MODES})")
