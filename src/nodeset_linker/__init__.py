"""OPC UA NodeSet Linker — semantic dependency linker and tree shaker.

The package is organised as a clean-architecture stack:

* ``parser`` / ``repository``  — XML is only a serialization format; they turn it
  into the semantic domain model.
* ``graph``                    — semantic graph engine, independent of XML.
* ``resolver``                 — dependency closure over the semantic graph.
* ``retention`` / ``policies`` — decisions about which nodes survive.
* ``validator``                — semantic correctness gate before export.
* ``exporter``                 — writes merged or namespace-split NodeSets and assigns
  NamespaceIndexes (the only place indexes exist).
* ``reports`` / ``explain``    — traceability of *why* every node is retained.
* ``cli`` / ``config``         — human-facing entry points.

Dependencies flow inward only: outer layers may import inner layers, never the
reverse.  ``models`` and ``utils`` depend on nothing inside the package.
"""

from __future__ import annotations

from nodeset_linker.models.identifiers import ExpandedNodeId, NodeId
from nodeset_linker.models.node import UANode

__all__ = ["ExpandedNodeId", "NodeId", "UANode", "__version__"]

__version__ = "0.1.1-alpha"
