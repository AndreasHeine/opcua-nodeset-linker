# Exposé: `opcua-nodeset-linker`

**Ein Kompaktwerkzeug für OPC-UA-Modellierung: reduziert Companion-Specs, validiert NodeSets ohne
laufenden Server und schreibt aus dem semantischen Diff den Changelog.**

Stand: `0.2.0-alpha`, Working Tree auf `main` (669 Tests grün, 94 % Coverage, `mypy` und `ruff` clean).

---

## 1. Die These

OPC UA wird als **Modell** definiert, aber als **Datei** veröffentlicht. Zwischen Modell und Datei
liegen drei Schritte, die heute manuell passieren und fehlerhaft bleiben:

1. *Reduktion* — welche Nodes einer Companion-Spec braucht dieses Produkt wirklich?
2. *Konformität* — ist die veröffentlichte Datei semantisch geschlossen, bevor ein Server sie lädt?
3. *Release-Information* — was hat sich gegenüber der veröffentlichten Version geändert, und darf die
   `NamespaceUri` wiederverwendet werden?

Das Tool macht alle drei Schritte deterministisch und maschinenlesbar. Es braucht dafür ** keinen
laufenden Server, keine Client-Bibliothek und keinen OPC-UA-Stack** — nur die NodeSet-Dateien. Damit
werden Modellfehler zu CI-Ergebnissen statt zu Feldproblemen.

---

## 2. Was heute schon funktioniert (Ist-Zustand)

| Bereich | Umsetzung | Größenordnung |
| --- | --- | --- |
| Parser | OPC 10000-6 NodeSets inkl. `NamespaceUris`/`ServerUris`/`Models`, Index → URI, tolerant gegen Re-Indexierung und Dokumentreihenfolge | — |
| Dependency-Graph | Identität ist `(namespaceUri, identifier, serverUri)`; Referenzen, Typdefinitionen, Encodings, Subtypen, `RequiredModel` | 3912 Kanten im Beispiel-Export |
| Reduktion | `STRICT` / `OPTIONAL_AWARE` / `RUNTIME` / `CUSTOM`, Roots, externe Modelle (`external`), Subset-Marker (§11.2) | 1110 Nodes, 8 Namespaces im Beispiel |
| Validierung | semantische Prüfung ohne Server, optional XSD, optional OPC-11030-Lint | 10 semantische Prüfbereiche, 25 Lint-Regeln |
| Semantischer Diff | 113 Regeln mit Severity, Regel-ID und Klausenzitat | Breaking / Advisory / INFO |
| Release-Verdikt | „darf diese `NamespaceUri` wiederverwendet werden?“ gegen publizierten Baseline (§3.3, §11.3) | Exit-Code-gate |
| Ausgabe | Console, Markdown, HTML, JSON (`schema_version`, `paper`) | — |
| Versionen | Vergleich auf dem SemVer-Kern statt der Schreibweise (Annex F.2, §12.41) | neue Regel `BP-Namespace-ModelVersionSyntax` |
| CI | `--fail-on-breaking`, `--fail-on-advisory`, Waiver, `--accept-breaking` | Exit 0/1 |

Beispielzahlen aus den mitgelieferten Demos:

- Reduktion von sieben Companion-Specs auf einen Maschinenbauer-Export: **1110 Nodes, 8 Namespaces,
  3912 Abhängigkeitskanten**, semantische Validierung „passed with warnings“ (8 Hinweise, alle
  externalisierte OPC-UA-Core-Abhängigkeiten).
- Demo3-Diff (zwei Showcase-Instanzen): `Nodes: 1110 → 1110`, 1 Modified, 1 Renumbered,
  **2 Breaking**, Impact **78** Nodes.
- Demo4-Diff (geänderte Machinery/MachineTool-Release): `1110 → 1109`, 1 Removed, 5 Modified,
  **6 Breaking**, Impact **494** Nodes.

---

## 3. Säule A — NodeSet-Validierung ohne realen Server

### Warum ohne Server validiert werden *kann*

Ein NodeSet ist ein geschlossener Graph. Alles, was ein Server zur Laufzeit „prüft“, ist in der Datei
selbst enthalten oder fehlt in ihr: Referenzziele, Typdefinitionen, Encodings, Subtypketten,
Namespace-Links und `RequiredModel`-Erklärungen. Diese Struktur lässt sich vollständig gegen die
Modellregeln rechnen, bevor ein Stack existiert. Ein Server prüft dagegen nur, was beim
Instanziieren auffällt — und zeigt genau dann einen Fehler, wenn ein Endanwender ihn sieht.

### Was die semantische Validierung prüft

