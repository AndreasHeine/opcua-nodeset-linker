# Release checklist — 0.1.0 alpha

Use this as the gate before publishing `nodeset-linker`. Items marked `[x]` were verified
on 2026-11-01 in this working tree; the rest still need a decision or an action.

## 1. Decide the version string

- [ ] Pick the PEP 440 alpha form: `0.1.0a0` (or `0.1.0a1` for a second alpha).
  `pyproject.toml` currently says plain `0.1.0`, which PyPI treats as the **final** release —
  once uploaded you cannot re-upload `0.1.0`, and an "alpha" tagged as `0.1.0` installs as
  the newest version for everyone.
- [ ] Keep the git tag identical to the distributed version: `v0.1.0a0`.
- [ ] `git tag -a v0.1.0a0 -m "…"` after the metadata commit, and push tags
  (`git push origin master --tags`).

## 2. Package metadata

- [ ] Add a `LICENSE` file at the repository root. `pyproject.toml` declares
  `license = { text = "MIT" }` but there is no licence text in the repo.
- [ ] Add `Classifier: License :: OSI Approved :: MIT License`.
- [ ] Replace the placeholder author `OPC UA NodeSet Linker maintainers` with a real name and
  e-mail (`authors = [{ name = "…", email = "…" }]`).
- [ ] Add `[project.urls]` (repository, issues, documentation) — the repo has **no remote**
  configured yet (`git remote -v` is empty), so create/push one first.
- [ ] Add `CHANGELOG.md` with the 0.1.0-alpha entry: what exists (merge, closure, retention
  modes STRICT/CUSTOM/RUNTIME, external NodeSet dependencies, explain/stats/validate, XSD
  validation) and what is explicitly **not** promised yet.
- [ ] State the alpha status in the README headline: API, config keys, warning codes and the
  output shape may still change between alphas.
- [ ] Decide whether `asyncua` stays an optional extra only — it is not in the required
  dependency set and must not become importable from the package itself.

## 3. Clean the working tree

- [ ] Delete generated/ad-hoc output: `out/` (all XML, probe YAML, `_verify.py`, `_verify2.py`,
  `_tmp/`) and `scratch/chain_demo.yaml`. Both folders are ignored by `.gitignore`; nothing
  references their contents.
- [ ] Delete tool caches: `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, and every `__pycache__`
  directory (`src/`, `tests/`, `scripts/`).
- [ ] Keep `.venv` local; it is never committed (179 MB).
- [ ] Confirm the release commit contains only: `src/nodeset_linker/`, `tests/` (including
  `tests/data/` and `tests/data/schema/UANodeSet.xsd`), `pyproject.toml`, `README.md`,
  `LICENSE`, `.gitignore`, `docs/`, `examples/`, `scripts/check_official_nodesets.py`.
- [ ] Decide explicitly about `demo1/reduced.xml` (1.3 MB): keep it as the browsable demo
  result, or drop it and let `demo1/README.md` regenerate it with
  `nodeset-linker link demo1\config.yaml`. Either is valid — make the choice deliberate.
- [ ] Keep `demo1/nodesets/` (the 8 input files the demo config loads) and the 4 screenshots.

## 4. Documentation consistency

- [ ] Link `demo1/README.md` from the root `README.md` — the root README currently never
  mentions the demo folder.
- [ ] Link the four docs pages (`architecture.md`, `dependency-resolution.md`,
  `reduction-example.md`, `usage-guide.md`) from the README.
- [ ] Re-check every command and path in README and docs against the real CLI surface:
  `analyze`, `link`, `explain`, `stats`, `validate` — and every documented output path
  (`out/reduced.xml`, `out/custom.xml`, `out/runtime.xml`, `out/reduced_external_core.xml`,
  `demo1\reduced.xml`) is produced by an example config that exists.
- [ ] Re-check the documented numbers (node counts in `docs/reduction-example.md`) against a
  fresh run — numbers that drifted are worse than no numbers.
- [ ] Document the four external-dependency warnings by name: `EXTERNAL_MODEL_UNUSED`,
  `EXTERNAL_MODEL_NOT_DECLARED`, `EXTERNAL_MODEL_NOT_LOADED`, `EXTERNAL_MODEL_VERSION_UNKNOWN`,
  and that no version is ever invented.
- [ ] Say plainly that namespace 0 (`http://opcfoundation.org/UA/`) is never written to
  `NamespaceUris` and its ids stay unqualified.
- [ ] Keep the sentence that schema validation is not OPC UA certification.

## 5. Green checks — verified in this tree

- [x] `pytest` — 233 tests passed, 3.1 s.
- [x] Coverage: 95 % total (2465 statements, 133 missed); lowest spots are
  `validator/semantic.py` (96 %) and `utils/text.py` (86 %).
