# Exposé: `opcua-nodeset-linker`

**The DevOps solution for OPC UA Information Models: Automates validation, reduction, and
release management without a server, for machine builders and standardization consortia.**

**License:** Apache 2.0 (Open Source)  
**Status:** Project version `0.4.0-alpha` (see `pyproject.toml`)

---

## 1. The Problem

OPC UA is the industry standard for secure networking in automation. It is defined as a
*semantic model*, but published as an *XML file*. The path from modeling to delivery is fragmented:

- **Machine builders** load Companion Specs from consortia (Machinery, DI, Diagnostics, PackML), which include all their dependencies — but they often need only a fraction of them. They reduce manually, errors occur silently.
- **Control System Editors** (engineering software like TIA-Portal, Studio 5000, CodeSys) must validate models before publication — for breaking changes, compliance with rules, dependency closure. Today this happens through workarounds or not at all.
- **Standardization consortia** (Machinery Working Group, UTIMAT, Diagnostics) publish new releases of Companion Specs and need changelogs that explain what changes *for the consumer* — not what changes in the XML.
- **Interface governance** is invisible: If a model contains breaking changes, consumers only learn this at live failure, not in CI.

Result: OPC UA models today are as error-prone as code without tests.

---

## 2. The Solution: What is `opcua-nodeset-linker`?

A **CLI tool and Python library** that treats OPC UA Information Models like *code*:
- Reads NodeSet files (OPC 10000-6), builds a semantic dependency graph
- Validates models without stack and without server — purely based on file structure
- Reduces large specs to a configured subset, explains why each node is retained
- Compares two models and classifies changes by severity (Breaking / Advisory / Info)
- Generates machine-readable reports (JSON) and human-readable documentation (Markdown, HTML)
- Runs in CI/CD pipelines, blocks faulty releases, documents review decisions

**Core feature:** The tool needs **no running server, no OPC UA Stack, no client library** — just the NodeSet XML files themselves.

---

## 3. What It Can Do Today (Product Features)

### A. Reduction with Explanation (`link`, `analyze`, `explain`)

**Problem:** Machine builders inherit Companion Specs with 2000+ nodes. They need perhaps 500. Manual deletion leads to broken references, dead code, and loss of compliance.

**Solution:**
```bash
nodeset-linker link config.yaml  # Reduces to roots, explains dependencies
nodeset-linker explain --node JobStateEnum config.yaml  # Why is this node in the output?
nodeset-linker analyze config.yaml  # Dry run without writing
```

- **4 reduction modes:** `STRICT` (roots only), `OPTIONAL_AWARE` (also optional declarations), `RUNTIME` (for instantiable types), `CUSTOM` (wildcard matching)
- **Externalization:** Declares OPC UA Core or DI as `external:` → References remain, declarations are omitted
- **Traceability:** Every node in the output is justified with a dependency chain
- **In the example:** 7 Companion Specs → 1110 nodes in 8 namespaces, all dependencies resolved

### B. Validation without Server (`validate`)

**Problem:** OPC UA models already contain in XML all the information a server checks. But there is no tool to validate before the first server starts. Errors only appear to the end user.

**Solution:**
```bash
nodeset-linker validate reduced.xml --schema
nodeset-linker validate reduced.xml --best-practices  # 25 additional OPC-11030 rules
```

**Validation scope** (all without server):
- Reference targets (does the target exist? Is it a reference type?)
- Type definitions and datatype references, encoding declarations
- Reference type and subtype hierarchies (including cycle detection)
- Namespace links and subset markers (§11.2 OPC-11030)
- External dependencies (`RequiredModel`)
- **Best Practices** (25 lint rules per OPC-11030):
  - Recursive mandatory fields (609 occurrences in demo export — no server shows this as an error!)
  - Naming conventions, browse-path collisions
  - CU and profile declarations, deprecation traces (§14.2/§14.3)
  - Version consistency (`Version` vs `ModelVersion`)

**Exit-code logic:**
- Validation errors block (Exit 1)
- Best-practices are review hints, don't block (Exit 0)
- A reviewer sees *all* findings, the pipeline gates only on real errors

### C. Semantic Diff and Release Gate (`diff`)

**Problem:** When a machine builder or consortium publishes a new version, the consumer needs a changelog — not about XML diffs, but about *semantic meaning*: Which fields changed? Which nodes disappeared? Will my clients still work?

**Solution:**
```bash
nodeset-linker diff v1.xml v2.xml --impact --markdown
nodeset-linker diff v1.xml v2.xml --impact --json > diff.json
nodeset-linker diff v1.xml v2.xml --release-verdict --baseline baseline.json
```

