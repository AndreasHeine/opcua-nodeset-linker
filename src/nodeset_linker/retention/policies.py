"""Built-in retention modes plus a custom-rule policy."""

from __future__ import annotations

from dataclasses import dataclass

from nodeset_linker.models.dependency import MandatoryLevel
from nodeset_linker.models.node import UANode, is_placeholder, mandatory_level_of
from nodeset_linker.models.node_class import ModellingRule, NodeRole
from nodeset_linker.retention.engine import CustomRule, RetentionContext


@dataclass(slots=True)
class StrictPolicy:
    """STRICT — preserve every dependency required by OPC UA semantics.

    Mandatory *and* Optional nodes reached by the closure are kept; nothing is dropped,
    because an optional component may still be instantiated by a server.
    """

    name = "STRICT"

    def should_retain(self, node: UANode, context: RetentionContext) -> bool:
        return True


@dataclass(slots=True)
class OptionalAwarePolicy(StrictPolicy):
    """OPTIONAL_AWARE — identical retention to STRICT, plus classification.

    :meth:`classify` is the classification a caller can ask for; the reports classify with
    :func:`nodeset_linker.models.node.mandatory_level_of` directly, because they classify in
    every mode, not only in this one. Both are the same rule.
    """

    name = "OPTIONAL_AWARE"

    @staticmethod
    def classify(node: UANode) -> MandatoryLevel:
        return mandatory_level_of(node)


@dataclass(slots=True)
class RuntimePolicy:
    """RUNTIME — omit optional declarations, without inferring live server usage.

    Keeps Mandatory declarations and required dependencies. Optional branches are skipped
    by the resolver; the engine protects explicit roots and restores non-prunable targets.
    """

    name = "RUNTIME"

    def should_retain(self, node: UANode, context: RetentionContext) -> bool:
        if node.role is NodeRole.PLACEHOLDER:
            return node.modelling_rule in {
                ModellingRule.MANDATORY_PLACEHOLDER,
                ModellingRule.MANDATORY,
            }
        optional = {ModellingRule.OPTIONAL, ModellingRule.OPTIONAL_PLACEHOLDER}
        return node.modelling_rule not in optional


@dataclass(slots=True)
class CustomPolicy:
    """CUSTOM — user-defined retain/exclude rules from YAML.

    Rules are evaluated in declaration order; the first match wins.  Nodes that no rule
    matches follow STRICT semantics, so custom rules can only narrow or widen the set,
    never break OPC UA correctness (the engine re-adds hard dependencies).
    """

    name = "CUSTOM"
    rules: list[CustomRule]

    def should_retain(self, node: UANode, context: RetentionContext) -> bool:
        for rule in self.rules:
            if rule.matches(node):
                return rule.include
        return True


def is_optional_placeholder(node: UANode) -> bool:
    """Placeholder nodes are optional unless declared Mandatory."""
    return is_placeholder(node.browse_name.name) and node.modelling_rule not in {
        ModellingRule.MANDATORY,
        ModellingRule.MANDATORY_PLACEHOLDER,
    }
