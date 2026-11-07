"""Version claims of a release: display spelling versus the comparable SemVer.

`Version` is display-only (OPC 10000-6 Annex F.2), `ModelVersion` is the field consumers compare
(OPC 10000-5 §12.41).  Published companion specs spell one release twice — `1.05.03` next to
`1.5.3` — so equality is judged on the numeric core, and a value nobody can parse is never
silently equal.
"""

from nodeset_linker.models.version import (
    canonical,
    is_semantic,
    normalized,
    version_precedes,
    versions_equal,
)


class TestNormalizedCore:
    """The tolerant read that judges what a release *means*, not how it is spelled."""

    def test_padded_display_spelling_normalizes_to_the_semver_core(self) -> None:
        assert normalized("1.04.0") == (1, 4, 0, "")
        assert normalized("1.05.07") == (1, 5, 7, "")
        assert normalized("1.4.0") == (1, 4, 0, "")

    def test_missing_segments_read_as_zero(self) -> None:
        assert normalized("1.4") == (1, 4, 0, "")
        assert normalized("2") == (2, 0, 0, "")

    def test_pre_release_is_kept_and_build_metadata_is_dropped(self) -> None:
        assert normalized("1.4.0-rc.1") == (1, 4, 0, "rc.1")
        assert normalized("1.4.0+build2") == (1, 4, 0, "")
        assert normalized("1.4.0-rc.1+build2") == (1, 4, 0, "rc.1")

    def test_values_that_are_not_numeric_dot_form_do_not_parse(self) -> None:
        for value in (
            "",
            "   ",
            None,
            "V2",
            "snapshot-3",
            "1.4.0a",
            "1.4.0-",
            "1.4.0+",
            "1..4",
            "1.4.0-alpha_1",
            "1.4.0+build_2",
        ):
            assert normalized(value) is None, value

    def test_whitespace_is_trimmed(self) -> None:
        assert normalized(" 1.4.0 ") == (1, 4, 0, "")


class TestSemanticSyntax:
    """§12.41 Table 303: `<numeric identifier>` forbids leading zeros and needs three segments."""

    def test_valid_semantic_versions(self) -> None:
        for value in ("1.4.0", "0.0.0", "1.4.0-rc.1", "1.4.0+build2", "10.20.30"):
            assert is_semantic(value), value

    def test_leading_zeros_and_short_forms_are_not_semantic(self) -> None:
        for value in ("1.04.0", "1.4", "1.4.0-01", "1.4.0-", "1.4.0+", "01.4.0"):
            assert not is_semantic(value), value

    def test_empty_or_unparseable_is_not_semantic(self) -> None:
        assert not is_semantic(None)
        assert not is_semantic("")
        assert not is_semantic("V2")


class TestCanonical:
    """The spelling a comparison sees, while reports keep showing what was written."""

    def test_padded_spelling_reads_as_the_comparable_one(self) -> None:
        assert canonical("1.04.0") == "1.4.0"
        assert canonical("1.05.07") == "1.5.7"
        assert canonical("1.4.0-rc.1") == "1.4.0-rc.1"

    def test_unparseable_spelling_stays_as_written(self) -> None:
        assert canonical("V2") == "V2"
        assert canonical(None) is None
        assert canonical("  ") is None


class TestEquality:
    """One release in two spellings is one release; anything else stays a string compare."""

    def test_display_and_semver_spelling_of_one_release_are_equal(self) -> None:
        assert versions_equal("1.04.0", "1.4.0")
        assert versions_equal("1.05.07", "1.5.7")
        assert versions_equal("1.00.1", "1.0.1")

    def test_different_releases_are_not_equal(self) -> None:
        assert not versions_equal("1.04.0", "1.5.0")
        assert not versions_equal("1.4.0", "1.4.0-rc.1")

    def test_unparseable_values_fall_back_to_string_compare(self) -> None:
        assert versions_equal("V2", "V2")
        assert not versions_equal("V2", "V3")
        assert not versions_equal("snapshot-3", "1.4.0")

    def test_a_claim_that_is_not_stated_is_not_equal_to_a_claim(self) -> None:
        assert not versions_equal("1.4.0", None)
        assert not versions_equal("1.4.0", "")
        assert versions_equal(None, "")


class TestPrecedence:
    """Annex F.2 comparison rules: the SemVer core decides, a pre-release is older."""

    def test_core_decides(self) -> None:
        assert version_precedes("1.04.0", "1.05.03") is True
        assert version_precedes("1.5.7", "1.5.7") is False
        assert version_precedes("2.0.0", "1.99.99") is False

    def test_pre_release_is_older_than_the_release(self) -> None:
        assert version_precedes("1.4.0-rc.1", "1.4.0") is True
        assert version_precedes("1.4.0", "1.4.0-rc.1") is False

    def test_pre_release_identifiers_compare_by_semver(self) -> None:
        assert version_precedes("1.4.0-alpha", "1.4.0-alpha.1") is True
        assert version_precedes("1.4.0-alpha.1", "1.4.0-alpha.2") is True
        assert version_precedes("1.4.0-alpha.10", "1.4.0-alpha.2") is False
        assert version_precedes("1.4.0-1", "1.4.0-alpha") is True
        assert version_precedes("1.4.0-alpha", "1.4.0-1") is False
        assert version_precedes("1.4.0-beta", "1.4.0-alpha") is False

    def test_identical_pre_release_identifiers_do_not_decide_the_order(self) -> None:
        # Equal identifiers carry no verdict; the next one does, and a longer tail is newer.
        assert version_precedes("1.4.0-alpha.2.1", "1.4.0-alpha.2.3") is True
        assert version_precedes("1.4.0-alpha.2", "1.4.0-alpha.2") is False

    def test_unparseable_versions_cannot_be_ordered(self) -> None:
        assert version_precedes("V2", "1.4.0") is None
        assert version_precedes(None, "1.4.0") is None
