# Changelog

Notes per released version, newest first. All releases are pre-release (`-alpha`), so a
behaviour change in the verdicts of a finding can land in any of them.

Rule ids are `BP-*` slugs that never encode a clause number of one paper revision; the cited
`§` numbers refer to OPC 11030 *Best Practices for Companion Models* v1.03.03, whose source
text ships in `docs/OPC-11030-v1.03.03.md`.

## 0.4.0-alpha — 2026-11-10

An audit pass over the input paths and the export: every XML read hardened, every refusal stated
instead of quietly pruned, and the strictness a consumer has to account for.

### Added

- **One hardened XML parser** (`utils/xml.py`) — `parse_xml` and `parse_fragment` read through a
  `SECURE_PARSER` that never resolves entity or DTD declarations (no expansion bomb), caps a
  document at 64 MiB and keeps network access off while parsing. Every reader in the package and in
  `scripts/` goes through it — the NodeSet parser, the CLI schema check, the exporter's embedded
  fragments, the official-nodeset script — so the hardening cannot be bypassed.
- **Exporter accounting per build** — `dropped` lists every identifier `build_document()` omits and
  `omitted` lists the class-illegal attributes it gates (a `Value` on a non-variable, an
  `InverseName` on a non-reference-type). Both reset at the start of every build, so they describe
  the document just built instead of accumulating across rebuilds.
- **`write()` takes an optional pre-built document** — `export()` reuses the document it probed
  instead of serialising a second time, so the hook a subclass overrides still runs.
- **Base-declared subtype links** (OPC 10000-6) — a base that declares `HasSubtype` from its own
  side (`IsForward="true"`) counts as a base. Graph and validator share one
  `forward_subtype_declarations` builder, so both classify the same hierarchy.
- **Identifier-keyed dependency report** — entries are keyed by `ExpandedNodeId.as_xml()` and the
  human name moved into a `labels` map: the same BrowseName in a vendor namespace and in the UA one
  is two entries, not one merged one, and the console renders the labels. A node typed by id
  `11616` counts as namespace metadata only when that type definition lives in the OPC UA namespace.
- **Supply-chain pins** — every `uses:` ref in `ci.yml` and `publish.yml` is pinned to a commit SHA
  with its version in a trailing comment, and `.github/dependabot.yaml` opens weekly PRs for the
  actions and for pip.
- **Hot paths that stopped rescanning** — duplicate member grouping counts once instead of a
  per-name `count()` scan, method argument lookup builds one index per side (`ModelIndex.arguments`)
  instead of walking the whole model per method, and namespace subset marking groups node counts and
  metadata objects in a single pass.
- **Official nodesets checked end to end** — `scripts/check_official_nodesets.py` validates the
  `--ref` shape before downloading, writes each download atomically through a `.part` file with a
  30 s timeout, and XSD- and semantically validates the *written* file as well as the sources.
- **Tests** — the suite grew to 708 (34 added): export refusals and gated attributes, a subtype
  chain deeper than the recursion limit, base-declared subtype links, identifier-keyed reports,
  strict config keys and selectors, hardened parsing and bundle paths, and the version a release
  states in two places.

### Changed

- **Config keys are checked** — `extra="forbid"` on `LinkerConfig` and `CustomRulesConfig`: a
  typo'd key (`ouput:`) fails the load instead of running with defaults. Custom-rule errors name the
  YAML spellings a user types (`namespace`, `browseName`, `nodeClass`, `modellingRule`), not the
  dataclass fields they fill.
- **`namespaceUri=` selectors are identity** — the full URI, a trailing slash tolerated on either
  side, instead of a suffix match. A partial selector that used to match now reports that it matched
  nothing, and a vendor namespace that merely *ends* with the wanted URI is no longer selected.
- **`modellingRule=` takes exact spellings** derived from the `ModellingRule` enum (`Optional`,
  `Mandatory`, `OptionalPlaceholder`, `MandatoryPlaceholder`, `None`), case-insensitive; globs are
  rejected there, and `None` selects rule-less nodes.
- **An export that cannot write an identifier refuses** — `export()` builds the document once and
  refuses the write when anything was dropped, so `validate_before_export: false` can no longer turn
  into silent data loss. Gated illegal attributes are recorded in `omitted`; the safe export still
  refuses only on identifiers that cannot be written.
- **Dependency-report JSON keys are identifiers** — `roots`, `dependency_origins`,
  `retention_reasons`, `mandatory_nodes` and `optional_nodes` carry `as_xml()` identifiers, and the
  human names come from the new `labels` map. A consumer that keyed entries by BrowseName has to
  read `labels`.