**Classification** (113 rules, each with severity, rule ID, clause):
- `BREAKING`: Breaks existing clients (e.g., fields removed, DataType changed)
- `ADVISORY`: Should be noted, doesn't break (e.g., optional field added, node renamed)
- `INFO`: Transparent change (e.g., re-indexing, reordering)

**For each change, reported:**
- What changed (node ID, old/new declaration)
- Why it changed (rule ID: `BP-FieldOrder-Changed`, `BP-DataType-Changed`)
- Which clause of the spec forbids or allows it (e.g., §3.2.4 "Field order immutable")
- Impact: How many downstream nodes are affected?
- Namespace metadata: `Version`, `ModelVersion`, `PublicationDate`, `RequiredModel` (SemVer-aware)

**Release gate:**
```bash
nodeset-linker diff v1.xml v2.xml --release-verdict --baseline published-versions.json
# Checks: Can this NamespaceUri be reused?
# (§3.3, §11.3 OPC-11030: Breaking requires a new namespace)
```

**In the examples:**
- Demo3: 1110 nodes unchanged, but 2 breaking changes → Impact 78 nodes
- Demo4: 1 node removed, 6 breaking changes → Impact 494 nodes

---

## 4. What It Cannot Do Yet (But Could)

**Medium-term expandable:**
1. **Split-bundle support for validation** — today only for `diff`; goal: namespace exports are releases, not files
2. **Live-server comparison** (optional `asyncua` extra) — compare a running server's address space against NodeSet
3. **AI integration as pipeline step** — transform JSON report into changelog; each point traceable (node ID, rule, clause)
4. **Distinguishable exit codes** — pipeline can differentiate "breaking" from "advisory" from "baseline unknown"
5. **Facets and feature-sets** — not covered today, but extensible within OPC UA

---

## 5. Why This Is Transformative for Control System Editors

Control System Editors (TIA-Portal, Studio 5000, CodeSys) manage millions of nodes in production facilities. Their requirements:

| Use Case | Today | With the Tool |
|----------|-------|------------|
| **Validate model upgrade** | Manual against docs, errors possible | `diff v1 v2 --impact` shows all breaking changes with node impact |
| **Verify dependency closure** | With stack/server or hope | `validate --best-practices` checks in build before facilities deploy |
| **Model reduction** | Manual + hand tests | `link` with `--retain` and `--external` is declarative and justified |
| **Release notes** | Hand work or XML-diff parsing | `diff --markdown` delivers structured changelogs, `--json` input for AI-generated changelogs |
| **Enforce versioning policy** | Hope/review | `diff --release-verdict` blocks breaking releases without new namespace |

The tool runs in GitHub Actions, GitLab CI, Jenkins — any pipeline. It needs no infrastructure, no license, no special rights.

---

## 6. Why This Is Transformative for Machine Builders and Integrators

**Machine builders** are clients. They load OPC UA Companion Specs and build their models on top:

| Problem | Solution |
|---------|----------|
| **"Why is this node in the output? Can I delete it?"** | `explain --node NodeId config.yaml` shows the dependency chain |
| **"My model is too large, clients are too slow."** | `link` with roots reduces automatically, `analyze` shows the size |
| **"I don't know if my model is valid before the first server starts."** | `validate --best-practices` checks recursive mandatory fields, reference targets, CU members |
| **"The new Machinery version has breaking changes — my clients will fail."** | `diff old.xml new.xml --impact --context my-model.xml` shows exactly how many of my nodes are affected |
| **"Migration between toolchains?"** | `diff old-export.xml new-export.xml` from export A to B: pure re-indexing shows as "No semantic differences" |
| **"I need to document the model for the customer."** | `diff --markdown` with tables and clause references — copy-paste into release document |

---

## 7. Why This Is Transformative for Standardization Consortia

OPC UA Companion Specs are published by consortia: Machinery, DI, Diagnostics, PackML, UTIMAT, ISA95-Jobs.
They need:

| Use Case | Implementation |
|----------|-----------------|
| **Validate draft before publication** | `validate + --schema + --best-practices` is the CI gate for every draft |
| **Enforce OPC-11030 rules compliance** | 25 best-practice rules validate naming, versioning, CU declarations, deprecation without server |
| **Enforce versioning policy** | `diff --release-verdict` against baseline manifest: breaking requires new namespace (§11.3) |
| **Review documentation** | Markdown/HTML reports with rule IDs and clause numbers are versionable and citable |
| **Changelog for consumers** | `diff --json` is input for automated release notes; every point traceable to rule and node |
| **CU and profile governance** | `validate --best-practices` finds CU declarations (not bound to hardcoded IDs), checks consistency |
| **Deprecation trace** | `validate --best-practices` counts deprecated nodes per namespace, `diff` notices missing successors |

A committee can validate models and gate releases, **without any stack having been implemented**.

---

## 8. Concrete Differentiators (What No Other Tool Offers)

