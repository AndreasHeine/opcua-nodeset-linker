# UI Concept — Browser-Driven, Guided Interface for the NodeSet Linker

A concept for a web frontend that runs **entirely in the browser**: the user creates a workspace,
imports NodeSet XML files by drag & drop or the file picker, configures roots and retention policy,
reviews analysis and diff, and downloads the reduced NodeSet — no server, no install, no upload.

The UI is a **guided process**, not a dashboard: every step states what is missing before the next
step is possible, and nothing dead-ends (a failed import shows a diagnosis, not a blank screen).

Status: **concept / design proposal**. Nothing here is implemented in the product. The operable
reference is [`ui-mockup-browser.html`](ui-mockup-browser.html) — a German wizard with real browser
file import, live closure, validation, diff and Blob downloads. This document describes that shape
as the primary design; the committed screens `UI-Mockup1.png` … `UI-Mockup7.png` (one per wizard
step, §4.1) remain as the visual design probe.

The mockup parses XML in JavaScript on purpose. That is allowed here as a **preview tier** — see §2
for the engine boundary that keeps the Python engine authoritative for the exported artifact and for
CI. It is a usability probe, not the production frontend.

---

## 1. Design goals

| Goal | Meaning in practice |
| --- | --- |
| Browser-only, no install | The whole flow works from one HTML file: import, analyse, diff, export. Nothing leaves the device. |
| Guided process | Steps 1–8 in order; a persistent gate line says what blocks progress; completed steps stay navigable. |
| Semantic, not textual | Every screen shows information-model facts (nodes, namespaces, dependencies, breaking changes) — never raw XML as the primary UI. |
| Explainability first | The tool's core promise is *"why does this node exist?"*. The hop chain is visible, not just counts. |
| Mobile-first | Designed at 360 px width first; desktop is an extension, not the reverse. |
| Review-friendly | A release reviewer on a phone must read the breaking list and approve/waive it in two taps. |
| One contract with CI | Configuration travels as `LinkerConfig` JSON; the authoritative run is the Python engine, so a UI result and a CI gate cannot disagree. |
| No silent model edits | The UI changes **configuration**, not the model. Manual node deletion stays out of scope; retention decisions go through modes and custom rules. |
| Memory-honest | Large companion-spec sets are bounded and releasable; the app never accumulates documents silently (§5). |
| Works offline too | CI artifacts (diff JSON/HTML) stay readable without the app (§9). |

## 2. Architecture

```
Browser (SPA, TypeScript + Vite) — wizard shell, all screens
   │
   ├─ Preview tier (JS worker)      instant feedback while configuring
   │     parse UANodeSet → namespace index → merge → closure → validation → diff
   │     purpose: chip resolution, counts, blocking findings before the export
   │
   └─ Authority tier (Python engine) the artifact and the CI verdict
         nodeset_linker ── config/schema.py      (LinkerConfig ↔ UI form)
                       ── pipeline.py            (analyze / run)
                       ── explain/explainer.py   (why a node is retained)
                       ── diff/                  (SemanticDiff, apply_impact)
                       ── exporter/              (merged file, split bundle + manifest)
         transport: Pyodide/WASM worker in the browser  ·  or `nodeset-linker serve` on localhost
```

Two shapes, same frontend:

* **Browser-only (primary)** — everything on the device. Preview tier answers interactively; the
  final export/diff runs in the Python engine inside a worker (Pyodide/WASM) so byte-determinism and
  the CLI semantics are preserved. No network at all.
* **Local server (`nodeset-linker serve`)** — the frontend talks to a thin FastAPI wrapper over the
  same layers on `localhost`. Chosen when the WASM build is unavailable, for very large bundles, or
  when the user wants the exact interpreter rather than a bundled build.
* **Hosted service (optional later)** — spec offices and reviewers; jobs run in an isolated workspace,
  uploads kept per workspace, never shared. Not required for the everyday flow.

### 2.1 Engine boundary

