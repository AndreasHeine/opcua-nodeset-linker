"""Retention policy tests."""

from __future__ import annotations

import pytest
from tests.conftest import make_object, make_variable, node_id

from nodeset_linker.models.dependency import MandatoryLevel
from nodeset_linker.models.identifiers import QualifiedName
from nodeset_linker.models.node import UANode
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.policies.registry import MODES, build_policy
from nodeset_linker.retention.engine import CustomRule, RetentionContext, RetentionEngine
from nodeset_linker.retention.policies import OptionalAwarePolicy, RuntimePolicy, StrictPolicy


def context(node, retained=True):  # type: ignore[no-untyped-def]
    return RetentionContext(node=node, retained_by_closure=retained, is_root=False)


class TestBuiltInModes:
    def test_strict_keeps_everything(self) -> None:
        policy = StrictPolicy()
        optional = make_variable("Diag", 1, rule=ModellingRule.OPTIONAL)
        assert policy.should_retain(optional, context(optional)) is True

    def test_optional_aware_classifies(self) -> None:
        policy = OptionalAwarePolicy()
        mandatory = make_variable("M", 1, rule=ModellingRule.MANDATORY)
        optional = make_variable("O", 2, rule=ModellingRule.OPTIONAL)
        assert policy.classify(mandatory) is MandatoryLevel.MANDATORY
        assert policy.classify(optional) is MandatoryLevel.OPTIONAL

    def test_runtime_drops_optional(self) -> None:
        policy = RuntimePolicy()
        optional = make_variable("O", 2, rule=ModellingRule.OPTIONAL)
        mandatory = make_variable("M", 1, rule=ModellingRule.MANDATORY)
        assert policy.should_retain(optional, context(optional)) is False
        assert policy.should_retain(mandatory, context(mandatory)) is True

    def test_runtime_keeps_mandatory_placeholder(self) -> None:
        placeholder = UANode(
            node_id=node_id(3),
            browse_name=QualifiedName(name="MachineType_Placeholder"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.PLACEHOLDER,
            modelling_rule=ModellingRule.MANDATORY_PLACEHOLDER,
        )
        assert RuntimePolicy().should_retain(placeholder, context(placeholder)) is True

    def test_runtime_drops_optional_placeholder(self) -> None:
        placeholder = UANode(
            node_id=node_id(4),
            browse_name=QualifiedName(name="Foo_Placeholder"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.PLACEHOLDER,
            modelling_rule=ModellingRule.OPTIONAL_PLACEHOLDER,
        )
        assert RuntimePolicy().should_retain(placeholder, context(placeholder)) is False

    def test_registry_builds_all_modes(self) -> None:
        for mode in MODES:
            assert build_policy(mode).name == mode

    def test_registry_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValueError, match="unknown retention mode"):
            build_policy("NOPE")


class TestCustomRules:
    def test_namespace_rule(self) -> None:
        from nodeset_linker.models.identifiers import ExpandedNodeId, NodeId

        rule = CustomRule(include=False, namespace="http://opcfoundation.org/UA/Machinery/")
        local = make_object("A", 1)
        assert rule.matches(local) is False

        foreign = UANode(
            node_id=ExpandedNodeId(identifier=NodeId.numeric_id(2), namespace_uri="http://opcfoundation.org/UA/Machinery/"),
            browse_name=local.browse_name,
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
        )
        assert rule.matches(foreign) is True

    def test_browse_glob(self) -> None:
        assert CustomRule(include=False, browse_name="*Diagnostics*").matches(make_object("MachineDiagnostics", 1))

    def test_engine_first_rule_wins(self) -> None:
        engine = RetentionEngine(
            policy=StrictPolicy(),
            custom_rules=[CustomRule(include=False, browse_name="*Diagnostics*")],
        )
        optional_node = make_variable("MachineDiagnostics", 1, rule=ModellingRule.OPTIONAL)
        assert engine.decide(optional_node, retained_by_closure=True, is_root=False) is False
        assert engine.dropped == [optional_node.node_id]

    def test_engine_never_drops_a_type_by_custom_rule(self) -> None:
        """A custom exclude rule cannot break an inheritance or datatype chain."""
        from tests.conftest import make_data_type

        engine = RetentionEngine(
            policy=StrictPolicy(), custom_rules=[CustomRule(include=False, browse_name="*DataType")]
        )
        assert engine.decide(make_data_type("JobDataType", 2001), retained_by_closure=True, is_root=False) is True

    def test_engine_never_drops_hard_dependencies(self) -> None:
        from tests.conftest import make_data_type

        engine = RetentionEngine(
            policy=RuntimePolicy(),
            custom_rules=[CustomRule(include=False, node_class="DataType")],
        )
        structure = make_data_type("JobDataType", 2001)
        assert engine.decide(structure, retained_by_closure=True, is_root=False) is True

    def test_engine_adds_nodes_outside_closure(self) -> None:
        engine = RetentionEngine(
            policy=RuntimePolicy(), custom_rules=[CustomRule(include=True, namespace="http://opcfoundation.org/UA/")]
        )
        node = make_object("Extra", 7)
        assert engine.decide(node, retained_by_closure=False, is_root=False) is True
        assert engine.added_by_custom == [node.node_id]

    def test_roots_are_always_retained(self) -> None:
        engine = RetentionEngine(policy=RuntimePolicy(), custom_rules=[CustomRule(include=False, browse_name="*")])
        node = make_object("Root", 1)
        assert engine.decide(node, retained_by_closure=True, is_root=True) is True