`nodeset-linker validate <output>` kontrolliert:

- Referenzziele (existiert das Ziel, ist es ein Referenztyp?) und Referenztyp-Deklarationen
- Typdefinitionen und Datatype-Referenzen (`HasTypeDefinition`, Encoding-Referenzen, `<Definition>`
  gegen Datatype)
- Referenztyp- und Subtyp-Hierarchie inkl. Zyklen (Selbst-/Transitivzyklen einer Subtypkette)
- Namespace-Links (`NamespaceUris`, Alias, QualifiedName-Index) und `IsNamespaceSubset`-Marker (§11.2)
- Externe Abhängigkeiten: Referenzen in ein als `RequiredModel` erklärtes Modell sind erlaubt,
  Referenzen in ein Modell, das die Datei nicht enthält, sind Fehler

Schema-Konformität wird **nicht** behauptet, wenn kein Schema gegeben ist: `--schema PATH` prüft
gegen `UANodeSet.xsd`; ohne diesen Schalter ist das Ergebnis *semantisch*, nicht „schema-konform“.

### Was der OPC-11030-Lint zusätzlich findet

`--best-practices` liefert zusätzlich 25 Regeln, die **ohne** Server und **ohne** Stack sichtbar werden — etwa:

- `MANDATORY_SELF_CONTAINED_DECLARATION` → `BP-Recursive-Mandatory`: ein Pflichtfeld in einem Typ, der
  sich selbst enthält — ein Server kann diesen Typ nicht instanziieren. Im Demo-Export finden sich so
  **609** Stellen; kein Server und kein Browser zeigt diese Klasse als Fehlermeldung.
- Namens- und Datei-Konventionen (§3.2.x), Duplicate-Namen über Namespaces, Browse-Path-Kollisionen,
  `NamespaceUri`-Template und fehlender Trailing Slash, `NamespaceIndex` in Text, Status-Code außerhalb
  der Base, Feldgrenzen von optionalen Strukturmitgliedern (§3.2.2), Audit-Event-Subtypen.

Die Lint-Findings ändern den Exit-Code bewusst nicht: sie sagen „schau dir das an“, nicht „das ist
kaputt“. Ein Reviewer sieht sie, die Pipeline gate't nicht.

### Praktischer Wert

| Vorher | Nachher |
| --- | --- |
| Modellfehler fallen beim ersten Instanziieren auf einem echten Server auf | fallen in der CI auf, bevor die Datei veröffentlicht wird |
| Konformität wird mit Server, Browser und manuellem Stöbern geprüft | `validate` + `--schema` + `--best-practices`, eine Zeile, Exit-Code |
| Companion-Spec-Reduktion ist Handarbeit mit unsicherem Ergebnis | `analyze` zeigt die Reduktion ohne Schreibvorgang, `explain` begründet jede Node |
| Externalisierte Abhängigkeiten sind ein Versprechen an den Consumer | werden als Warnings benannt, die der Consumer abgleichen kann |

---

## 4. Säule B — KI-gestützte Changelog-Erstellung durch den semantischen Diff

### Das Prinzip: deterministische Fakten, sprachliche Prosa

Der Diff vergleicht **semantische Modelle**, nie XML. Dokumentreihenfolge, Whitespace, Kommentare und
ein neu zugewiesener `NamespaceIndex` sind keine Unterschiede; ein Node wird über
`(namespaceUri, identifier, serverUri)` identifiziert. Ein umnummerierter Node erscheint als
*renumbered*, nicht als Add plus Remove. Jede Änderung trägt:

- Severity (`BREAKING` / `ADVISORY` / `INFO`)
- Regel-ID (`BP-DataType-Changed`, `BP-NodeId-Immutable`, `BP-FieldOrder-Encoding`, …)
- Klausenzitat aus OPC 11030 (§3.2.11, §11.3, …) und eine begründete Rationale
- Node-Identität und Impact (exakte Anzahl abhängiger Nodes)
- Namespace-Metadaten: `Version`, `PublicationDate`, `XmlSchemaUri`, `ModelVersion`, `RequiredModel` —
  verglichen auf dem SemVer-Kern, nicht auf der Schreibweise

Das ist die Voraussetzung für KI-gestützte Changelogs: ein Sprachmodell bekommt keine Textdiff-Zeilen,
sondern eine **strukturierte, bereits klassifizierte Faktenliste**. Es formuliert daraus Prosa — und
jede Aussage ist über Regel-ID, Klausel und Node-ID nachprüfbar. Halluzinationen werden dadurch sichtbar,
nicht glaubbar.