What each tier may decide. The preview tier must never produce an artifact:

| Concern | Preview (JS) | Authority (Python) |
| --- | --- | --- |
| Parse XML, resolve `ns=`/`nsu=` indexes | yes (for display and chip counts) | yes |
| Root selector resolution | yes | yes |
| Closure / retained set / reduction counts | yes, approximate (subset of rules) | yes, definitive |
| Validation errors and warnings | yes, advisory | yes, blocking gate for export |
| Semantic diff and impact | yes, current mockup rules | yes, `SemanticDiff` + `apply_impact` |
| Exported NodeSet, manifest, SHA-256 | **no** — downloads only after the authority run | yes |
| Release note Markdown/HTML | no | yes (`render.markdown_text`, `render.html_text`) |

Handoff: the wizard serialises its state to a `LinkerConfig` document (`roots`, `mode`,
`external_models`, `custom_rules`, `export_mode`, `validate`, `subset_markers`). The authority tier
re-reads the imported files from the browser's File handles — no re-typing, no server round-trip.
Errors map to the existing exception types (`ParseError`, `ResolutionError`, `ValidationError`,
`DuplicateNodeError`, `ExportError`) and keep the CLI's human phrasing plus a suggested fix.

Optional hosted/serve REST surface (unchanged from the earlier concept, maps 1:1 to existing
objects): `POST /configs`, `POST /runs/{id}/analyze`, `POST /runs/{id}/link`,
`GET /runs/{id}/explain?q=…`, `GET /runs/{id}/graph?namespace=&depth=&limit=`, `POST /diff`,
`GET /diff/{id}/markdown|html`, `POST /validate`, `GET /runs/{id}/artifacts`.

## 3. Information architecture — the wizard

```
1 Workspace  → 2 Import  → 3 Roots  → 4 Policy  → 5 Analyse  → 6 Erklären  → 7 Diff  → 8 Export
                 (optional)                                   (optional)
```

| Step | Completes when | Gate text when blocked |
| --- | --- | --- |
| 1 Workspace | name/release set | "Noch kein Workspace — Schritt 1." |
| 2 Import | ≥ 1 parsed file | "Nodesets importieren (Schritt 2)." |
| 3 Roots | ≥ 1 resolved root | "Mindestens einen Root wählen (Schritt 3)." |
| 4 Policy | closure computed | "Analyse rechnen (Schritt 4 → 5)." |
| 5 Analyse | closure + validation | validation errors: "N Validierungsfehler — Export blockiert." |
| 6 Erklären | closure available | — (read-only step) |
| 7 Diff | a second version imported | breaking: "N breaking changes (Diff)." |
| 8 Export | validation passed | "Export blockiert — Validierung." |

Rules of the flow:

* Steps are reachable by tapping any **completed** step; forward jumps only where prerequisites hold.
* Changing roots, mode or external models recomputes the closure immediately and re-gates.
* Steps 6 and 7 are optional and never block export.
* Every failure carries a diagnosis panel (§3.2), never a bare error toast.
* A **Zurücksetzen** action in step 1 releases every parsed document (§5).

### 3.1 Step 1 — Workspace
Name, release/version label, mode preset. Two entries: *Workspace anlegen* and *Demo-Nodesets laden*
(one-click sample: UA base + Machinery, root `browseName=MSSControllerType`, UA set as external).
Workspace metadata goes into the exported reports so a reviewer can tell which release was reduced.

### 3.2 Step 2 — Import
Drop zone plus a file-picker button (keyboard-equivalent of the drop). Multiple files, companion
specs together. Per file: node count, declared namespaces, source filename. Invalid XML or a
non-`UANodeSet` root is reported per file with the parser's first line, and the step stays usable.
Re-importing the same filename replaces its previous version — it never accumulates (§5).

