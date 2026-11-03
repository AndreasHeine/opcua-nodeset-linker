"""Directional closure and valid boundary pruning regressions."""

from __future__ import annotations

from dataclasses import replace

import pytest
from lxml.etree import XMLSchema, parse
from tests.conftest import (
    DATA,
    NODESET_SCHEMA,
    Machinery_NS,
    make_data_type,
    make_object,
    make_object_type,
    make_variable,
    node_id,
    structure_definition,
)

from nodeset_linker.config.schema import LinkerConfig
from nodeset_linker.exporter.nodeset import NodeSetExporter
from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.models.node import Reference
from nodeset_linker.models.node_class import ModellingRule
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import build_model, load_nodesets
from nodeset_linker.resolver.closure import DependencyResolver
from nodeset_linker.retention.engine import prune_navigation
from nodeset_linker.validator.semantic import SemanticValidator


def ref(name: str, target: int, forward: bool = True) -> tuple[Reference, ...]:
    return (Reference(target=node_id(target), type_name=name, is_forward=forward),)


def closure(nodes, root: int):
    return DependencyResolver(SemanticGraph.from_model(build_model(nodes))).resolve([node_id(root)])


def test_datatype_does_not_pull_unrelated_users_or_structure_fields() -> None:
    datatype = make_data_type("String", 12)
    selected = make_variable("Selected", 100, data_type=datatype.node_id)
    unrelated = make_variable("Unrelated", 101, data_type=datatype.node_id)
    structure = make_data_type(
        "UnrelatedStructure", 102, definition=structure_definition((("Text", datatype.node_id),))
    )
    assert closure([datatype, selected, unrelated, structure], 100).ids() == {
        selected.node_id, datatype.node_id,
    }
    assert closure([datatype, selected, unrelated, structure], 12).ids() == {datatype.node_id}


@pytest.mark.parametrize("name", ["HasTypeDefinition", "HasInterface"])
def test_shared_type_does_not_pull_other_instances(name: str) -> None:
    shared = make_object_type("Shared", 1)
    selected = make_object("Selected", 2, refs=ref(name, 1))
    unrelated = make_object("Unrelated", 3, refs=ref(name, 1))
    assert closure([shared, selected, unrelated], 2).ids() == {node_id(1), node_id(2)}
    assert closure([shared, selected, unrelated], 1).ids() == {node_id(1)}


@pytest.mark.parametrize("forward_only", [False, True])
def test_datatype_inheritance_keeps_bases_not_siblings(forward_only: bool) -> None:
    base = make_data_type("Base", 1, refs=ref("HasSubtype", 2) if forward_only else ())
    derived = make_data_type("Derived", 2, refs=() if forward_only else ref("HasSubtype", 1, False))
    sibling = make_data_type("Sibling", 3, refs=ref("HasSubtype", 1, False))
    nodes = [base, derived, sibling]
    assert closure(nodes, 2).ids() == {node_id(1), node_id(2)}
    assert closure(nodes, 1).ids() == {node_id(1)}
    model = build_model(nodes)
    graph = SemanticGraph.from_model(model)
    prepared = prune_navigation(model, graph, {node_id(1), node_id(2)})
    prepared.nodes = {nid: n for nid, n in prepared.nodes.items() if nid != node_id(3)}
    assert SemanticValidator(prepared).validate().ok


@pytest.mark.parametrize("name", ["HasComponent", "HasProperty", "Organizes"])
def test_inverse_only_child_is_discovered_without_importing_its_sibling(name: str) -> None:
    parent = make_object("Parent", 1)
    child = make_object("Child", 2, refs=ref(name, 1, False))
    sibling = make_object("Sibling", 3, refs=ref(name, 1, False))
    assert closure([parent, child, sibling], 1).ids() == {node_id(1), node_id(2), node_id(3)}
    resolved = closure([parent, child, sibling], 2)
    assert resolved.ids() == {node_id(1), node_id(2)}
    assert resolved.navigation_only == {node_id(1)}


def test_external_dependency_is_a_traversal_boundary() -> None:
    external_type = replace(
        make_object_type("External", 1, refs=ref("HasComponent", 3)),
        node_id=node_id(1, Machinery_NS),
    )
    root = make_object(
        "Root", 2, refs=(Reference(target=external_type.node_id, type_name="HasTypeDefinition"),)
    )
    other = make_object("Other", 3)
    graph = SemanticGraph.from_model(build_model([external_type, root, other]))
    resolved = DependencyResolver(graph, external=frozenset({Machinery_NS})).resolve([root.node_id])
    assert resolved.ids() == {root.node_id, external_type.node_id}