> Wichtig, ehrlich abgegrenzt: Das Tool selbst ruft **kein** Sprachmodell auf. Es liefert die
> deterministische Grundlage (`--json`, `--markdown`), und das Modell schreibt darüber. Der Wert liegt
> in der Kombination: Maschine klassifiziert, Modell formuliert.

### Workflow, wie er in einer Release-Pipeline läuft

```bash
# 1. Fakten erzeugen (deterministisch, maschinenlesbar)
nodeset-linker diff release/v1.xml release/v2.xml --impact \
  --json     --output out/diff.json      # Input für das Sprachmodell
nodeset-linker diff release/v1.xml release/v2.xml --impact \
  --markdown --output out/diff.md        # PR-Kommentar / Review

# 2. Gate (nur Breaking blockiert; Advisory bleibt Lesepflicht)
nodeset-linker diff release/v1.xml release/v2.xml --fail-on-breaking --release-verdict --baseline registry.json

# 3. Changelog aus den Fakten
#    Prompt: "Schreibe aus diff.json den Abschnitt 'Breaking changes' für Release 1.05.03.
#    Nenne pro Punkt Node-ID, Regel-ID und Klausel aus dem JSON. Keine Punkte erfinden."
```

Der JSON-Report enthält `schema_version`, die Quellenangabe des Papiers, Summary (`added/removed/modified/renumbered`,
`breaking_changes`, `advisory_changes`, `namespace_changes`, `identical`, `waived_changes`) und pro Node
die Regel- und Impact-Daten. Damit lassen sich Changelog-Abschnitte automatisch erzeugen und gegen die
Report-Zahlen abgleichen: „6 breaking changes“ ist keine Behauptung im Text, sondern eine Zahl aus dem
Report.

### Was der Diff zusätzlich beantwortet, das kein Textdiff beantwortet

| Frage | Antwort des Tools |
| --- | --- |
| Darf diese `NamespaceUri` wiederverwendet werden? | `--release-verdict --baseline …` (§3.3, §11.3); ohne Baseline sagt es „baseline unknown“ statt zu raten; „not reusable“ exit 1 |
| Welche Consumer hängen an dieser Änderung? | `--impact`, `--context <consumer-model>` zählt auch Abhängigkeiten eines Nebenmodells |
| Ist die Datei ein Linker-Export, also externalisiert? | `--reduced-export` liest fehlende Deklarationen als `BP-Declaration-Externalized` (INFO), nicht als Removal (§3.2.16) |
| Vergleicht ein Split-Export mit dem alten Merged-File? | Ja: eine Seite darf ein Bundle mit `manifest.json` sein |
| Welche Review-Entscheidung wurde akzeptiert? | `--waive`/`--waiver-file` (Advisory/INFO) und `--accept-breaking` bleiben im Report mit `[waived]`/Acknowledgement stehen; Breaking ist nicht waivable |
| Sind `Version` und `ModelVersion` ein Widerspruch? | Nur wenn sie *verschiedene Releases* benennen (`BP-Namespace-VersionDrift`, Advisory); `1.04.0` neben `1.4.0` ist ein Release und meldet nichts; ungültiges `ModelVersion` ist `BP-Namespace-ModelVersionSyntax` |
| Wurde ein Namespace veröffentlicht und enthält Breaking-Änderungen? | `BP-BreakingInReleasedNamespace` |
| Wurde deprecated, ohne einen neuen Namespace zu bringen? | `BP-Deprecation-Without-New-Namespace` (§14.2/§14.3) |
| Hat das Modell Conformance Units / Profiles? | `BP-CU-*`, `BP-Profile-Removed`, `BP-Namespace-Without-CU` (§12.1/§12.2, gefunden über BrowseName, nicht über hartcodierte IDs) |

Für die Standardisierungsseite ist die letzte Zeile entscheidend: CU-/Profile-Deklarationen werden vor
der Veröffentlichung geprüft, ohne dass ein Stack oder ein Conformance-Tester läuft.

---

## 5. Einsatzmöglichkeiten auf der Standardisierungsseite

Typische Nutzer: Autoren und Reviewer von Companion-Specs, Konsortien (Machinery/UTIMAT, Diagnostics,
PackML, ISA95-Job-Control), Arbeitsgruppen, die vor der Publikation ein Modell prüfen.

1. **Spec-Review vor Publikation.** `validate` + `--schema` + `--best-practices` ist ein
   Konformitätscheck ohne Implementierung: der Reviewer prüft das Modell, nicht einen Server eines
   Mitglieds.