### 3.3 Step 3 — Roots
Chip input with the selector grammar `browseName=`, `nodeid=`, `namespaceUri=`, `typeDefinition=`.
Each chip resolves against the merged namespace-Uri table and shows *gelöst* or a reason:
*deutlich mehrere Knoten — mit NodeId auflösen*, *keine Übereinstimmung*, or
*ns=-Index ist dokument-lokal — nodeid= mit nsu=… verwenden*. Document-local `ns=` is rejected as
identity, matching the resolver (§13). A root that sits inside an externalized namespace is warned
before analysis (mirrors `_reject_external_roots`).

### 3.4 Step 4 — Policy
* **Mode** — segmented control `STRICT | OPTIONAL_AWARE | RUNTIME | CUSTOM`.
* **Custom rules** (CUSTOM only) — `retain:` / `exclude:` rows over
  `namespace=`, `browseName=`, `nodeclass=`, `modellingrule=`; chips show each rule and its kind.
* **External models** — ModelUri chips; consumers must provide these namespaces, and they are
  excluded from the export and shown hatched in the namespace bars.
* **Export mode** — merged single file vs split per namespace (applied in step 8).

### 3.5 Step 5 — Analyse
* **Stat tiles**: original → retained → removed → reduction %, external nodes as a separate
  hatched tile ("vom Consumer bereitzustellen"), invariant `original = retained + removed + external`
  in the tooltip.
* **Namespace panel**: bar per namespace (declared vs retained), `subset` / `extern` / `voll` badge.
* **Validation panel**: errors first with a blocking badge, warnings collapsible; the blocking state
  disables the export button and names the reason ("hängt im Export", "nicht im Modell").
* **Graph**: namespace-scoped SVG, capped view (§5), NodeClass colour, dashed navigation-only edges,
  plus a keyboard-navigable text alternative.

### 3.6 Step 6 — Erklären
Search by browse name or NodeId. Result card shows the hop chain as a horizontal rail
(`MSSControllerType → JobManagementType → CurrentJob → JobDataType → JobStateEnum`) with origin and
dependency kind; ambiguous names become selectable NodeId chips instead of an error; a removed node
says *entfernt (nicht von einem Root erreichbar)*. Each hop is tappable → explains that hop.

### 3.7 Step 7 — Diff (the release-review flow)
Drop zone for the new version (single file or bundle) plus *Demo-Diff laden*. Identity is
`(namespaceUri, identifier, serverUri)`, so XML order and index renumbering never produce noise.
* **Headline banner**: `N brechende Änderungen` (red) / `kein semantischer Unterschied` (green),
  plus `Hinweis: N` and `Information: N` counts beside it.
* **Three buckets** — **Brechend** (a consumer could fail; blocks the release), **Hinweis** (should
  be reviewed), **Information** (a fact, nothing to fix). Each finding shows its rule chip
  (`BP-NodeId-Immutable` …), the node, old→new and the affected count.
* **Waive** is offered on Hinweis and Information only, with a required reason that becomes
  `waived_by`. A brechende finding is never waivable: the reviewer confirms the release instead
  (`Breaking changes bestätigen`, the UI of `--accept-breaking`), and the confirmation stays visible
  in the report and the JSON.
* **Groups** by namespace, collapsible; added/removed shown as counts with a severity label.
* **Renumbered** as `old id → new id` with a warning badge (identifier moves are breaking —
  `docs/semantic-diff.md` §7.1) — roadmap for both tiers.
* **Metadata block**: version / publication date / `RequiredModel` deltas, marked *Hinweis* or
  *Information* as the engine reports them.
* **Release verdict**: `weiterverwendbar` / `nicht weiterverwendbar — V2/ veröffentlichen`
  against the stored baseline (§11.3); a confirmation never turns a verdict into *weiterverwendbar*.
* **Release note**: Markdown / HTML from the authority tier, JSON preview from the preview tier.

### 3.8 Step 8 — Export
Validation gate shown again, then real downloads via `Blob`:
* merged: `<workspace>-reduced.xml`;
* split: `manifest.json` plus one file per namespace, listed with node counts;
* `<workspace>-reports.json`: workspace metadata, mode, external models, roots, statistics,
  namespace subsets, validation, and retention origins.
