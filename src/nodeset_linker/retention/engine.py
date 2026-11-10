"""Retention decisions: which nodes survive the reduction."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from nodeset_linker.graph.engine import OWNERSHIP_REFERENCES, SemanticGraph, follows_reference
from nodeset_linker.models.dependency import MandatoryLevel
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.node import UANode, mandatory_level_of
from nodeset_linker.models.node_class import ModellingRule, NodeRole
from nodeset_linker.repository.model import SemanticModel


def prune_navigation(
    model: SemanticModel,
    graph: SemanticGraph,
    keep: set[ExpandedNodeId],
    external: frozenset[str] = frozenset(),
    *,
    navigation_only: set[ExpandedNodeId] | None = None,
) -> SemanticModel:
    """Prepare retained nodes without legal boundary links; preserve missing hard targets."""
    result = SemanticModel(
        nodes=dict(model.nodes),
        namespaces=dict(model.namespaces),
        documents=list(model.documents),
        models=dict(model.models),
    )
    for node_id in keep:
        node = model.nodes[node_id]
        references = []
        for ref in node.references:
            semantics = graph.reference_semantics(ref.type_name)
            target = model.get(ref.target)
            omitted = (
                target is not None
                and ref.target not in keep
                and ref.target.namespace not in external
            )
            optional_child = (
                target is not None
                and semantics in OWNERSHIP_REFERENCES | {"Organizes"}
                and target.modelling_rule
                in {ModellingRule.OPTIONAL, ModellingRule.OPTIONAL_PLACEHOLDER}
            )
            if omitted and (
                not follows_reference(semantics, ref.is_forward) or optional_child
                or (
                    navigation_only is not None and node_id in navigation_only
                    and semantics in OWNERSHIP_REFERENCES | {"Organizes"}
                )
            ):
                continue
            references.append(ref)
        updated = node.with_references(tuple(references))
        if (
            node.parent_node_id in model.nodes
            and node.parent_node_id not in keep
            and node.parent_node_id is not None
            and node.parent_node_id.namespace not in external
        ):
            updated = replace(updated, parent_node_id=None)
        result.nodes[node_id] = updated
    return result


@dataclass(slots=True)
class RetentionContext:
    """Everything a policy may look at when deciding about one node."""

    node: UANode
    retained_by_closure: bool = False
    is_root: bool = False
    mandatory_level: MandatoryLevel = MandatoryLevel.OPTIONAL


class RetentionPolicy(Protocol):
    """Base interface for retention policies."""

    name: str

    def should_retain(self, node: UANode, context: RetentionContext) -> bool:
        """Return ``True`` if the node must appear in the reduced NodeSet."""
        ...


@dataclass(slots=True)
class CustomRule:
    """One user-defined retain/exclude rule from YAML.

    Supported keys: ``namespace`` (the namespace *URI*, e.g.
    ``http://opcfoundation.org/UA/Machinery/`` or ``*Machinery*``), ``browseName``,
    ``nodeClass`` and ``modellingRule``; ``namespace`` and ``browseName`` values may
    contain globs (``*Diagnostics*``), ``nodeClass`` and ``modellingRule`` are exact
    spellings (case-insensitive).  ``modellingRule=None`` selects nodes that carry no
    modelling rule at all.  All given keys must match for the rule to apply.
    """

    include: bool = True
    namespace: str | None = None
    browse_name: str | None = None
    node_class: str | None = None
    modelling_rule: str | None = None

    def matches(self, node: UANode) -> bool:
        from nodeset_linker.utils.text import matches_glob

        if self.namespace is not None and not matches_glob(self.namespace, node.node_id.namespace):
            return False
        names = {node.browse_name.name}
        if node.display_name:
            names.add(node.display_name)
        if self.browse_name is not None and not any(
            matches_glob(self.browse_name, name) for name in names
        ):
            return False
        if self.node_class is not None and node.node_class.value.lower() != self.node_class.lower():
            return False
        if self.modelling_rule is not None:
            # NO_RULE is an enum member whose *value* is None, and enum members are
            # truthy, so the rule-less case must be detected on the member, not on
            # the value: a rule-less node spells as "None" and stays comparable.
            if node.modelling_rule is None or node.modelling_rule is ModellingRule.NO_RULE:
                rule = "None"
            else:
                rule = node.modelling_rule.value
            if rule.lower() != self.modelling_rule.lower():
                return False
        return True


@dataclass(slots=True)
class RetentionEngine:
    """Applies a policy to every node and records mandatory/optional classification.

    Closure membership is the floor: a node reached by a required dependency chain is
    always retained, regardless of policy.  Policies may additionally *drop* nodes that
    are only reachable through optional chains, and *add* nodes via custom rules.
    """

    policy: RetentionPolicy
    custom_rules: list[CustomRule] = field(default_factory=list)
    dropped: list[ExpandedNodeId] = field(default_factory=list)
    excluded: list[ExpandedNodeId] = field(default_factory=list)
    added_by_custom: list[ExpandedNodeId] = field(default_factory=list)

    def decide(self, node: UANode, *, retained_by_closure: bool, is_root: bool) -> bool:
        context = RetentionContext(
            node=node,
            retained_by_closure=retained_by_closure,
            is_root=is_root,
            mandatory_level=mandatory_level_of(node),
        )
        rule_decision: bool | None = None
        for rule in self.custom_rules:
            if rule.matches(node):
                # User rules are the user's intent and win over the base policy; they can
                # only narrow or widen retention of *droppable* nodes.
                rule_decision = rule.include
                break

        allowed = (
            rule_decision if rule_decision is not None else self.policy.should_retain(node, context)
        )
        if is_root or (retained_by_closure and self._closure_is_hard(node)):
            # Hard OPC UA dependencies and roots may never be dropped.
            allowed = True
        elif not retained_by_closure and rule_decision is not True:
            allowed = False  # outside the closure nothing is kept except by a retain rule
        if not allowed and retained_by_closure:
            self.dropped.append(node.node_id)
            if rule_decision is False:
                # Excluded on purpose by the user; never silently repaired.  Validation
                # reports the break an exclusion causes instead of the engine fixing it.
                self.excluded.append(node.node_id)
        elif allowed and not retained_by_closure:
            self.added_by_custom.append(node.node_id)
        return allowed

    def repair(
        self,
        graph: SemanticGraph,
        retained: set[ExpandedNodeId],
        *,
        repairable: set[ExpandedNodeId],
    ) -> list[ExpandedNodeId]:
        """Bring back dropped nodes that retained nodes still reference.

        Legal navigation links are pruned before this runs. A retained node that still
        keeps a reference to a dropped target would produce a
        model with dangling references, so the target is restored and reported as a repair.
        Only nodes in *repairable* (the ones the policy dropped) are brought back; nodes a
        user excluded on purpose stay gone, and validation reports any break they cause.
        Hard dependencies are already protected by :meth:`_closure_is_hard`.
        """
        added: list[ExpandedNodeId] = []
        pending = list(retained)
        seen = set(retained)
        while pending:
            current = pending.pop()
            node = graph.node(current)
            if node is None:
                continue
            for ref in node.references:
                target = ref.target
                if target in seen or not graph.has_node(target):
                    continue
                if target not in repairable:
                    continue
                seen.add(target)
                retained.add(target)
                added.append(target)
                pending.append(target)
        return added

    @staticmethod
    def _closure_is_hard(node: UANode) -> bool:
        """Types and Mandatory declarations/placeholders are hard dependencies.

        Inheritance chains, data types, encodings and interfaces must never be broken, so
        they cannot be dropped by a policy or a custom rule. Optional placeholders may
        be omitted like other optional declarations. Everything else — an instance node
        without its own
        modelling rule, or one declared ``Optional`` — is droppable; that is what RUNTIME
        drops and what CUSTOM rules exclude.
        """
        if node.role.is_hard_dependency:
            return True
        if node.role is NodeRole.PLACEHOLDER:
            return node.modelling_rule is not ModellingRule.OPTIONAL_PLACEHOLDER
        return node.modelling_rule in {ModellingRule.MANDATORY, ModellingRule.MANDATORY_PLACEHOLDER}