2. **Änderungsprotokoll über Draft-Releases.** Zwei Draft-Stände vergleichen ergibt die vollständige
   Änderungsliste mit Klauseln: eingefügte Hierarchie, geänderte Feldreihenfolge gegen Encoding,
   Symmetrie eines Referenztyps, `MaxStringLength`, CU-Mitglieder. Ein Draft, der unbemerkt breaking ist,
   wird vor der Publikation sichtbar.
3. **Release-Policy als Test.** §11.3 („Breaking braucht einen neuen Namespace“) wird durch
   `--release-verdict` + Baseline-Registry zur überprüfbaren Aussage statt zur Absichtserklärung.
4. **Deprecation-Spur (§14.2/§14.3).** Deprecated Nodes ohne neuen Namespace werden advisiert; die Zahl
   deprecated Nodes pro Namespace erscheint im Namespace-Eintrag — genau das, was eine
   Rückzugsplanung braucht.
5. **Versionsschreibweise statt Streit.** OPC schreibt ein Release zweimal (`Version="1.05.03"`,
   `ModelVersion="1.5.3"`). Der Kernvergleich macht aus dem scheinbaren Widerspruch nichts; ein
   tatsächlich differentes Release oder ein ungültiges `ModelVersion` bleibt als Advisory stehen.
6. **Konferenz-/Gremienarbeit.** Markdown- und HTML-Reports sind versendbare Review-Unterlagen; die
   Regel-IDs geben dem Protokoll eine referenzierbare Sprache („BP-Subtype-Inserted, §3.2.4“).
7. **KI-gestützte Spec-Dokumentation.** Aus dem JSON: Changelog-Abschnitte, Änderungen für die
   Spec-Textstelle, eine „Was sich gegenüber Release X ändert“-Seite — jeder Punkt mit Regel-ID und
   Klausel, also zitierbar.

---

## 6. Einsatzmöglichkeiten auf der Maschinenbauerseite

Typische Nutzer: Maschinen- und Anlagenbauer, Integratoren, Endanwender mit eigener Adressraum-Planung.

1. **Datenhaltung verkleinern statt alles laden.** `link` mit Roots liefert den Teil einer
   Companion-Spec, den diese Maschine wirklich braucht; `--export.mode split` schreibt pro Namespace eine
   Datei (`manifest.json`), was Datei-basierte Werkzeugketten und Patch-Workflows ermöglicht.
2. **Warum ist diese Node drin?** `explain -n <BrowseName|NodeId>` beantwortet die Retention mit dem
   Dependency-Pfad — die Frage, die jeder Integrator stellt und die heute nur durch Ausprobieren
   beantwortet wird.
3. **DI/Machinery externalisieren.** `external:` erklärt OPC UA Core oder DI als Abhängigkeit: Referenzen
   und `RequiredModel` bleiben in der Datei, Deklarationen werden weggelassen (§11.2). Der Consumer muss
   das Modell bereitstellen — und `validate` benennt diese Pflicht als Warning.
4. **Eigenes Modell, fremde Consumer.** Änderungen am Maschinenmodell zeigen den Impact auf 500 Nodes,
   nicht die Vermutung davon (`--impact`, `--impact-limit`, `--context`). Ein DataType-Wechsel an einem
   Pflichtfeld wird als Breaking erkannt, bevor ein Feldgerät damit ausgeliefert wird.
5. **Vendor-Release-Gate.** CI prüft jeden Export gegen den zuletzt ausgelieferten Stand: Breaking →
   Exit 1. Bekannte, bewusst akzeptierte Advisory-Findings werden per Waiver-Datei dokumentiert statt
   weggeschaltet, damit der Report ehrlich bleibt.
6. **Changelog für Kunde und Service.** Aus dem Markdown/JSON-Report entsteht ein Changelog, der sagt, was
   sich für den Consumer ändert: umbenannte Mitglieder, geänderte Argumentlisten, neue Pflichtfelder,
   entfallene Enum-Werte. Der Servicetechniker sieht die betroffene Node, nicht eine Dateiänderung.
7. **Migration zwischen Werkzeugketten.** Export alter vs neuer Toolchain-Version verglichen: eine
   reine Re-Indexierung oder Umbenennung der Schreibweise ist „No semantic differences found“; ein
   echter Nodeschwund ist es nicht.
8. **Prüfung vor Auslieferung an den Endanwender.** Ohne Stack prüft `validate` den Adressraum, den der
   Kunde lädt — inklusive der rekursiven Pflichtfelder, die ein Server beim Instanziieren ablehnt.

---

## 7. Potenzial — warum das über ein Diff-Tool hinausgeht