The artifact list shows what was written; the authority tier adds SHA-256 so a reviewer can verify
byte identity across runs (export is deterministic).

## 4. Mobile-first layout

**Breakpoints:** `xs < 640`, `sm 640–960`, `md 960–1280`, `lg > 1280`.

* `xs/sm`: single column; a **progress bar** for steps 1–8; bottom nav for completed steps;
  secondary info in collapsible sections; tables degrade to stacked cards; gate line sits above nav.
* `md`: two columns (content + side panel).
* `lg`: three panes — step rail / content / detail (node explanation or diff side-by-side).

Touch rules:

* tap targets ≥ 44 × 44 px, ≥ 8 px spacing between adjacent targets;
* no hover-only affordance: the drop zone always has a visible file-picker button;
* `env(safe-area-inset-*)` respected for nav and the gate line; viewport meta `viewport-fit=cover`;
* long identifiers (`nsu=http://…;i=2401`) wrap with `overflow-wrap:anywhere` and are copyable;
* pull-to-refresh disabled inside the app shell; screen switching by nav/stepper, not swipe;
* content resizes to 200% zoom without clipping.

### 4.1 Wireframes

The rendered screens of the mockup are committed as `docs/UI-Mockup1.png` … `docs/UI-Mockup7.png`,
one per wizard step; the ASCII sketches below are the layout contract they must keep.

Phone (~360 px), step 2 Import:

```
┌──────────────────────────────┐
│ Machinery Demo · v1.0.0  ⋯   │  header
├──────────────────────────────┤
│ 1 ▸ 2 ● 3 ○ 4 ○ 5 ○ 6 ○      │  progress (tap any completed step)
├──────────────────────────────┤
│ ┌──────────────────────────┐ │
│ │  Nodesets hier ablegen   │ │  drop zone (44 px)
│ │  [Dateien auswählen]     │ │  keyboard equivalent
│ └──────────────────────────┘ │
│ machinery.xml   8 Knoten, 1 NS │
│ ua_base.xml     6 Knoten, 0 NS │
├──────────────────────────────┤
│ Diagnose                 ▾   │
│  umati.xml: XML ungültig     │
├──────────────────────────────┤
│ Importiert: 2 Dateien        │  gate line
├──────────────────────────────┤
│ Workspace │ Import │ Roots │  │  bottom nav (completed steps)
│ Analyse   │ Export │       │  │
└──────────────────────────────┘
```

Desktop (≥ 1280 px), step 5 Analyse — three panes:

```
┌ Steps ─┬── Analyse ────────────┬── Detail ─────────┐
│ 1 ✓    │ 71 → 51 → 20 (28.2%) │ JobStateEnum      │
│ 2 ✓    │ [Original][Retained]  │ retained          │
│ 3 ✓    │ [External 4956]       │ MSSControllerType │
│ 4 ✓    │ ── namespaces ──      │  → JobManagement… │
│ 5 ●    │ UA      4956 ▒▒▒ ext  │  → CurrentJob     │
│ 6 ○    │ Machinery 71 ▓▓░ 51   │  → JobDataType    │
│ 7 ○    │ ShowCase  15 ▓░  15   │  → JobStateEnum   │
│ 8 ○    │ ── validation ──      │ (origin, DATATYPE)│
│        │ ✓ passed, 2 warnings  │                   │
└────────┴───────────────────────┴───────────────────┘
```

## 5. Memory and large-bundle rules

Companion-spec sets grow, and browser memory is a real ceiling. These rules are part of the concept,
not implementation details:

* **Replace, never append** — re-importing a file with the same name replaces its previous version;
  `state.files` must not grow per import round.
* **Release file handles** — the file input's value is cleared after reading so the `FileList`/`File`
  objects are not retained; object URLs are revoked right after the download.
* **Zurücksetzen** — the only way to hand large XML back to the browser: clears models, closure,
  diff, roots, rules and rendered panels.
