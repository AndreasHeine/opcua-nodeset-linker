"""External NodeSet dependencies: models that are referenced but deliberately not shipped.

An external dependency is not a reduction.  The nodes of the configured model are left out
of the export even when the dependency closure pulled them in, while every reference that
points at them is kept, the namespace URI stays in ``NamespaceUris`` and the dependency is
stated as a ``RequiredModel``.  The OPC UA namespace (namespace 0) is a dependency like any
model, but not the same case: it is never written as a ``<Uri>`` and its identifiers stay
unqualified, so its tests are separate from the ordinary-model tests below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from lxml.etree import XMLSchema, parse
from tests.conftest import DATA, NODESET_SCHEMA, UA_NS, Machinery_NS

from nodeset_linker.cli.main import main
from nodeset_linker.config.schema import LinkerConfig
from nodeset_linker.exporter.nodeset import ExportError, export
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.utils.errors import ResolutionError, ValidationError
from nodeset_linker.validator.semantic import declared_external_namespaces, validate_model

DI_NS = "http://opcfoundation.org/UA/DI/"
ALPHA_NS = "http://opcfoundation.org/UA/Alpha/"
HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'

DI_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{DI_NS}</Uri>
  </NamespaceUris>
  <Models>
    <Model ModelUri="{DI_NS}" XmlSchemaUri="http://opcfoundation.org/UA/DI/Types.xsd" Version="1.0.2" PublicationDate="2024-02-02T00:00:00Z" ModelVersion="1.0.2">
      <RequiredModel ModelUri="{UA_NS}" XmlSchemaUri="http://opcfoundation.org/UA/2008/02/Types.xsd" Version="1.05.07" PublicationDate="2026-07-30T00:00:00Z" ModelVersion="1.5.7"/>
    </Model>
  </Models>
  <Aliases>
    <Alias Alias="Int32">i=6</Alias>
    <Alias Alias="HasSubtype">i=45</Alias>
    <Alias Alias="HasComponent">i=47</Alias>
    <Alias Alias="HasTypeDefinition">i=40</Alias>
    <Alias Alias="HasEncoding">i=38</Alias>
  </Aliases>
  <UAObjectType NodeId="ns=1;i=1000" BrowseName="1:DeviceType" SymbolicName="DeviceType">
    <DisplayName>DeviceType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>
      <Reference ReferenceType="HasComponent">ns=1;i=1010</Reference>
    </References>
  </UAObjectType>
  <UAVariable NodeId="ns=1;i=1010" BrowseName="1:Tag" DataType="i=6">
    <DisplayName>Tag</DisplayName>
    <References>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=1;i=1000</Reference>
      <Reference ReferenceType="HasTypeDefinition">i=63</Reference>
    </References>
  </UAVariable>
  <UAVariableType NodeId="ns=1;i=1015" BrowseName="1:TagType" SymbolicName="TagType" DataType="i=6" ValueRank="-2">
    <DisplayName>TagType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=63</Reference>
    </References>
  </UAVariableType>
  <UADataType NodeId="ns=1;i=2001" BrowseName="1:TagStructure" SymbolicName="TagStructure">
    <DisplayName>TagStructure</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
      <Reference ReferenceType="HasEncoding">ns=1;i=2050</Reference>
      <Reference ReferenceType="HasEncoding">ns=1;i=2051</Reference>
    </References>
    <Definition Name="TagStructure">
      <Field Name="Value" DataType="i=6"/>
      <Field Name="State" DataType="ns=1;i=2002"/>
    </Definition>
  </UADataType>
  <UADataType NodeId="ns=1;i=2002" BrowseName="1:TagState" SymbolicName="TagState">
    <DisplayName>TagState</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=29</Reference>
    </References>
    <Definition Name="TagState">
      <Field Name="Idle" Value="0"/>
      <Field Name="Active" Value="1"/>
    </Definition>
  </UADataType>
  <UADataType NodeId="ns=1;i=2050" BrowseName="1:TagStructureBinary" SymbolicName="TagStructureBinary" Purpose="ServicesOnly">
    <DisplayName>TagStructure Binary Encoding</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
    </References>
  </UADataType>
  <UADataType NodeId="ns=1;i=2051" BrowseName="1:TagStructureXml" SymbolicName="TagStructureXml" Purpose="ServicesOnly">
    <DisplayName>TagStructure XML Encoding</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
    </References>
  </UADataType>
  <UAReferenceType NodeId="ns=1;i=3000" BrowseName="HasTag">
    <DisplayName>HasTag</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=33</Reference>
    </References>
  </UAReferenceType>
</UANodeSet>
"""

