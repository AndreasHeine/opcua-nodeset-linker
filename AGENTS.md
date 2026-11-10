# AGENTS.md — `opcua-nodeset-linker`

Python 3.12+ library + Click CLI (`nodeset-linker`) that reduces OPC UA NodeSets, validates them, and
diffs two semantic models. `src/nodeset_linker/` is the only package; `pyproject.toml` uses hatchling
and exposes `nodeset-linker = nodeset_linker.cli.main:main`.

## Environment (Windows, easy to get wrong)

- Run everything through the venv: `.venv\Scripts\python.exe` and `.venv\Scripts\nodeset-linker.exe`,
  from the repository root.
- The global Windows Store Python has a **stale installed `nodeset_linker`** (0.1.1-alpha) in
  `site-packages`; plain `python` imports that copy instead of `src/`. It fails outright for the
  diff layer (`ModuleNotFoundError` for `nodeset_linker.diff.rules` and `nodeset_linker.utils.xml`,
  `ImportError` for symbols like `Baseline`, `PAPER`, `SCHEMA_VERSION`) and fails *silently* for
  modules the old copy still has, so the run looks green while testing the installed build. Never
  verify a change with the interpreter outside `.venv`.
- CLI output needs `set PYTHONIOENCODING=utf-8` (`→` and box-drawing otherwise crash the cp1252
  console with `UnicodeEncodeError`; `§` encodes fine).
- In this shell, avoid `findstr`/`tail`/`grep` pipelines and `python -c "..."` (quoting breaks); write a
  throwaway probe script under the temp dir instead.

## Commands

```bash
.venv\Scripts\python.exe -m pytest --no-cov -q          # fast suite (~5s, ~707 tests)
.venv\Scripts\python.exe -m pytest tests/test_diff.py::TestCli -q   # one file/class/test
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m mypy src
```

- `pyproject.toml` `addopts` always enables `--cov --cov-branch --cov-report=term-missing`; add
  `--no-cov` for iteration, and run without it for the real gate. Branch coverage counts: an
  untested half of an `if/else` is untested.
- CI (`.github/workflows/ci.yml`) = pytest on 3.12/3.13, plus `ruff check src tests scripts` and
  `mypy src` on 3.12. Local order that mirrors it: ruff → mypy → pytest. `mypy` is strict; `lxml.*`
  stubs are ignored on purpose. All workflow actions are SHA-pinned with a `# vN` comment — when
  bumping, replace the SHA and keep the comment (`.github/dependabot.yaml` opens weekly PRs for
  actions and pip).
- `.venv\Scripts\python.exe scripts\check_official_nodesets.py` is **manual**, not a test: it downloads
  the official OPC UA/DI/Machinery/IA nodesets into `scratch/` — the `--ref` must be
  tag/branch/commit-shaped, downloads are atomic via a `.part` file with a 30s timeout — and validates
  the sources *and* the written file against `tests/data/schema/UANodeSet.xsd` plus semantics. Run it
  through the venv: its imports (`config`, `pipeline`, `repository`, `validator`) all resolve in the
  stale global copy, so plain `python` validates the old installed build and still prints PASS/FAIL.
  Do not add it to CI.

## Architecture facts worth knowing before editing

- **XML is never compared.** Node identity is `(namespaceUri, identifier, serverUri)`; the parser
  resolves document-local `NamespaceIndex`/`ServerUris` to URIs, so reordered or re-indexed documents
  diff as identical. `diff/engine.py` compares `SemanticModel` objects only.
- **XML enters only through `utils/xml.py`.** Every reader — the NodeSet parser, the CLI schema check,
  the official-nodeset script and the exporter's embedded fragments — goes through `parse_xml` /
  `parse_fragment` on `SECURE_PARSER` (entities and DTDs never resolved, no network, 64 MiB size cap).
  Do not add raw lxml `parse`/`fromstring` call sites in `src/` or `scripts/`; the hardening must stay
  unbypassable. Tests may parse trusted fixtures with the default parser.
- **Export refuses instead of silently pruning.** The raw `NodeSetExporter` records, per
  `build_document()` call, the identifiers it omits (`dropped`) and the class-illegal attributes it
  gates (`omitted`: a `Value` on a non-variable, an `InverseName` on a non-reference-type). The safe
  `export()` builds the document once and refuses the write when `dropped` is non-empty — turning
  `validate=False` off must never become silent data loss.