* **Drop parsed DOM early** — the parsed document is kept only while the export needs the original
  elements; after the authority run, keep the semantic nodes and release the DOM (§5 roadmap).
* **Parse off the main thread** — parsing and closure in a worker; a tens-of-MB UA base must not
  freeze the wizard.
* **Bounded views** — graph ≤ 60 visible nodes in the mockup, ≤ 300 in production, namespace-scoped;
  lists paginated, warnings capped.
* **Persist only configuration** — IndexedDB stores workspace configs, roots, modes, rules, baselines
  — never the imported XML; the user re-imports files, keeping memory and privacy bounded.
* **Progressive feedback** — per-file progress and a cancellable import; a bundle of many specs shows
  "3/12 Dateien geparst" rather than a spinner.

## 6. Design system

**Tokens (CSS custom properties, as used in the mockup):**

```css
--bg, --bg-elevated, --fg, --muted, --border
--accent          /* primary action */
--ok              /* validation passed / no changes / retained */
--warn            /* warnings, navigation-only deps */
--bad             /* breaking changes, blocking validation, unresolved chips */
--mono            /* NodeIds, filenames, values */
--radius, --space-1..6
```

**Semantic colour rules** (one vocabulary for reviewers; colour never alone):

| Meaning | Colour | Also encoded as |
| --- | --- | --- |
| breaking / blocking | red | bold label + icon |
| warning | amber | icon |
| ok / identical / retained | green | icon |
| mandatory dependency | filled chip | "Mandatory" text |
| optional dependency | outlined chip | "Optional" text |
| external model | neutral hatched badge | "vom Consumer bereitgestellt" |
| navigation-only | dashed | dashed pattern in graph |
| subset namespace | outlined badge | "subset" text |

**Type:** system-ui stack; headings `clamp()` scaled; body 16 px min on mobile; monospace only for
identifiers and values. **Dark mode:** `prefers-color-scheme`, tokens swapped; contrast ≥ 4.5:1 body,
≥ 3:1 large text and borders. **Motion:** ≤ 200 ms, `prefers-reduced-motion` removes graph animation.

**Components:** wizard stepper + gate line, chip input, segmented control, drop zone with diagnosis,
node card, hop rail, namespace bar row, finding row, diff banner, artifact list, empty/skeleton/error
states, toast + inline form errors.

## 7. Accessibility

* WCAG 2.2 AA: contrast, focus rings, logical tab order, skip-link, landmarks per step.
* Wizard semantics: `aria-current="step"` on the active step, completed steps announced, gate line in
  an `aria-live="polite"` region so blocking states are read out.
* Every result set has a text/list alternative next to any visualization — the dependency chain as
  ordered text, the graph as a keyboard-navigable node list.
* Drop zone needs a keyboard path (file picker button + Enter on the input), never drag-only.
* Form errors linked via `aria-describedby` and repeated in a summary panel; colour-only signals
  always carry text.
* Reports (Markdown/HTML) are plain semantic HTML — usable without CSS.

## 8. Internationalization

German is the mockup's surface; production ships `en`/`de` with English default. OPC UA terms stay
untranslated (BrowseName, NodeClass, ModellingRule). Strings live in a flat dictionary keyed by
finding **rule id**, so a localized UI sentence, a localized release note and the CI message share one
identity. Gate texts are short and imperative, and name the next action.

## 9. Offline and CI-friendly artifacts

Two things must survive without the app:

1. **Static HTML diff report** — self-contained (already how `render.html_text` works), extended with
   anchors, collapsible sections and responsive layout so it reads on a phone opened from CI.
2. **JSON artifact + viewer** — `diff --json` and the reports JSON can be re-opened in the wizard
   (a "Bericht laden" entry in step 1/7) to re-render findings offline after the CI job and after a
   hosted workspace expired. The mockup downloads JSON today but does not reload it yet — roadmap.

## 10. Suggested stack