def alpha_fixture(*, required_models: str = "") -> str:
    """A model that depends on DI through cross-namespace identifiers."""
    return f"""{HEADER}
  <NamespaceUris>
    <Uri>{ALPHA_NS}</Uri>
  </NamespaceUris>
  <Models>
    <Model ModelUri="{ALPHA_NS}" XmlSchemaUri="http://opcfoundation.org/UA/Alpha/Types.xsd" Version="2.1.0" PublicationDate="2025-01-01T00:00:00Z" ModelVersion="2.1.0">
{required_models}
    </Model>
  </Models>
  <Aliases>
    <Alias Alias="String">i=12</Alias>
    <Alias Alias="Structure">i=22</Alias>
    <Alias Alias="HasSubtype">i=45</Alias>
    <Alias Alias="HasComponent">i=47</Alias>
    <Alias Alias="HasTypeDefinition">i=40</Alias>
  </Aliases>
  <UAObjectType NodeId="ns=1;i=100" BrowseName="1:AlphaMachineType" SymbolicName="AlphaMachineType">
    <DisplayName>AlphaMachineType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">nsu={DI_NS};i=1000</Reference>
      <Reference ReferenceType="HasComponent">nsu={DI_NS};i=1010</Reference>
    </References>
  </UAObjectType>
  <UAVariable NodeId="ns=1;i=200" BrowseName="1:AlphaTag" DataType="nsu={DI_NS};i=2001">
    <DisplayName>AlphaTag</DisplayName>
    <References>
      <Reference ReferenceType="HasTypeDefinition">nsu={DI_NS};i=1015</Reference>
    </References>
  </UAVariable>
  <UADataType NodeId="ns=1;i=300" BrowseName="1:AlphaReading" SymbolicName="AlphaReading">
    <DisplayName>AlphaReading</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
    </References>
    <Definition Name="AlphaReading">
      <Field Name="Tag" DataType="nsu={DI_NS};i=2001"/>
      <Field Name="State" DataType="nsu={DI_NS};i=2002"/>
    </Definition>
  </UADataType>
  <UADataType NodeId="ns=1;i=310" BrowseName="1:AlphaChoice" SymbolicName="AlphaChoice">
    <DisplayName>AlphaChoice</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
    </References>
    <Definition Name="AlphaChoice" IsUnion="true">
      <Field Name="Text" DataType="i=12"/>
      <Field Name="State" DataType="nsu={DI_NS};i=2002"/>
    </Definition>
  </UADataType>
  <UAObject NodeId="ns=1;i=400" BrowseName="1:AlphaInstance">
    <DisplayName>AlphaInstance</DisplayName>
    <References>
      <Reference ReferenceType="HasTypeDefinition">nsu={DI_NS};i=1000</Reference>
      <Reference ReferenceType="nsu={DI_NS};i=3000">nsu={DI_NS};i=1010</Reference>
    </References>
  </UAObject>
</UANodeSet>
"""


REQUIRED_DI_WITH_VERSION = (
    f'      <RequiredModel ModelUri="{DI_NS}" XmlSchemaUri="http://opcfoundation.org/UA/DI/Types.xsd"'
    ' Version="1.0.2" PublicationDate="2024-02-02T00:00:00Z" ModelVersion="1.0.2"/>\n'
    f'      <RequiredModel ModelUri="{UA_NS}" XmlSchemaUri="http://opcfoundation.org/UA/2008/02/Types.xsd"'
    ' Version="1.05.07" PublicationDate="2026-07-30T00:00:00Z" ModelVersion="1.5.7"/>\n'
)

REQUIRED_DI_WITHOUT_VERSION = f'      <RequiredModel ModelUri="{DI_NS}"/>\n'