def test_method_declaration_is_a_required_dependency() -> None:
    from nodeset_linker.models.node import UAMethod

    declaration = UAMethod(node_id=node_id(2), browse_name=make_object("Declaration", 2).browse_name)
    instance = UAMethod(
        node_id=node_id(1), browse_name=make_object("Instance", 1).browse_name,
        method_declaration_id=declaration.node_id,
    )
    assert closure([instance, declaration], 1).ids() == {node_id(1), node_id(2)}


def test_runtime_omits_optional_subtree_but_explicit_root_survives(tmp_path) -> None:
    cfg = LinkerConfig(
        nodesets=[str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
        roots=["browseName=MSSControllerType"], mode="RUNTIME",
        output=str(tmp_path / "runtime.xml"),
    )
    pipeline = LinkerPipeline(cfg)
    model = pipeline.load()
    optional = model.find_by_browse_name("Diagnostics")[0]
    nested = make_object("OptionalNestedChild", 99001)
    model.nodes[optional.node_id] = optional.with_references(
        (*optional.references, Reference(target=nested.node_id, type_name="HasComponent"))
    )
    model.nodes[nested.node_id] = nested
    result = pipeline.run()
    assert optional.node_id not in result.retained
    assert nested.node_id not in result.retained
    reloaded = load_nodesets([result.output_path])
    assert reloaded.unresolved_targets() == []
    assert XMLSchema(parse(str(NODESET_SCHEMA))).validate(parse(str(result.output_path)))
    rooted = LinkerPipeline(cfg.model_copy(update={"roots": ["browseName=Diagnostics"]})).analyze()
    assert optional.node_id in rooted.retained


def test_optional_placeholder_can_be_omitted_by_runtime() -> None:
    parent = make_object_type("Parent", 1, refs=ref("HasComponent", 2))
    child = make_variable("Placeholder", 2, rule=ModellingRule.OPTIONAL_PLACEHOLDER)
    graph = SemanticGraph.from_model(build_model([parent, child]))
    assert DependencyResolver(graph, include_optional=False).resolve([parent.node_id]).ids() == {
        parent.node_id,
    }
    assert DependencyResolver(graph).resolve([parent.node_id]).ids() == {
        parent.node_id, child.node_id,
    }


def test_navigation_context_is_upgraded_when_required_elsewhere() -> None:
    parent = make_object("Parent", 1, refs=ref("HasComponent", 4))
    child = make_object(
        "Child", 2, refs=ref("HasComponent", 1, False) + ref("HasTypeDefinition", 3)
    )
    shared_type = make_object_type("SharedType", 3, refs=ref("RequiresParent", 1))
    sibling = make_object("Sibling", 4)
    resolved = closure([parent, child, shared_type, sibling], 2)
    assert resolved.ids() == {node_id(1), node_id(2), node_id(3), node_id(4)}
    assert parent.node_id not in resolved.navigation_only


def test_custom_ownership_reference_inherits_direction_from_its_base() -> None:
    from nodeset_linker.models.node import UAReferenceType

    base = UAReferenceType(
        node_id=node_id(47), browse_name=make_object("HasComponent", 47).browse_name
    )
    custom = UAReferenceType(
        node_id=node_id(100, Machinery_NS),
        browse_name=make_object("CustomComponent", 100).browse_name,
        references=(Reference(target=base.node_id, type_name="HasSubtype", is_forward=False),),
    )
    parent = make_object("Parent", 1)
    child = make_object("Child", 2, refs=ref("CustomComponent", 1, False))
    sibling = make_object("Sibling", 3, refs=ref("CustomComponent", 1, False))
    resolved = closure([parent, child, sibling, custom, base], 2)
    assert sibling.node_id not in resolved.ids()
    assert parent.node_id in resolved.navigation_only
    assert custom.node_id in resolved.ids()


def test_runtime_repairs_optional_target_required_by_non_navigation_link() -> None:
    cfg = LinkerConfig(
        nodesets=[str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
        roots=["browseName=MSSControllerType"], mode="RUNTIME",
    )
    pipeline = LinkerPipeline(cfg)
    model = pipeline.load()
    root = model.find_by_browse_name("MSSControllerType")[0]
    diagnostics = model.find_by_browse_name("Diagnostics")[0]
    model.nodes[root.node_id] = root.with_references(
        (*root.references, Reference(target=diagnostics.node_id, type_name="RequiresDiagnostics"))
    )
    result = pipeline.analyze()
    assert diagnostics.node_id in result.retained
    assert result.validation.ok


def test_custom_retained_type_gets_its_dependencies_and_explanation() -> None:
    cfg = LinkerConfig(
        nodesets=[str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
        roots=["browseName=JobStateEnum"], mode="CUSTOM",
        custom={"retain": ["browseName=UnrelatedType"]},
    )
    result = LinkerPipeline(cfg).analyze()
    extra = result.model.find_by_browse_name("UnrelatedType")[0]
    assert extra.node_id in result.closure.ids()
    assert result.explainer().explain("UnrelatedType").retained
    assert result.validation.ok


def test_custom_exclusion_of_optional_component_prunes_its_link(tmp_path) -> None:
    result = LinkerPipeline(LinkerConfig(
        nodesets=[str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
        roots=["browseName=MSSControllerType"], mode="CUSTOM",
        custom={"exclude": ["browseName=Diagnostics"]}, output=str(tmp_path / "custom.xml"),
    )).run()
    reloaded = load_nodesets([result.output_path])
    assert not reloaded.find_by_browse_name("Diagnostics")
    assert reloaded.unresolved_targets() == []


def test_argument_datatypes_and_encoding_ids_survive_namespace_remapping(tmp_path) -> None:
    namespace = "http://example.org/Argument/"
    xml = f'''<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">
      <NamespaceUris><Uri>http://unused.example/</Uri><Uri>{namespace}</Uri></NamespaceUris>
      <UAMethod NodeId="ns=2;i=100" BrowseName="2:Method">
        <References><Reference ReferenceType="i=46">ns=2;i=101</Reference></References>
      </UAMethod>
      <UAVariable NodeId="ns=2;i=101" BrowseName="InputArguments" DataType="i=24136">
        <Value><ListOfExtensionObject xmlns="http://opcfoundation.org/UA/2008/02/Types.xsd">
          <ExtensionObject><TypeId><Identifier>i=298</Identifier></TypeId>
            <Body><Argument><Name>Job</Name><DataType><Identifier>ns=2;i=102</Identifier></DataType>
            <ValueRank>-1</ValueRank><ArrayDimensions/><Description/></Argument></Body>
          </ExtensionObject>
        </ListOfExtensionObject></Value>
      </UAVariable>
      <UADataType NodeId="ns=2;i=102" BrowseName="2:Job">
        <References><Reference ReferenceType="i=45" IsForward="false">i=22</Reference></References>
      </UADataType>
      <UAObject NodeId="i=298" BrowseName="Default Binary"/>
    </UANodeSet>'''
    source = tmp_path / "arguments.xml"
    source.write_text(xml, encoding="utf-8")
    output = tmp_path / "out.xml"
    result = LinkerPipeline(LinkerConfig(
        nodesets=[str(DATA / "ua_base.xml"), str(source)],
        roots=[f"nsu={namespace};i=100"], output=str(output),
    )).run()
    assert node_id(102, namespace) in result.retained
    assert node_id(298) in result.retained
    reloaded = load_nodesets([output])
    argument = reloaded.find_by_browse_name("InputArguments")[0]
    assert ("ns=1;i=102", node_id(102, namespace)) in argument.value_dependencies
    assert result.validation.ok
    assert XMLSchema(parse(str(NODESET_SCHEMA))).validate(parse(str(output)))


def test_namespace_zero_browse_name_with_colon_is_unambiguous(tmp_path) -> None:
    node = make_object("http://example.org/UA/", 1)
    model = build_model([node])
    output = tmp_path / "colon.xml"
    NodeSetExporter(model, {node.node_id}).write(output)
    document = parse(str(output)).getroot()
    assert document[0].get("BrowseName") == "0:http://example.org/UA/"
    assert load_nodesets([output]).get(node.node_id).browse_name.name == node.browse_name.name


def test_unused_required_model_is_not_misrepresented_as_external(tmp_path) -> None:
    from nodeset_linker.models.namespace import ModelInfo

    node = make_object("Root", 1)
    unused = "http://example.org/Unused/"
    model = build_model([node])
    model.models[node.node_id.namespace] = ModelInfo(
        namespace_uri=node.node_id.namespace, required_models=(unused,)
    )
    output = tmp_path / "unused.xml"
    NodeSetExporter(model, {node.node_id}).write(output)
    assert unused not in output.read_text(encoding="utf-8")
