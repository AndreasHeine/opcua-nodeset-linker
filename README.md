# OPC UA NodeSet Linker

A **semantic dependency linker and tree shaker for OPC UA Information Models**.

It loads one or more `UANodeSet` XML files, builds the *semantic* graph of the
information model, computes the dependency closure of user-selected root nodes, drops
everything unreachable, and writes a reduced NodeSet that stays semantically valid for its
declared dependencies — while explaining **why every retained node exists**. Models listed
under `external:` are intentionally not bundled; the importing application must provide them.

It is deliberately **not** an XML minifier: XML is treated as a serialization format
only. All decisions are made on the semantic model.