DI_NODES = {
    ExpandedNodeId.parse(f"nsu={DI_NS};i=1000"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=1010"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=1015"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=2001"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=2002"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=2050"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=2051"),
    ExpandedNodeId.parse(f"nsu={DI_NS};i=3000"),
}


def write(tmp_path: Path, name: str, xml: str) -> Path:
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


def local(element) -> str:  # type: ignore[no-untyped-def]
    return element.tag.rpartition("}")[2]


def root_of(path: Path):  # type: ignore[no-untyped-def]
    return parse(path).getroot()


def node_ids(path: Path) -> list[str]:
    """The ``NodeId`` attributes the file actually declares."""
    return [
        value
        for el in root_of(path).iter()
        if local(el).startswith("UA") and (value := el.get("NodeId")) is not None
    ]


def reference_texts(path: Path) -> list[str]:
    return [el.text or "" for el in root_of(path).iter() if local(el) == "Reference"]


def namespace_uris(path: Path) -> list[str]:
    table = next(el for el in root_of(path) if local(el) == "NamespaceUris")
    return [el.text or "" for el in table]


def model_entries(path: Path) -> dict[str, list[dict[str, str]]]:
    """Shipped ``<Model>`` entries with their ``<RequiredModel>`` children."""
    entries: dict[str, list[dict[str, str]]] = {}
    for el in root_of(path).iter():
        if local(el) == "Model":
            entries[el.get("ModelUri") or ""] = [
                dict(child.attrib) for child in el.iter() if local(child) == "RequiredModel"
            ]
    return entries


def xsd_valid(path: Path) -> bool:
    return XMLSchema(parse(NODESET_SCHEMA)).validate(parse(path))


def _model_nodesets(alpha: Path, di: Path) -> list[Path]:
    """The fixtures behind an Alpha/DI run.

    ``ua_base.xml`` declares instances whose datatype declarations live in
    ``machinery.xml``, and the closure follows the type hierarchy down to them.  Both are
    loaded so that a missing datatype stays an ordinary error rather than a hidden external
    dependency: only what ``external`` names is allowed to point outside the file.
    """
    return [DATA / "ua_base.xml", DATA / "machinery.xml", alpha, di]


def run(
    tmp_path: Path,
    nodesets: list[Path],
    roots: list[str],
    *,
    external: list[str] = (),
    mode: str = "STRICT",
    custom: dict | None = None,
) -> object:
    config = LinkerConfig(
        nodesets=[str(path) for path in nodesets],
        roots=roots,
        output=str(tmp_path / "out.xml"),
        mode=mode,  # type: ignore[arg-type]
        custom=custom,  # type: ignore[arg-type]
        external=list(external),
    )
    return LinkerPipeline(config).run()


