# Semantic diff

- Old: `examples\demo4\original.xml`
- New: `examples\demo4\modified.xml`

## Summary
- Nodes: 1110 → 1110
- Added: 0   Removed: 0   Modified: 2
- Breaking changes: 2
- Total Impact: 165 nodes

## Modified nodes
- **Location [Variable, ns=Machinery, i=6029]**
  - Modelling rule: Optional → Mandatory
  - Impact: 165 nodes
- **ProductInstanceUri [Variable, ns=Machinery, i=6015]**
  - DataType: String (i=12) → Double (i=11)
  - Impact: 165 nodes

## Impact
- Total Impact: 165 nodes

## Breaking changes
- **Location [i=6029]: Modelling rule: Optional → Mandatory**
  - Affects 165 nodes
- **ProductInstanceUri [i=6015]: DataType: String (i=12) → Double (i=11)**
  - Affects 165 nodes