- **`prune_navigation` is now `repair_navigation`** — it rewrites the references of the nodes that
  are retained, it does not prune them.
- **A split document states only the model its source declared**, and the companion-file naming
  check compares spellings case-insensitively (the official DI file is `Opc.Ua.Di.NodeSet2.xml`
  although its alias is `DI`).
- **Bad inputs are reported, not raised** — `stats` and `validate` name the file that failed
  instead of printing a traceback, and a corrupt schema is a failure rather than a crash.
- **Hierarchy verdicts can move for documents that declare subtypes from the base side** — those
  links count as bases now, so the subtype hierarchy and its cycle check see edges the previous
  first-base-only walk missed.

### Fixed

- A subtype chain deeper than the recursion limit no longer raises `RecursionError`: the multi-base
  `HasSubtype` walk keeps an explicit enter/exit stack, and it walks every declared base rather than
  only the first, with a shared visited set so inheritance diamonds stay linear.
- A `QualifiedName` is qualified by the namespace URI, never by a document-local index.
- `nodeset_linker.__version__` still said `0.2.0-alpha` while the project shipped `0.3.0-alpha`; it
  now matches the project version, and a test pins the two together so a release cannot forget one.

### Scope of this release

The same boundary as 0.3.0-alpha: the tool judges semantic structure and best-practice rules. It
does not certify OPC UA conformance, and it does not verify runtime value/datatype compatibility or
server behaviour.

## 0.3.0-alpha — 2026-11-08

The semantic diff stopped being a report of differences and became a release gate: every
finding carries the best-practices rule behind it, and the tool answers whether a published
namespace may be reused.

### Added

- **Rule engine** (`diff/rules.py`) — one registry maps every change kind to a verdict with a
  severity bucket (BREAKING / ADVISORY / INFO), a stable rule id, a rationale that says why a
  consumer breaks, and the clause as provenance. 111 rule ids; `--json` reports each under
  `severity`, `rule_id`, `rationale` and `clause`, and the payload names the paper it judges.
- **Severities aligned to §3.2 and §3.2.17** — added structure fields, enum values, union
  alternatives and OptionSet bits are BREAKING (§3.2.4–§3.2.8); removing an optional
  InstanceDeclaration or detaching an Optional component is BREAKING (§3.2.16); a moved
  identifier is BREAKING (`BP-NodeId-Immutable`, §3.2.17/§11.3); `SymbolicName`, `InverseName`
  and field/alternative order dropped to ADVISORY (§2.1.2, §2.2, §3.2.17).
- **Method signatures** compared against both models (`BP-MethodSignature-Immutable`, §3.2.11),
  including the argument list spelled in an older-style property value; an argument variable's
  datatype change is stated once on the method, with argument metadata as information
  (`BP-MethodArg-Metadata`).
- **Hierarchy rules by where a type sits** — insertion inside a branching tree or the event
  hierarchy is BREAKING (§3.2.9, §3.2.15), a bottom addition advises, an inconsistent supertype
  or an enum under a non-enum base is BREAKING (§3.2.10), interface added advises and interface
  removed breaks (§3.2.3).
- **Renames, duplicates and direction** — a vanished field that returns under a new name is
  reported as `BP-Field-Renamed`, or `BP-Field-Rename-Guess` when the pairing is only a
  candidate; two members of one name are counted before name-keyed comparisons
  (`BP-Duplicate-Member`); `AllowSubTypes` and `MaxStringLength` are judged by direction,
  because rejecting values breaks a consumer while widening does not.
- **Release facts** — deprecation and replacement readings (`BP-Deprecation-*`,
  `BP-Type-Replaced*`, §14.2, §11.2.6), and Conformance Unit / Profile changes recognised by
  BrowseName in the companion namespace (`BP-CU-*`, `BP-Profile-*`) so they are detected
  without loading the OPC UA base nodeset.
- **Namespace release metadata** compared across releases (§11.1, §11.3) on the namespace entry,
  and version claims compared on their numeric core (`models/version.py`): OPC spells one
  release twice (`Version` display beside `ModelVersion` SemVer), so a re-spelled release
  reports nothing and `BP-Namespace-VersionDrift` fires only on claims naming different
  releases. Two documents disagreeing about one namespace are reported
  (`BP-Namespace-MetadataConflict`) instead of silently merged.