class TestExternalOrdinaryModel:
    """An included model that depends on DI, with DI externalized."""

    @pytest.fixture
    def linked(self, tmp_path):
        alpha = write(tmp_path, "alpha.xml", alpha_fixture(required_models=REQUIRED_DI_WITH_VERSION))
        di = write(tmp_path, "di.xml", DI_FIXTURE)
        result = run(
            tmp_path,
            _model_nodesets(alpha, di),
            [f"namespaceUri={ALPHA_NS}"],
            external=[DI_NS],
        )
        return result, tmp_path / "out.xml"

    def test_external_declarations_are_not_in_the_output(self, linked) -> None:
        result, path = linked
        assert result.external == frozenset({DI_NS})
        text = path.read_text(encoding="utf-8")
        ids = node_ids(path)
        assert ids
        assert all(not value.startswith(f"nsu={DI_NS}") for value in ids)
        assert all(not value.startswith("ns=2;") for value in ids)
        for browse in ("DeviceType", "TagStructure", "TagState", "HasTag", "TagStructureBinary"):
            assert f'BrowseName="1:{browse}"' not in text
            assert f'SymbolicName="{browse}"' not in text

    def test_every_reference_to_the_external_model_is_written(self, linked) -> None:
        _, path = linked
        texts = set(reference_texts(path))
        assert "ns=2;i=1000" in texts  # subtype base of AlphaMachineType
        assert "ns=2;i=1010" in texts  # component and custom-reference target
        assert 'ReferenceType="ns=2;i=3000"' in path.read_text(encoding="utf-8")  # DI reference type
        assert 'ReferenceType="ns=2;i=1015"' not in path.read_text(encoding="utf-8")
        assert "ns=2;i=1015" in texts  # AlphaTag's type definition is a DI VariableType

    def test_datatype_and_type_definition_dependencies_survive(self, linked) -> None:
        _, path = linked
        text = path.read_text(encoding="utf-8")
        assert 'DataType="ns=2;i=2001"' in text  # AlphaTag: attribute
        assert 'DataType="ns=2;i=2002"' in text  # structure field + union alternative
        assert 'NodeId="ns=1;i=200"' in text
        assert f'NodeId="nsu={DI_NS}' not in text  # index remapped, never spelled out

    def test_namespace_uri_and_required_model_metadata_are_written(self, linked) -> None:
        _, path = linked
        uris = namespace_uris(path)
        assert DI_NS in uris  # referenced, so its URI stays in the table
        assert uris.index(ALPHA_NS) < uris.index(DI_NS)
        models = model_entries(path)
        assert DI_NS not in models  # the external model is not re-declared
        required = models[ALPHA_NS]
        by_uri = {entry["ModelUri"]: entry for entry in required}
        assert by_uri[DI_NS]["Version"] == "1.0.2"
        assert by_uri[DI_NS]["PublicationDate"] == "2024-02-02T00:00:00Z"
        assert by_uri[DI_NS]["XmlSchemaUri"] == "http://opcfoundation.org/UA/DI/Types.xsd"

    def test_exported_file_is_schema_valid(self, linked) -> None:
        _, path = linked
        assert xsd_valid(path)

    def test_a_loaded_dependency_produces_no_dependency_warnings(self, linked) -> None:
        """DI was read, so its declarations were checked and its version is known."""
        result, _ = linked
        codes = {finding.code for finding in result.validation.warnings}
        assert not codes & {
            "EXTERNAL_MODEL_NOT_LOADED",
            "EXTERNAL_MODEL_VERSION_UNKNOWN",
            "EXTERNAL_MODEL_NOT_DECLARED",
        }

    def test_reports_separate_external_nodes_from_removed_nodes(self, linked) -> None:
        result, _ = linked
        statistics = json.loads(result.statistics.as_json())
        assert statistics["external_nodes"] == len(DI_NODES)
        assert statistics["retained_nodes"] == len(result.retained)
        eligible = statistics["original_nodes"] - statistics["external_nodes"]
        assert statistics["removed_nodes"] == eligible - statistics["retained_nodes"]
        assert statistics["reduction_percent"] == round(
            statistics["removed_nodes"] / eligible * 100, 1
        )
        namespace_report = json.loads(result.namespace_report.as_json())
        assert namespace_report["external_models"] == [DI_NS]
        assert namespace_report["external_references"]["DI"] > 0  # counted per namespace alias

    def test_dependency_is_configured_by_uri_not_by_a_document_index(self, tmp_path) -> None:
        """The same run with a trailing-slash-free ModelUri externalizes DI."""
        alpha = write(tmp_path, "alpha.xml", alpha_fixture(required_models=REQUIRED_DI_WITH_VERSION))
        di = write(tmp_path, "di.xml", DI_FIXTURE)
        result = run(
            tmp_path,
            _model_nodesets(alpha, di),
            [f"namespaceUri={ALPHA_NS}"],
            external=["http://opcfoundation.org/UA/DI"],
        )
        assert result.external == frozenset({DI_NS})
        assert DI_NS in namespace_uris(tmp_path / "out.xml")