- [x] `ruff check .` — all checks passed.
- [x] `mypy src` — strict, no issues in 38 source files.
- [ ] `mypy tests scripts` — fails on module mapping: `conftest.py` is found twice
  (`conftest` vs `tests.conftest`). Fix by adding `tests/__init__.py`, or by setting
  `explicit_package_bases = true` / a `mypy_package` mapping in `pyproject.toml`, so CI can
  type-check the tests too.
- [ ] Run the checks on **Python 3.12** as well. The classifiers advertise 3.12 and 3.13, but
  this machine only has 3.13.14; an untested 3.12 claim in an alpha is a risk to fix or drop.
- [ ] Run the suite with the `asyncua` extra installed (`pip install -e ".[asyncua]"`) at least
  once, so the optional path is exercised.

## 6. Functional smoke test from a clean checkout

Do this in a fresh clone, not in the working directory:

- [ ] `python -m venv .venv` → activate → `pip install -e ".[dev]"`.
- [ ] `nodeset-linker analyze examples/config.yaml` — preview writes nothing.
- [ ] `nodeset-linker link examples/config.yaml` — writes `out/reduced.xml`, validation passes.
- [ ] `nodeset-linker link examples/config_custom.yaml`, `examples/config_runtime.yaml`,
  `examples/config_external.yaml` — all three export.
- [ ] `nodeset-linker stats out/reduced.xml` and
  `nodeset-linker validate out/reduced.xml --schema tests/data/schema/UANodeSet.xsd`.
- [ ] `nodeset-linker explain examples/config.yaml -n MSSControllerType` — dependency tree sane.
- [ ] Negative case still fails: a config whose root belongs to an `external:` model must raise
  a resolution error, and an undeclared reference target must block the export.
- [ ] `nodeset-linker link demo1\config.yaml` from the repository root, then
  `nodeset-linker validate demo1\reduced.xml --schema tests\data\schema\UANodeSet.xsd`.
- [ ] `python demo1\server.py --check-only` with the asyncua extra — the showcase root imports
  and the external Core requirement is reported, not silently satisfied.
- [ ] `python scripts/check_official_nodesets.py` (downloads into `scratch/`, ignored) — keep it
  out of the release commit.

## 7. Build and inspect the artifacts

- [ ] Install the build backend: `hatchling` is **not** installed in this environment
  (`pip show hatchling` → not found). Use `pip install build hatchling` or `pip install -e ".[dev]"`
  plus `python -m build`.
- [ ] `python -m build` → `dist/` contains wheel and sdist; add `dist/` to `.gitignore` (already there).
- [ ] Inspect the wheel: only `nodeset_linker` ships (`[tool.hatch.build.targets.wheel] packages =
  ["src/nodeset_linker"]`). Confirm `tests/`, `docs/`, `examples/`, `demo1/` are not inside it.
- [ ] Inspect the sdist: it should contain `README.md`, `LICENSE`, `pyproject.toml`, `src/`, and
  whatever you want reviewers to see; it must not contain `.venv`, `out/`, `scratch/`, caches.
- [ ] `twine check dist/*` — README renders, metadata parses.
- [ ] Install the wheel in a **fresh venv without the repository** and run the CLI on a small
  config you create there; this proves nothing depends on repo-internal paths at runtime.
- [ ] Confirm the console entry point works: `nodeset-linker --help` lists
  `analyze`, `link`, `explain`, `stats`, `validate`.

## 8. Publish and announce

- [ ] Release candidate commit: metadata, LICENSE, CHANGELOG, README links, cleaned tree.
- [ ] Tag `v0.1.0a0` and push commits **and** tags.
- [ ] Upload the alpha to PyPI (or to your internal index only) — an alpha is usually
  published so users can try it, not to be the default resolution.
- [ ] Create the release page with the wheel/sdist attached and the known limitations listed.
- [ ] Announce with: what works, what needs external NodeSets or SDK-provided Core, the alpha
  caveat, and how to report issues.

## 9. Alpha caveats worth stating in public

- [ ] No backwards-compatibility promise for config keys, warning codes or exported XML shape.
- [ ] The linker reduces only what was loaded: a model whose NodeSet files were not among
  `nodesets:` cannot appear in the output (`DATATYPE_MISSING_TARGET` and friends stay errors).
- [ ] Externalized models are referenced, never copied; their version metadata is reported
  missing rather than guessed.
- [ ] Custom datatypes still need the companion specification XSD when importing into an SDK;
  the linker copies `XmlSchemaUri`, not the schema itself.
- [ ] `IsNamespaceSubset=true` is a marker, not a certification statement.