- **The rule table is the public contract.** `diff/rules.py` maps internal change kinds to
  `RuleVerdict(severity, rationale, rule_id, clause)` via `verdict(kind, RuleContext)`. `BP-*` rule ids
  and clauses are user-facing (CLI `--waive`, waiver files, CI, docs) — keep them stable and add a
  registry entry rather than inventing an ad-hoc verdict in the engine. `verdict()` falls back to
  `_NEUTRAL` (`BP-Change-Neutral`, INFO) for an unknown kind, so a mistyped kind in `engine.py`
  downgrades the finding instead of failing — assert the rule id in a test when adding a kind.
- **Namespace findings live on `NamespaceChange`, not in the flat breaking list.** Summary/advisory
  counts combine `diff.advisory` + advisory namespace entries (`render.py::_summary_lines`), and
  `--no-advisory` hides the rule but keeps namespace facts. Exit-code gates are in
  `cli/main.py` (`blocking_breaking()`, `blocking_namespaces()`, `blocking_advisory*()`);
  `--fail-on-breaking` must stay 0 for advisories, and a release verdict that is not reusable exits 1.
- **Version claims compare on the numeric core** (`models/version.py`): display `Version` (`1.04.0`)
  beside SemVer `ModelVersion` (`1.4.0`) is one release and reports nothing; only differing releases are
  `BP-Namespace-VersionDrift` (advisory, never breaking), an invalid `ModelVersion` is
  `BP-Namespace-ModelVersionSyntax`. Unparseable values fall back to string compare.
- **Internal identity keys are text, not tuples.** `diff/engine.py` keys hierarchies, moves and
  reference pairing by `namespace␟identifier␟server` (`_IDENTITY_SEPARATOR`, a control character:
  `|` is legal inside a string NodeId). Reports spell identifiers with `ExpandedNodeId.as_xml()`,
  never with this encoding — don't put identity text into user-facing output. `DependencyReportData`
  follows the same principle: JSON keys are `as_xml()` identifiers and the human label lives in the
  `labels` map.
- **Selectors and config are strict.** `namespaceUri=` matches the full URI exactly — trailing slash
  tolerated on either side, suffix matching would select vendor namespaces that merely *end* with the
  wanted URI. `modellingRule=` takes exact case-insensitive `ModellingRule` spellings derived from the
  enum (`None` selects rule-less nodes; `NO_RULE`'s *value* is `None` and enum members are truthy, so
  detect the member, never the value). Configs reject unknown keys (`extra="forbid"`), and rule errors
  name the YAML spellings a user types, not the dataclass fields.
- `validate` and `stats` take **one file**; only `diff` accepts a split-export bundle directory
  (`manifest.json`).
- Severity buckets are BREAKING / ADVISORY / INFO; advisory findings are review hints and must not
  become breaking. Waivers mark findings without deleting them — the report keeps severity, rule id and
  a `[waived]` marker; breaking findings are not waivable.

## Tests and examples

- Tests use fixtures under `tests/data` (`ua_base.xml`, `machinery.xml`, `legacy_namespace.xml`, the XSD)
  via `tests/conftest.py`, not the editable files in `examples/`. Demo configs, generated outputs and
  `examples/demo1/server.py` are not test dependencies.
- Committed example artifacts **are regenerated by hand** when rules or rendering change:
  `examples/demo3`/`demo4` hold `diff.md`/`diff.json` produced by
  `nodeset-linker diff <old> <new> --impact --markdown|--json --output ...`. A rule change that
  leaves stale artifacts makes the docs contradict the tool; regenerate and fix the README narrative
  in the same pass. Generated scratch output goes to `out/` (git-ignored).

## Docs

- `docs/OPC-11030-v1.03.03.md` is the shipped paper every clause citation refers to; rule rationales
  cite its `§` numbers (e.g. §3.2.11, §11.3). `docs/best-practices-alignment.md` tracks clause ↔ rule
  coverage; `docs/semantic-diff.md` holds the open engineering gaps — keep both in sync when adding or
  re-severitizing a rule.
- `docs/semantic-diff.md` is CRLF with **mixed continuation indentation**: §4 bullets continue at 2
  spaces, §5.1–§5.2 and §7.1–§7.2 at 3, §5.3–§5.8 and §7.3–§7.5 at 4 (one §7.5 bullet at 5). The
  edit tool matches CRLF either way, but an `oldString` with the wrong indent does not match — check
  the subsection's indent first, or replace a single line instead.

## Git

`main` tracks `origin` (github.com/AndreasHeine/opcua-nodeset-linker). Do not commit or push unless
asked; leave changes in the working tree, and remember new files (`git add`-less) stay untracked.