class TestRoundTrip:
    """Parse -> export -> parse keeps the external references and their namespace."""

    @pytest.fixture
    def reduced(self, tmp_path) -> tuple[object, Path]:
        alpha = write(tmp_path, "alpha.xml", alpha_fixture(required_models=REQUIRED_DI_WITH_VERSION))
        di = write(tmp_path, "di.xml", DI_FIXTURE)
        result = run(
            tmp_path,
            _model_nodesets(alpha, di),
            [f"namespaceUri={ALPHA_NS}"],
            external=[DI_NS],
        )
        return result, tmp_path / "out.xml"

    def test_reloading_the_output_keeps_the_external_namespace(self, reduced) -> None:
        _, path = reduced
        model = load_nodesets([path])
        assert model.namespaces.get(DI_NS) is not None
        machine = model.nodes[ExpandedNodeId.parse("nsu=" + ALPHA_NS + ";i=100")]
        assert machine.references[0].target.namespace == DI_NS
        assert machine.references[0].target.identifier.as_xml() == "i=1000"
        unresolved = model.unresolved_targets()
        assert {target.namespace for _, target in unresolved if target.namespace != UA_NS} == {DI_NS}
        assert "i=1000" in {target.identifier.as_xml() for _, target in unresolved}

    def test_validation_accepts_the_declared_dependency_only(self, reduced) -> None:
        _, path = reduced
        model = load_nodesets([path])
        assert declared_external_namespaces(model) == frozenset({DI_NS})
        assert validate_model(model, external=declared_external_namespaces(model)).ok

    def test_reexport_from_the_reloaded_file_keeps_the_references(self, reduced, tmp_path) -> None:
        _, path = reduced
        model = load_nodesets([path])
        second = tmp_path / "again.xml"
        export(model, set(model.nodes), second, mark_namespace_subsets=False, external=frozenset({DI_NS}))
        assert sorted(reference_texts(second)) == sorted(reference_texts(path))
        assert namespace_uris(second) == namespace_uris(path)
        assert xsd_valid(second)


class TestCoreNamespace:
    """Namespace 0 is a dependency too, but it is not the same case as an ordinary model."""

    def test_core_declarations_are_omitted_and_references_stay_unqualified(self, tmp_path) -> None:
        result = run(
            tmp_path,
            [DATA / "ua_base.xml", DATA / "machinery.xml"],
            [f"namespaceUri={Machinery_NS}"],
            external=[UA_NS],
        )
        path = tmp_path / "out.xml"
        assert result.external == frozenset({UA_NS})
        assert result.validation is not None and result.validation.ok
        assert node_ids(path)  # something was shipped
        assert all(value.startswith("ns=1;") for value in node_ids(path))
        assert "i=11715" in reference_texts(path)  # the Namespaces folder, namespace 0
        assert "i=11616" in reference_texts(path)  # NamespaceMetadataType
        assert 'ReferenceType="HasSubtype"' in path.read_text(encoding="utf-8")

    def test_core_dependency_is_stated_without_a_model_entry(self, tmp_path) -> None:
        run(
            tmp_path,
            [DATA / "ua_base.xml", DATA / "machinery.xml"],
            [f"namespaceUri={Machinery_NS}"],
            external=[UA_NS],
        )
        path = tmp_path / "out.xml"
        assert namespace_uris(path) == [Machinery_NS]
        models = model_entries(path)
        assert UA_NS not in models
        required = {entry["ModelUri"]: entry for entry in models[Machinery_NS]}
        assert required[UA_NS]["Version"] == "1.05.07"
        assert required[UA_NS]["ModelVersion"] == "1.5.7"

    def test_core_dependency_is_reported_when_it_is_not_loaded(self, tmp_path) -> None:
        """Machinery states its Core dependency; the linker cannot check what it did not read."""
        result = run(
            tmp_path,
            [DATA / "machinery.xml"],
            [f"namespaceUri={Machinery_NS}"],
            external=[UA_NS],
        )
        path = tmp_path / "out.xml"
        assert result.validation is not None and result.validation.ok
        codes = {finding.code for finding in result.validation.warnings}
        assert "EXTERNAL_MODEL_NOT_LOADED" in codes
        assert "EXTERNAL_MODEL_VERSION_UNKNOWN" not in codes
        assert required_without_version(path) is False

    def test_version_metadata_is_kept_as_the_source_declared_it(self, tmp_path) -> None:
        """A RequiredModel without Version stays without Version: the linker invents nothing."""
        alpha = write(tmp_path, "alpha.xml", alpha_fixture(required_models=REQUIRED_DI_WITHOUT_VERSION))
        result = run(
            tmp_path,
            [DATA / "ua_base.xml", DATA / "machinery.xml", alpha],
            [f"namespaceUri={ALPHA_NS}"],
            external=[DI_NS],
        )
        path = tmp_path / "out.xml"
        assert required_without_version(path) is True
        codes = {finding.code for finding in result.validation.warnings}
        assert "EXTERNAL_MODEL_VERSION_UNKNOWN" in codes
        assert "EXTERNAL_MODEL_NOT_LOADED" in codes
        assert "EXTERNAL_MODEL_NOT_DECLARED" not in codes


