"""Import the reduced demo NodeSet into a local asyncua server."""

from __future__ import annotations

import argparse
import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

from asyncua import Server, ua
from asyncua.common.xmlimporter import XmlImporter

ROOT = Path(__file__).resolve().parent.parent
NODESET = ROOT / "demo1" / "reduced.xml"
SHOWCASE_NAMESPACE = "http://umati.org/UA/ShowCaseMachineTool/"
SHOWCASE_NODE_ID = 5003
ENDPOINT = "opc.tcp://127.0.0.1:4840/demo1/"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class DemoXmlImporter(XmlImporter):
    """Check external requirements while allowing models bundled in this NodeSet.

    asyncua checks every RequiredModel against the server before parsing the file, even
    when that model is declared in the same NodeSet. Check those in-file dependencies as
    bundled, and ask asyncua to validate each remaining external requirement separately.
    """

    async def _check_required_models(
        self, xmlpath: str | Path | None = None, xmlstring: str | bytes | None = None
    ) -> None:
        if (xmlpath is None) == (xmlstring is None):
            raise ValueError("Expected either a NodeSet path or XML content.")

        if xmlpath is None:
            if xmlstring is None:
                raise ValueError("XML content is required when no path is provided.")
            root = ET.fromstring(xmlstring)
        else:
            root = ET.parse(xmlpath).getroot()

        bundled_models = {
            element.get("ModelUri")
            for element in root.iter()
            if _local_name(element.tag) == "Model"
        }
        requirements = [
            element
            for element in root.iter()
            if _local_name(element.tag) == "RequiredModel"
        ]
        checked: set[tuple[tuple[str, str], ...]] = set()

        for requirement in requirements:
            model_uri = requirement.get("ModelUri")
            if model_uri in bundled_models:
                continue
            attributes = tuple(sorted(requirement.attrib.items()))
            if attributes in checked:
                continue
            checked.add(attributes)

            check_root = ET.Element(root.tag, root.attrib)
            ET.SubElement(check_root, requirement.tag, requirement.attrib)
            await super()._check_required_models(
                xmlstring=ET.tostring(check_root, encoding="utf-8")
            )


async def run(*, check_only: bool) -> None:
    if not NODESET.is_file():
        raise FileNotFoundError(
            f"Reduced NodeSet not found: {NODESET}\n"
            "Generate it first with: nodeset-linker link demo1\\config.yaml"
        )

    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("ShowCaseMachineTool demo")

    imported_nodes = await DemoXmlImporter(
        server, auto_load_definitions=False
    ).import_xml(xmlpath=NODESET)
    namespace_index = await server.get_namespace_index(SHOWCASE_NAMESPACE)
    showcase = server.get_node(ua.NodeId(SHOWCASE_NODE_ID, namespace_index))
    browse_name = await showcase.read_browse_name()
    if browse_name.Name != "ShowCaseMachineTool":
        raise RuntimeError(
            f"Imported root has unexpected BrowseName: {browse_name.Name!r}"
        )

    print(f"Imported {len(imported_nodes)} nodes from {NODESET}")
    print(f"Verified root: ns={namespace_index};i={SHOWCASE_NODE_ID} ({browse_name.Name})")
    print("Custom datatype Python classes were not generated (asyncua auto-load disabled).")

    if check_only:
        return

    await server.start()
    print(f"Server listening at {ENDPOINT} (Ctrl+C to stop)")
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="import and verify the NodeSet without starting the OPC UA endpoint",
    )
    args = parser.parse_args()
    asyncio.run(run(check_only=args.check_only))


if __name__ == "__main__":
    main()