### A. Semantic Graph, Not XML

Most OPC UA tools work at the XML level. This tool builds a **semantic dependency graph**:
- Node identity is `(namespaceUri, identifier, serverUri)`, not an XML position
- Re-indexing or re-ordering XML → no difference in diff
- External models are declarable (`external:`), not an error
- The graph is machine-readable and programmable

### B. Validation Without Server

No other tool can **offline** check:
- Recursive mandatory fields (609 occurrences in demo export — **not a single server catches this at instantiation!**)
- Browse-path collisions across namespaces
- CU membership and profile consistency
- Compliance with 25 OPC-11030 rules

All without stack, without client, without network.

### C. Deterministic Reduction with Explanation

Reduction tools exist. `opcua-nodeset-linker` explains:
- Why each node is retained (dependency chain)
- What is external (must be supplied by consumer)
- Which nodes are affected by it (`--impact`)

This is declaratively configurable (`config.yaml`), not an ad-hoc list of node IDs.

### D. Release Governance Through Diff

The semantic diff is not textual. Each change is classified:
- Why is it breaking? (rule ID and clause)
- Which downstream nodes are affected? (impact)
- Can I reuse the `NamespaceUri`? (`--release-verdict`, §11.3)

This is the only tool that can automatically answer "can I publish this?"

### E. AI-Ready Reports

The JSON report is structured:
```json
{
  "schema_version": "1.0",
  "paper": "OPC-11030-v1.03.03",
  "summary": {
    "added": 5,
    "removed": 2,
    "breaking_changes": 3,
    "advisory_changes": 7
  },
  "findings": [
    {
      "node_id": "ns=2;i=1234",
      "change_kind": "DataTypeChanged",
      "rule_id": "BP-DataType-Changed",
      "clause": "§3.2.11",
      "severity": "BREAKING",
      "impact": 78
    }
  ]
}
```

A language model can generate changelogs from this; the statements are verifiable (rule ID, node, impact).

---

## 9. Market Positioning

### Why This Project Is Valuable

**OPC UA is not optional:** It's the industry standard for secure networking. Every Industry 4.0 factory, every smart machine, every engineering system is built on it.

**The problem is unsolved:** Today there are:
- **XSD validators** — check syntax, not semantics
- **OPC UA stacks (paid)** — expensive, for production, not for CI
- **Proprietary tools (from editors)** — tightly bound to one product, not interchangeable
- **Nothing** for reduction with explanation, compliance without server, release governance

**This tool fills this gap:** Apache 2.0, integrable into every pipeline, no licensing model, no dependencies.

### Target Groups (B2B2C)

1. **Control System Editors** (TIA-Portal, Studio 5000, CodeSys) — model management, validation, release gates
2. **Machine builders and integrators** — model reduction, upgrade checks, documentation
3. **Standardization consortia** (Machinery, DI, Diagnostics, PackML) — draft review, release governance, changelog automation
4. **OPC UA server vendors** — can integrate the tool into their test suite
5. **Testing tools / conformance testers** — can build semantic checks on top

---

## 10. Open Source as Strategy

The project is **Apache 2.0 licensed**. This means:
- Freely usable, also commercially
- No license fees, no seat locks
- Modifications possible, return is not enforced
- Industry standard for demanding projects (Kubernetes, Android, TensorFlow follow the same model)

**Interesting for sponsors:**
- Co-shape a standard tool that enters the industry
- Branding in the repository and community (contributor name)
- Use in own product or infrastructure
- Long-term maintainability through a decentralized model (not tied to one person)

---

## 11. Conscious Limitations of the Current State

The tool **does not start a server** and does not instantiate an address space:
- Semantic validation also *against a real stack* remains optionally possible
- Live-server comparison is on the roadmap (`--live-server`)

Additional open points (see `docs/semantic-diff.md`):
- Split-bundle support for `validate`/`stats` (today only for `diff`)
- Facets and feature-sets (extensible, not in current state)
- Distinguishable exit codes per error class (in backlog)

All intentionally designed that way, not "not yet implemented".

---

## 12. Conclusion: Why This Project Is Valuable

OPC UA is complex; tools are fragmented; errors only appear in production. This project brings
**DevOps principles to OPC UA modeling:**

- **Shift left:** Model errors are caught in CI, not at the customer
- **Declarative governance:** Configurable reduction, versioning policy as code
- **Traceability:** Every change is traceable to rule, clause, and node
- **Open:** Apache 2.0, integrable into every pipeline, no dependencies on proprietary stacks

For investors and sponsors, this is attractive because:
1. It solves a real, unsolved problem in an established industry
2. Open Source makes it a long-term stable resource
3. Early adoption by consortia and editor vendors is likely
4. The combination of automation + AI-ready reports provides a sustainable foundation for follow-on solutions