def required_without_version(path: Path) -> bool:
    """Whether a Core/DI dependency is stated without any version metadata."""
    for entries in model_entries(path).values():
        for entry in entries:
            if entry["ModelUri"] in {UA_NS, DI_NS} and "Version" not in entry:
                return True
    return False


class TestStrictnessStaysStrict:
    """Externalizing is opt-in; everything else still has to resolve inside the file."""

    def test_undeclared_missing_targets_still_block_the_export(self, tmp_path) -> None:
        """A dependency that was never declared external is not excused by the export."""
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        shipped = {node_id for node_id in model.nodes if node_id.namespace == Machinery_NS}
        with pytest.raises(ExportError, match="export blocked"):
            export(model, shipped, tmp_path / "out.xml", external=frozenset({DI_NS}))
        assert not (tmp_path / "out.xml").exists()

    def test_explicit_exclusion_that_breaks_the_model_still_fails(self, tmp_path) -> None:
        """An ordinary exclude rule is not relaxed by an external dependency."""
        base = write(
            tmp_path, "base.xml",
            (DATA / "ua_base.xml").read_text(encoding="utf-8").replace(
                '<Reference ReferenceType="HasComponent">i=1012</Reference>',
                '<Reference ReferenceType="RequiresDiagnostics">i=1012</Reference>',
            ),
        )
        with pytest.raises(ValidationError) as excinfo:
            run(
                tmp_path,
                [base, DATA / "machinery.xml"],
                ["browseName=MSSControllerType"],
                external=[DI_NS],
                mode="CUSTOM",
                custom={"exclude": ["browseName=Diagnostics"]},
            )
        assert not (tmp_path / "out.xml").exists()
        assert any(f.code == "REF_TARGET_MISSING" for f in excinfo.value.findings.errors)

    def test_a_root_may_not_live_in_an_externalized_model(self, tmp_path) -> None:
        alpha = write(tmp_path, "alpha.xml", alpha_fixture(required_models=REQUIRED_DI_WITH_VERSION))
        di = write(tmp_path, "di.xml", DI_FIXTURE)
        with pytest.raises(ResolutionError, match="externalized model"):
            run(
                tmp_path,
                [DATA / "ua_base.xml", alpha, di],
                [f"nsu={DI_NS};i=1000"],
                external=[DI_NS],
            )
        with pytest.raises(ResolutionError, match="externalized model"):
            run(
                tmp_path,
                [DATA / "ua_base.xml", DATA / "machinery.xml"],
                ["browseName=BaseObjectType"],
                external=[UA_NS],
            )

    def test_reference_into_an_undeclared_dependency_is_reported(self, tmp_path) -> None:
        """Nothing in the file states the DI dependency: that is a warning, not a bundle."""
        alpha = write(tmp_path, "alpha.xml", alpha_fixture())
        di = write(tmp_path, "di.xml", DI_FIXTURE)
        result = run(
            tmp_path,
            _model_nodesets(alpha, di),
            [f"namespaceUri={ALPHA_NS}"],
            external=[DI_NS],
        )
        codes = {finding.code for finding in result.validation.warnings}
        assert "EXTERNAL_MODEL_NOT_DECLARED" in codes
        assert DI_NS not in model_entries(tmp_path / "out.xml")
        assert DI_NS in namespace_uris(tmp_path / "out.xml")

    def test_an_external_model_nothing_references_is_reported(self, tmp_path) -> None:
        result = run(
            tmp_path,
            [DATA / "ua_base.xml", DATA / "machinery.xml"],
            ["browseName=MSSControllerType"],
            external=[DI_NS],
        )
        codes = {finding.code for finding in result.validation.warnings}
        assert "EXTERNAL_MODEL_UNUSED" in codes
        assert DI_NS not in namespace_uris(tmp_path / "out.xml")

    def test_configuration_only_accepts_model_uris(self) -> None:
        base = {"nodesets": ["x.xml"], "roots": ["browseName=X"]}
        with pytest.raises(ValueError, match="namespace index or identifier"):
            LinkerConfig(**base, external=["ns=1;i=1000"])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="namespace index or identifier"):
            LinkerConfig(**base, external=["index:2"])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="not a namespace URI"):
            LinkerConfig(**base, external=["DI"])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="not empty"):
            LinkerConfig(**base, external=["  "])  # type: ignore[arg-type]
        assert LinkerConfig(**base, external=[DI_NS, DI_NS]).external == [DI_NS]  # type: ignore[arg-type]

    def test_a_modeluri_without_the_trailing_slash_is_the_same_dependency(self) -> None:
        # OPC 10000-5 writes a namespace URI with a trailing '/'.  A config that leaves it off
        # means the same model, and both spellings collapse into the URI documents declare.
        base = {"nodesets": ["x.xml"], "roots": ["browseName=X"]}
        without_slash = "http://opcfoundation.org/UA/DI"
        assert LinkerConfig(**base, external=[without_slash]).external == [DI_NS]  # type: ignore[arg-type]
        assert (
            LinkerConfig(**base, external=[DI_NS, without_slash]).external  # type: ignore[arg-type]
            == [DI_NS]
        )