* SPA: TypeScript + Vite (Svelte or React), plain CSS custom properties — no heavyweight UI kit.
* Preview tier: TypeScript modules in a Web Worker (`parse → merge → closure → validate → diff`).
* Authority tier: Pyodide/WASM build of `nodeset_linker`, or `nodeset-linker serve` over the same
  layers; the pipeline is synchronous, so jobs run in a worker and stream progress via SSE/worker messages.
* Graph rendering: SVG; layout computed where the tier runs; optional WebGL for > 1000 nodes later.
* Downloads: `Blob` + `URL.createObjectURL` with prompt revoke; ZIP via a streaming writer for splits.
* Persistence: IndexedDB for configs, roots, modes, rules and stored baselines (roadmap §7.5 in
  `docs/semantic-diff.md`); never raw XML.
* Auth for hosted mode: per-workspace tokens, uploads isolated per workspace.

## 11. Phasing

| Phase | Scope | Notes |
| --- | --- | --- |
| P0 | Wizard shell + steps 1–4 and 5 with the JS preview tier | The browser mockup already proves the flow and the gate rules. |
| P1 | Explain + Roots resolution parity, diagnosis panel | Highest user value: explains retention. |
| P2 | Authority tier (WASM or `serve`) for validation, diff and export | Preview becomes advisory; artifact and CI verdict come from Python. |
| P3 | Diff screen: rule ids, impact, waive allow-list, renumbering, metadata deltas | Needs the roadmap items from `docs/semantic-diff.md`. |
| P4 | Graph view (namespace-scoped, capped, layout off main thread) | Expensive; after scoping exists. |
| P5 | Persistence (IndexedDB), baselines, JSON report reload, hosted mode | Multi-spec registry workflows and PR comments. |

## 12. Known gaps of the current mockup (deliberate, not design flaws)

* Preview rules are a subset of the Python engine: no renumbering pairing, no version/`RequiredModel`
  metadata diff, no `<Extensions>`/RolePermissions comparison, impact by label not identifier.
* Waives are shown but not persisted. The CLI has the file form (`diff --waiver-file waivers.json`,
  `{rule, namespace?, node?, reason}`) and `--accept-breaking`; the mockup has neither, so the UI
  cannot produce a waiver file a reviewer can commit, and a *Brechend* confirmation is display-only.
* No bundle/manifest import in step 7 — only single files today.
* Graph capped at 60 nodes, no neighbour expansion; no SHA-256 on artifacts.
* German only; no report reload; parsing runs on the main thread.

## 13. Explicit non-goals

* No manual node editing or "delete this node" button — retention decisions belong to modes and
  custom rules, so the reasoning stays explainable.
* No full address-space browser — the UI is about reduction, dependencies and release deltas, not
  runtime server browsing.
* Namespace **indexes are never identity**: `ns=` is a document property; identity is
  `(namespaceUri, identifier, serverUri)`, and a `nodeid=` selector carrying a bare `ns=` index
  is rejected.
* Client-side parsing is allowed **only** in the preview tier. The exported NodeSet and the CI
  verdict always come from the Python engine — this replaces the earlier blanket "no client-side
  parsing" rule.
* No forced upload to a server: browser-only mode keeps files on the device; hosted mode is opt-in.

## 14. Related documents

* [Browser-driven mockup](ui-mockup-browser.html) — the operable reference for this concept
* Mockup screens `UI-Mockup1.png` … `UI-Mockup7.png` — one per wizard step (§4.1)
* [Semantic Diff — extended documentation & roadmap](semantic-diff.md)
* [OPC 11030 v1.03.03](OPC-11030-v1.03.03.md) — the whitepaper the severities derive from; a finding
  cites its clause, so the diff screen can gate BREAKING and keep ADVISORY/INFO non-gating
* [Architecture](architecture.md) — layer boundaries the UI must respect
* [Usage Guide](usage-guide.md) — the CLI workflows the UI mirrors
* [README](../README.md) — CLI commands and report formats
