"""The official-NodeSet compliance script must report failures, not success.

The script downloads megabyte-sized files, so the reliability parts are tested offline: the
schema check has to *count* failures, and what the run used has to be reported explicitly.
"""

from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from lxml.etree import XMLSchema, parse
from tests.conftest import DATA, NODESET_SCHEMA

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_official_nodesets.py"


@pytest.fixture(scope="module")
def script() -> Any:
    spec = importlib.util.spec_from_file_location("check_official_nodesets", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def broken_copy(source: Path, tmp_path: Path) -> Path:
    """A copy that loads fine but violates the schema, standing in for a bad official file."""
    copy = tmp_path / "broken.xml"
    copy.write_text(
        source.read_text(encoding="utf-8").replace("<UANodeSet", '<UANodeSet Bogus="1"', 1),
        encoding="utf-8",
    )
    return copy


def test_schema_failures_counts_a_bad_source(script, tmp_path) -> None:
    xsd = XMLSchema(parse(str(NODESET_SCHEMA)))
    assert script.schema_failures([DATA / "ua_base.xml"], xsd) == 0
    assert script.schema_failures([broken_copy(DATA / "ua_base.xml", tmp_path)], xsd) == 1
    assert script.schema_failures([DATA / "ua_base.xml", DATA / "machinery.xml"], xsd) == 0


def test_a_bad_source_cannot_pass_the_run(script, tmp_path) -> None:
    """The old script printed the failure and still exited 0; the failure must be counted."""
    xsd = XMLSchema(parse(str(NODESET_SCHEMA)))
    failures = script.schema_failures([broken_copy(DATA / "machinery.xml", tmp_path)], xsd)
    assert failures != 0


def test_run_reports_the_source_ref_and_versions(script) -> None:
    assert "{ref}" in script.BASE_URL
    assert script.BASE_URL.format(ref="1.05.05") == (
        "https://raw.githubusercontent.com/OPCFoundation/UA-Nodeset/1.05.05/"
    )
    assert script.sha256(DATA / "ua_base.xml")
    assert "http://opcfoundation.org/UA/Machinery/" in script.model_versions(DATA / "machinery.xml")
    assert "1.05.04" in script.schema_version(NODESET_SCHEMA)


def test_fetch_uses_a_separate_cache_for_each_ref(script, tmp_path, monkeypatch) -> None:
    requested: list[str] = []

    class Response(BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

    def fake_urlopen(url: str) -> Response:
        requested.append(url)
        return Response(url.encode("utf-8"))

    monkeypatch.setattr(script, "urlopen", fake_urlopen)

    first = script.fetch(tmp_path, "tag-a")
    assert len(requested) == len(script.OFFICIAL_FILES)
    second = script.fetch(tmp_path, "tag-b")
    assert len(requested) == 2 * len(script.OFFICIAL_FILES)
    assert set(first).isdisjoint(second)

    script.fetch(tmp_path, "tag-a")
    assert len(requested) == 2 * len(script.OFFICIAL_FILES)
    assert all(path.read_bytes().startswith(b"https://") for path in first + second)