class TestCli:
    @pytest.fixture
    def external_config(self, tmp_path) -> Path:
        config = tmp_path / "config.yaml"
        config.write_text(
            "\n".join(
                [
                    "nodesets:",
                    f"  - {(DATA / 'ua_base.xml').as_posix()}",
                    f"  - {(DATA / 'machinery.xml').as_posix()}",
                    f"roots:\n  - namespaceUri={Machinery_NS}",
                    f"external:\n  - {UA_NS}",
                    f"output: {(tmp_path / 'reduced.xml').as_posix()}",
                    "mode: STRICT",
                ]
            ),
            encoding="utf-8",
        )
        return config

    def test_link_lists_the_external_dependencies(self, external_config) -> None:
        result = CliRunner().invoke(main, ["link", str(external_config)])
        assert result.exit_code == 0, result.output
        assert "External dependencies" in result.output
        assert UA_NS in result.output

    def test_validate_reads_a_dependency_that_is_not_in_the_file(self, external_config) -> None:
        linked = CliRunner().invoke(main, ["link", str(external_config)])
        assert linked.exit_code == 0, linked.output
        checked = CliRunner().invoke(
            main, ["validate", str(external_config.parent / "reduced.xml"), "--schema", str(NODESET_SCHEMA)]
        )
        assert checked.exit_code == 0, checked.output
        assert "schema" in checked.output.lower()

    def test_validate_still_rejects_a_reference_without_a_dependency(self, tmp_path) -> None:
        """The reduced file refers to DI, but nothing declares it: a consumer would fail."""
        alpha = write(tmp_path, "alpha.xml", alpha_fixture())
        config = tmp_path / "config.yaml"
        config.write_text(
            "\n".join(
                [
                    "nodesets:",
                    f"  - {(DATA / 'ua_base.xml').as_posix()}",
                    f"  - {(DATA / 'machinery.xml').as_posix()}",
                    f"  - {alpha.as_posix()}",
                    f"  - {(tmp_path / 'di.xml').as_posix()}",
                    f"roots:\n  - namespaceUri={ALPHA_NS}",
                    f"external:\n  - {DI_NS}",
                    f"output: {(tmp_path / 'reduced.xml').as_posix()}",
                ]
            ),
            encoding="utf-8",
        )
        write(tmp_path, "di.xml", DI_FIXTURE)
        linked = CliRunner().invoke(main, ["link", str(config)])
        assert linked.exit_code == 0, linked.output
        checked = CliRunner().invoke(main, ["validate", str(tmp_path / "reduced.xml")])
        assert checked.exit_code == 1
        assert "REF_TARGET_MISSING" in checked.output