- **Release reuse verdict** — `--release-verdict` answers per NamespaceUri whether a release may
  reuse it or must publish a versioned one (§3.3, §11.3); `--baseline` points at a split-export
  manifest or a registry of published URIs, and without one the verdict says *baseline unknown*
  instead of guessing. A not-reusable verdict exits 1.
- **Waivers** — `--waive <rule-id>`, `--waiver-file` and `--accept-breaking` mark findings
  instead of deleting them, so severity, rule id and reason stay visible and only the gate
  reads the marker. Advisory and information findings are waivable; breaking findings are not,
  because waiving one would let a published NamespaceUri carry a break (§11.3).
- **Best-practices lint** — `validate --best-practices` judges a single model against §2–§15
  (19 checks) and reuses the diff's rule ids. Its findings are advisory/information and never
  change an exit code.
- **Impact by identifier** — `impacted_nodes` lists identifiers and `impacted_labels` the human
  labels; `impact_count` stays exact while a report lists at most `--impact-limit` (50 by
  default); `--context` counts consumers that live outside the two compared files; inheritance
  is followed three hops deep instead of every generation.
- **Diff inputs and scoping** — each side may be a NodeSet file *or* a split-export bundle (a
  directory or its `manifest.json`); `--namespace`, `--exclude-namespace` and `--node-class`
  scope both sides, and `--reduced-export` reads nodes whose namespace the export declares as a
  `RequiredModel` as externalized (§11.2) rather than removed (§3.2.16).
- **Contract markers** — JSON gained `schema_version` and `paper`; split-export `manifest.json`
  gained `schema_version` plus the release metadata of each namespace, which is what
  `--baseline` reads back (§11.1, §11.3).
- **Documentation** — the whitepaper source, the best-practices work plan, the clause↔rule
  alignment table, the extended semantic-diff document, an expose write-up and the UI concept
  with its mockup screens.
- **Tests** — seven test files added (`best_practices_rules`, `lint`, `waivers`, `version`,
  `json_contract`, `reduced_export`, `release_gate_checklist`) bringing the suite to 674 tests;
  the release-gate checklist now drives the CLI over real documents, and the JSON contract test
  pins the report keys. `pytest` measures branch coverage, so the untested half of an `if/else`
  counts as untested.

### Changed

- `--fail-on-breaking` now fails on blocking **namespace** findings too — a moved NamespaceUri
  or a removed namespace used to be reported as totals only and never gated.
- `impacted_nodes` changed meaning: identifiers instead of labels. Consumers that read labels
  must switch to `impacted_labels`. The same BrowseName in two namespaces is now two nodes, so
  impact totals can rise.
- `summary.breaking_changes` counts node findings and namespace findings; it is no longer equal
  to the length of the `breaking_changes` array.
- `external:` URIs are stored in their canonical spelling (trailing `/`, OPC 10000-5). A
  dependency spelled without the slash used to match nothing and silently externalize nothing;
  it now matches, so reduction output can be smaller than before.
- An unmatched `--namespace` is an error instead of a report that says "No semantic differences
  found" and exits 0. An unmatched `--exclude-namespace` is a note on stderr.
- A failing gate states why on stderr (`Returning exit code 1: …`) before exiting.
- `--output` creates missing parent directories, so a CI job may write `dist/diff.html`.

### Fixed

- One identifier move is reported once. Re-numbered nodes were compared against their new
  identifier while references, base types, interface sets and method declarations still carried
  the old one, so a single move also read as a detached child, a hierarchy move, a datatype
  change and a signature change.
- `HasArgumentDescription` / `HasOptionalInputArgumentDescription` are documentation about an
  argument (§5), not a contract reference: they now report as information, so a documentation-only
  edit no longer fails a release gate.
- An inconsistent re-declaration of one identifier no longer aborts the diff; the merge keeps the
  first declaration and the report states the conflict as an advisory `BP-Merge-Conflict`.
- Renumbered pairs carry pairing confidence (`exact` / `heuristic`), so a guessed pairing is
  visible to a reviewer instead of silent.

### Scope of this release

The tool judges semantic structure and best-practice rules. It does not certify OPC UA
conformance, and it does not verify runtime value/datatype compatibility or server behaviour.

## Earlier releases

`0.1.0-alpha`, `0.1.1-alpha` and `0.2.0-alpha` predate this changelog: the linker, reduction,
external dependency handling, split export and the first semantic diff shipped in them. See the
[README](README.md) for the current feature set and the tag history on GitHub.
