# Semantic diff

- Old: `examples\demo3\original.xml`
- New: `examples\demo3\modified.xml`

## Summary
- Nodes: 1110 → 1110
- Added: 0   Removed: 0   Modified: 1
- Renumbered: 1
- Breaking changes: 2
- Total Impact: 78 nodes

## Modified nodes
- **ProductInstanceUri [Variable, ns=ShowCaseMachineTool, i=6002]**
  - DataType: String (i=12) → Double (i=11)
  - Impact: 52 nodes

## Renumbered nodes
- **ControlIdentifier1 [Variable, ns=ShowCaseMachineTool]: i=6022 → i=6302**
  - Impact: 52 nodes

## Namespace changes
- **ShowCaseMachineTool (http://umati.org/UA/ShowCaseMachineTool/)**
  - Nodes: 86 → 86
  - References: 260 → 260
  - DataTypes: 0 → 0
  - Version: 1.03.0 → 1.04.0
  - PublicationDate: 2026-01-30T12:22:50Z → 2026-02-15T09:00:00Z
  - RequiredModel: http://opcfoundation.org/UA/, http://opcfoundation.org/UA/DI/, http://opcfoundation.org/UA/IA/, http://opcfoundation.org/UA/ISA95-JOBCONTROL_V2/, http://opcfoundation.org/UA/MachineTool/, http://opcfoundation.org/UA/Machinery/, http://opcfoundation.org/UA/Machinery/Jobs/ → http://opcfoundation.org/UA/, http://opcfoundation.org/UA/DI/, http://opcfoundation.org/UA/IA/, http://opcfoundation.org/UA/MachineTool/, http://opcfoundation.org/UA/Machinery/
  - RequiredModel version: - 2.0.0 (http://opcfoundation.org/UA/ISA95-JOBCONTROL_V2/)
  - RequiredModel version: - 1.0.1 (http://opcfoundation.org/UA/Machinery/Jobs/)

## Impact
- Total Impact: 78 nodes

## Breaking changes
- **ProductInstanceUri [i=6002]: DataType: String (i=12) → Double (i=11)**
  - BP-DataType-Immutable: The DataType of a published Variable or InstanceDeclaration shall not change (§3.2.12): readers decode the value with the old type.
  - Affects 77 nodes
- **ControlIdentifier1 [i=6302]: identifier moved: i=6022 → i=6302**
  - BP-NodeId-Immutable: The NodeId of a published node changed (§3.2.17): identifiers shall not change, and a consumer that resolves the node by identifier now reaches nothing or the wrong node. Add new identifiers instead of renumbering, and publish a new namespace for a breaking change (§11.3).
  - Affects 77 nodes
