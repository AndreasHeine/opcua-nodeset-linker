"""Check the linker against the official OPC UA NodeSet files.

Downloads the standard nodesets into ``scratch/nodesets`` (if they are not there), then runs
them through the linker and validates both the sources and the produced file against the
official ``UANodeSet.xsd``.  Nothing here is a unit test: the files are several megabytes and
come from the network, so this runs by hand.

    python scripts/check_official_nodesets.py
    python scripts/check_official_nodesets.py --root browseName=IMachineryEquipmentType
    python scripts/check_official_nodesets.py --ref 1.05.05   # pin the nodeset source

The exit status is 0 only when *everything* passed: every source against the XSD and
semantically, the reduction, and the written file.  A source that does not validate is a
failed run, not a footnote.  Which sources were used is printed, because ``latest`` moves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen

from lxml.etree import XMLSchema, parse

from nodeset_linker.config.schema import LinkerConfig
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.validator.semantic import SemanticValidator

BASE_URL = "https://raw.githubusercontent.com/OPCFoundation/UA-Nodeset/{ref}/"
OFFICIAL_FILES = [
    "Schema/Opc.Ua.NodeSet2.xml",
    "DI/Opc.Ua.Di.NodeSet2.xml",
    "Machinery/Opc.Ua.Machinery.NodeSet2.xml",
    "IA/Opc.Ua.IA.NodeSet2.xml",
]
SCHEMA = Path("tests/data/schema/UANodeSet.xsd")


def fetch(directory: Path, ref: str) -> list[Path]:
    """Fetch/cache official files in a directory unique to the requested ref."""
    cache_key = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    cache_directory = directory / cache_key
    cache_directory.mkdir(parents=True, exist_ok=True)
    base = BASE_URL.format(ref=ref)
    paths: list[Path] = []
    for remote in OFFICIAL_FILES:
        path = cache_directory / Path(remote).name
        if not path.exists():
            with urlopen(base + remote) as response:
                path.write_bytes(response.read())
            print(f"downloaded {remote} ({path.stat().st_size} bytes)")
        paths.append(path)
    return paths


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def model_versions(path: Path) -> str:
    """The ``Model`` entries a file declares: what version of the model it is."""
    root = parse(str(path)).getroot()
    entries = [
        f"{child.get('ModelUri')}"
        f" v{child.get('Version') or '?'} ({child.get('PublicationDate') or '?'})"
        for child in root.iter()
        if child.tag.rpartition("}")[2] == "Model"
    ]
    return "; ".join(entries) or "no Model declaration"


def schema_version(xsd_path: Path) -> str:
    """The version of the schema itself, from its own annotation."""
    root = parse(str(xsd_path)).getroot()
    for child in root.iter():
        if child.tag.rpartition("}")[2] == "Model":
            version = child.get("Version")
            date = child.get("PublicationDate")
            return f"{child.get('ModelUri')} v{version} ({date})"
    return "unknown schema version"


def xsd_ok(path: Path, xsd: XMLSchema) -> bool:
    document = parse(str(path))
    ok = xsd.validate(document)
    if not ok:
        print(f"  {path.name}: {str(xsd.error_log)[:500]}")
    return ok


def schema_failures(paths: list[Path], xsd: XMLSchema) -> int:
    """How many of ``paths`` the schema rejects — counted, so a failure cannot pass."""
    return sum(0 if xsd_ok(path, xsd) else 1 for path in paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="scratch/nodesets", help="where the official files live")
    parser.add_argument(
        "--ref",
        default="latest",
        help="UA-Nodeset branch, tag or commit to download from (default: latest, which moves)",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=["browseName=IMachineryEquipmentType"],
        help="root selector for the reduction (repeatable)",
    )
    options = parser.parse_args()

    if not SCHEMA.exists():
        print(f"missing schema: {SCHEMA}")
        return 1
    xsd = XMLSchema(parse(str(SCHEMA)))
    print(f"schema: {SCHEMA} - {schema_version(SCHEMA)}")
    print(f"source: OPCFoundation/UA-Nodeset ref={options.ref}")

    sources = fetch(Path(options.dir), options.ref)
    failures = schema_failures(sources, xsd)
    for source in sources:
        print(
            f"{source.name}: sha256 {sha256(source)}, {model_versions(source)},"
            f" schema valid = {xsd_ok(source, xsd)}"
        )
    model = load_nodesets([str(source) for source in sources])
    print(f"merged model: {model.node_count()} nodes, {len(model.namespaces)} namespaces")
    source_report = SemanticValidator(model).validate()
    failures += len(source_report.errors)
    print(f"source semantics: {source_report.summary()}")

    config = LinkerConfig(
        nodesets=[str(source) for source in sources],
        roots=options.root,
        output=str(Path(options.dir) / "reduced.xml"),
    )
    result = LinkerPipeline(config).run()
    output = Path(config.output)
    output_failures = schema_failures([output], xsd)
    failures += output_failures
    print(
        f"reduction: kept {len(result.retained)} of {result.model.node_count()},"
        f" schema valid = {output_failures == 0}"
    )
    print("statistics:", json.loads(result.statistics.as_json()))
    namespaces = json.loads(result.namespace_report.as_json())
    print("namespace subsets:", namespaces["namespace_subsets"])
    print("subset markers written:", namespaces["subset_markers_written"])

    reloaded = load_nodesets([str(output)])
    print(f"the written file reloads on its own: {reloaded.node_count()} nodes")
    print("PASS" if failures == 0 else f"FAIL: {failures} failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
