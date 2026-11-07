# Dependency Resolution

Reduction follows required dependencies, not every node connected to a root.
The precise algorithm is the default for all modes; earlier versions traversed almost
all references in both directions and consequently retained unrelated datatype users,
instances of shared types, and subtypes.

## Roots

Selectors accept NodeIds (prefer `nsu=<namespace URI>`), browse-name globs, namespace URIs,
and `typeDefinition=`. A namespace selector selects all its declarations; a type-definition
selector selects the type and its instances. Unmatched selectors fail.
Document-local `ns=` indexes are ambiguous across inputs: use URI identities.

CUSTOM retain rules seed additional closures, including the added nodes' dependencies and
explanations. They do not change the list of explicitly selected roots.

## Directional closure

Traversal is breadth-first and cycle-safe. Reference direction is respected even when only
one end declares a link:

| Relationship | Required traversal | Not pulled |
| --- | --- | --- |
| `HasSubtype` | Subtype to base, for all type classes. An inverse declaration names the base; a forward declaration at the base identifies its subtype's dependency. | Other subtypes of a base. |
| `DataType`, structure fields, union alternatives | Declaring variable/type to its datatype. | Other variables or structures using that datatype. |
| `HasTypeDefinition`, `HasInterface`, `GeneratesEvent`, `HasDescription` | Declaring node to its forward target. | Other users of the type/interface/event. |
| `HasComponent`, `HasProperty`, `HasOrderedComponent`, argument descriptions | Parent to children, including children discovered from inverse-only declarations. | Siblings through an ancestor retained only for navigation. |
| `Organizes` | Selected folders/views to their members. Ancestors are navigation context only. | Other members of context-only folders. |
| `HasEncoding` | Datatype to encodings; an encoding also requires its datatype. | Other users of that datatype. |
| `MethodDeclarationId` | Method to declaration. | Other instances of the method. |
| Value encoding TypeIds and argument DataTypes | Owning value to the resolved encoding/datatype. | Other users of that encoding/datatype. |
| Unknown custom reference | Conservatively retain its declared target. | Nodes merely declaring an incoming custom link. |

Loaded custom reference types derived from standard ownership/reference types inherit
their traversal semantics. Used non-UA reference type declarations and their bases are retained.
Standard UA reference types are supplied by the importing application's base model.

## Ancestor context

Parents and ancestor folders remain available for import and browse navigation, including
parents stated only by `ParentNodeId`. They are recorded as `navigation_only` in the closure.
Their unrelated children are not expanded. If another dependency or root requires that node's
full contents, it is upgraded and traversed again.

Namespace metadata for contributing non-external namespaces is retained explicitly, so
`IsNamespaceSubset` can be written without traversing every object under `Namespaces`.

## Modes and boundary pruning

STRICT and OPTIONAL_AWARE preserve optional declarations belonging to required types/subtrees.
RUNTIME skips Optional/OptionalPlaceholder ownership branches, including their otherwise
unneeded descendants. Explicit roots and custom-retained nodes survive even if optional.
Mandatory declarations, required types, fields, encodings and signatures cannot be dropped.

Before validation/export, the pipeline prepares copies of source nodes:

* navigation links to omitted children of context-only ancestors are removed;
* references to legally omitted optional children are removed;
* inverse navigation and forward subtype links to omitted nodes are removed;
* missing required targets remain validation errors, including unresolved source references
  and custom exclusions that break a non-navigation dependency.

Policy-dropped nodes still needed by a non-prunable reference are repaired. Explicit custom
exclusions are never repaired. The source model is not mutated.

## External dependencies

An external namespace is a traversal boundary: its declarations are not shipped and its
internals do not pull unrelated nodes into the output. References into it remain intact.
RequiredModel entries are preserved with known metadata when their namespace remains used;
requirements for completely unused namespaces are omitted rather than falsely externalized.
The importing application must provide external models.

A dependency is configured by its stable NamespaceUri — never by a document-local namespace
index, which means nothing once the exporter re-numbers namespaces. The URI is stored in the
spelling OPC 10000-5 uses, with the trailing `/`, so `http://opcfoundation.org/UA/DI` and
`http://opcfoundation.org/UA/DI/` are the same dependency and both match what a document
declares; a URI nothing declares stays as written, because a dependency can be externalized
without being loaded.

## Evidence and limitations

Tests cover directional inheritance, shared-type/datatype isolation, inverse-only ownership,
context-only ancestors, optional subtrees, custom retention/exclusion, method argument
datatype remapping, schema validity and reload validation. The demo also supports asyncua
import checks with its preloaded UA namespace externalized.

This is not a proof of a globally minimum model. Unknown references remain conservative,
opaque extensions are not interpreted, and RUNTIME does not observe a live server to infer
which optional declarations an application actually uses.
