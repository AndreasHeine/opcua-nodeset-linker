# Reduction Example

The two fixtures in `tests/data` contribute 71 unique nodes: 53 in `ua_base.xml` and
18 in `machinery.xml`. Both are reduced; neither is automatically kept intact.

```yaml
nodesets:
  - tests/data/ua_base.xml
  - tests/data/machinery.xml
mode: STRICT
roots:
  - browseName=MSSControllerType
  - nodeid=nsu=http://opcfoundation.org/UA/Machinery/;i=2200
output: out/reduced.xml
```

The precise default traversal retains 51 nodes in STRICT (28.2% reduction) and
49 in RUNTIME (31.0%), with no external models configured.

The selected type pulls its bases, declarations and variable datatypes. The second root
pulls its interface, method, arguments and their dependencies. JobDataType retains its
fields' datatypes and both declared encodings. Shared datatype bases do not pull their
unrelated subtypes or users.

Parents and ancestor folders are retained as navigation-only context where needed; their
unrelated children are omitted. Namespace metadata is retained explicitly and reduced
namespaces are marked through their `IsNamespaceSubset` properties.

STRICT preserves Diagnostics and MachineDiagnostics as optional declarations. RUNTIME may
omit these branches and their links, without leaving dangling references.
UnrelatedType and UnrelatedMachineType are not dependencies of these roots.

Run `nodeset-linker analyze` for current counts and explanations, then `link` to produce
the actual output. Validate the written XML, not just the in-memory retained set:

```powershell
nodeset-linker link examples/config.yaml
nodeset-linker validate out/reduced.xml --schema tests/data/schema/UANodeSet.xsd
nodeset-linker stats out/reduced.xml
```

`original_nodes = retained_nodes + removed_nodes + external_nodes`.
The percentage is removed nodes divided by non-external input nodes. Externalizing the
UA namespace omits its declarations but does not count those nodes as reduction.

See [dependency-resolution.md](dependency-resolution.md) for traversal rules and limitations,
and [../tests/test_precise_reduction.py](../tests/test_precise_reduction.py) for regressions
that distinguish required dependencies from merely connected nodes.