- **Verschiebt die Fehlerklasse nach links.** Instanziierungsfehler (rekursive Pflichtfelder, tote
  Referenzziele, Encoding-Lücken, Subtypzyklen) tauchen in CI auf, nicht beim Kunden. Das ist der
  Unterschied zwischen „Modell released“ und „Modell funktioniert“.
- **Macht Modellierung versionierbar.** Modelle haben heute keine Release-Semantik. Severity, Regel-ID,
  Klausel und Namespace-Verdikt geben einer Companion-Spec die Struktur, die Code mit semver und CHANGELOG
  hat.
- **Macht KI-Ausführungen kontrollierbar.** Ein Sprachmodell, das auf XML-Diffs arbeitet, erfindet
  Änderungen. Ein Sprachmodell auf diesem JSON kann jede Aussage auf Node-ID, Regel-ID und Klausel
  zurückführen — und wird gegen die Summary-Zahlen geprüft. Das ist der Weg zu automatisierten, aber
  prüfbaren Changelogs und Release Notes.
- **Standardisierung ohne Implementierung.** Konformität, CU-/Profile-Prüfung und Deprecation-Spur sind
  ohne Server und ohne Conformance-Tester prüfbar; Gremien können Modelle prüfen, bevor ein Stack existiert.
- **Reduktion als Produktentscheidung.** Root-Liste + Retention-Policy statt manuell gepflegter
  Node-Listen; die Begründung ist mit dem Graph verknüpft (`explain`), nicht mit einem Kommentar.
- **Geringe Betriebskosten.** Kein Server, kein Stack, nur Python und NodeSet-Dateien; ein CLI, das in
  eine bestehende Pipeline passt; Beispiele liegen im Repo und sind regenerierbar.

---

## 8. Bewusste Grenzen des Ist-Zustands

- Das Tool startet **keinen** Server und instanziiert keinen Adressraum. Die Gegenprüfung gegen einen
  echten Stack bleibt ergänzend möglich (optional extra `asyncua`, `examples/demo1/server.py`).
- Schema-Konformität wird nur behauptet, wenn `--schema` gegeben war.
- Lint-Findings und INFO blockieren den Exit-Code nicht; eine Release-Entscheidung muss sichtbar bleiben,
  nicht verschwinden.
- Ein Namespace ohne `ModelVersion` wird nicht bewertet (Annex F.2 verlangt ihn ab Spec 1.05.03) — offene
  Lücke, siehe `docs/semantic-diff.md` §5.
- `validate` und `stats` nehmen eine Datei; ein Split-Bundle-Verzeichnis akzeptiert bisher nur `diff`.
- Ein Teil der Diff-Gaps ist offen (u. a. Facets/Feature-Sets, Value-vs-Datatype-Konsistenz,
  Identifier-Struktur in `reports/data.py`, unterscheidbare Exit-Codes).

---

## 9. Ausbauschritte, die das Potenzial heben

1. **Split-Bundle für `validate`/`stats`** — ein Namespace-Export ist eine Release, nicht eine Datei.
2. **Live-Server-Vergleich** über das `asyncua`-Extra: Adressraum eines Servers gegen den NodeSet-Export;
   damit wird „validiert ohne Server“ durch „validiert gegen den Server“ ergänzt.
3. **KI-Integration als eigener Schritt:** Prompt-Contract auf `diff.json` (Regel-ID, Klausel, Node-ID,
   Impact) plus Rückprüfung der generierten Changelog-Punkte gegen den Report; Ziel: Changelog als
   CI-Artefakt, nicht als Handarbeit.
4. **Waiver-/Registry-Format für Gremien:** Baseline-Registry publizierter `NamespaceUris` als Shared
   Artefakt, damit `--release-verdict` in einer Spec-Pipeline läuft.
5. **Modellversion als Pflicht** (`ModelVersion` fehlt → Finding), Facets/Feature-Sets,
   Value-vs-Datatype-Konsistenz.
6. **Unterscheidbare Exit-Codes** für „breaking“, „advisory“, „baseline unknown“, damit eine Pipeline die
   Gründe unterscheiden kann.

---

## 10. Fazit

Das Tool nimmt drei Aufgaben, die in der OPC-UA-Modellierung heute manuell und reaktiv laufen, und macht
sie deterministisch: Reduktion mit Begründung, Validierung ohne Server, Release-Vergleich mit
Klauselreferenz. Der entscheidende Hebel für die KI ist, dass der semantische Diff keine Textzeilen
liefert, sondern klassifizierte Modellfakten — dadurch wird ein Changelog automatisch geschrieben und
 trotzdem nachprüfbar. Für die Standardisierungsseite entsteht damit ein Konformitätscheck vor der
Publikation, für die Maschinenbauerseite ein Release-Gate vor der Auslieferung.
