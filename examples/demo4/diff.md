# Semantic diff

- Old: `examples\demo4\original.xml`
- New: `examples\demo4\modified.xml`

## Summary
- Nodes: 1110 → 1109 (-1)
- Added: 0   Removed: 1   Modified: 5
- Breaking changes: 6
- Total Impact: 494 nodes

## Removed nodes
- **ManufacturerUri [Variable, ns=Machinery, i=6007]**
  - Impact: <Component>
  - Impact: <MonitoredElement>
  - Impact: <OrderedObject>
  - Impact: <Part>
  - Impact: <PartSet>
  - Impact: <SoftwareItem>
  - Impact: Aborted
  - Impact: AbortedToInitializing
  - Impact: ActiveProgram
  - Impact: AlertType
  - Impact: AssetId
  - Impact: Channel1
  - Impact: ChannelMode
  - Impact: ChannelState
  - Impact: ComponentName
  - Impact: Components
  - Impact: ContainsMixedParts
  - Impact: ControlIdentifier1
  - Impact: ControlIdentifierInterpretation
  - Impact: CreateDirectory
  - Impact: CreateFile
  - Impact: CurrentState
  - Impact: CustomerOrderIdentifier
  - Impact: DefaultInstanceBrowseName
  - Impact: Delete
  - Impact: DeviceClass
  - Impact: EURange
  - Impact: EndUserObligated
  - Impact: Ended
  - Impact: EndedToInitializing
  - Impact: EngineeringUnits
  - Impact: EnumValues
  - Impact: Equipment
  - Impact: EquipmentID
  - Impact: ErrorCode
  - Impact: FeedOverride
  - Impact: FileSystem
  - Impact: HardwareRevision
  - Impact: IMachineTagNameplateType
  - Impact: IMachineVendorNameplateType
  - Impact: IMachineryItemVendorNameplateType
  - Impact: Id
  - Impact: Identification
  - Impact: Identifier
  - Impact: InitialOperationDate
  - Impact: Initializing
  - Impact: InitializingToAborted
  - Impact: InitializingToRunning
  - Impact: InputArguments
  - Impact: Interrupted
  - ... and 124 more

## Modified nodes
- **EnumValues [Variable, ns=MachineTool, i=190]**
  - Attributes: 4 → 5
  - ArrayDimensions: 4 → 5
  - Value: <Value xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"> <uax:ListOfExtensionObject xmlns:uax="http://opcfoundation.org/UA/2008/02/Types.xsd"> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>0</uax:Value> <uax:DisplayName> <uax:Text>CapabilityUnavailable</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>The machine tool is not able to give a statement about process irregularities.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>1</uax:Value> <uax:DisplayName> <uax:Text>Detected</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>A process irregularity has been detected.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>2</uax:Value> <uax:DisplayName> <uax:Text>NotDetected</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>There was no process irregularity detected.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>3</uax:Value> <uax:DisplayName> <uax:Text>NotYetDetermined</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>A statement about the process irregularity is to be expected.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> </uax:ListOfExtensionObject> </Value> → <Value xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"> <uax:ListOfExtensionObject xmlns:uax="http://opcfoundation.org/UA/2008/02/Types.xsd"> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>0</uax:Value> <uax:DisplayName> <uax:Text>CapabilityUnavailable</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>The machine tool is not able to give a statement about process irregularities.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>1</uax:Value> <uax:DisplayName> <uax:Text>Detected</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>A process irregularity has been detected.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>2</uax:Value> <uax:DisplayName> <uax:Text>NotDetected</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>There was no process irregularity detected.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>3</uax:Value> <uax:DisplayName> <uax:Text>NotYetDetermined</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>A statement about the process irregularity is to be expected.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> <uax:ExtensionObject> <uax:TypeId> <uax:Identifier>i=7616</uax:Identifier> </uax:TypeId> <uax:Body> <uax:EnumValueType> <uax:Value>4</uax:Value> <uax:DisplayName> <uax:Text>NotApplicable</uax:Text> </uax:DisplayName> <uax:Description> <uax:Locale>en</uax:Locale> <uax:Text>Process irregularities are not applicable to this job.</uax:Text> </uax:Description> </uax:EnumValueType> </uax:Body> </uax:ExtensionObject> </uax:ListOfExtensionObject> </Value>
  - Impact: 158 nodes
- **Location [Variable, ns=Machinery, i=6029]**
  - Modelling rule: Optional → Mandatory
  - Impact: 165 nodes
- **MachineryItemIdentificationType [ObjectType, ns=Machinery, i=1004]**
  - - HasProperty -> ManufacturerUri (nsu=http://opcfoundation.org/UA/Machinery/;i=6007) : HasProperty
  - Impact: 173 nodes
- **ProcessIrregularity [DataType, ns=MachineTool, i=62]**
  - + NotApplicable = 4
  - Impact: 158 nodes
- **ProductInstanceUri [Variable, ns=Machinery, i=6015]**
  - DataType: String (i=12) → Double (i=11)
  - Impact: 165 nodes

## Namespace changes
- **Machinery (http://opcfoundation.org/UA/Machinery/)**
  - Nodes: 134 → 133 (-1)
  - References: 519 → 516 (-3)
  - DataTypes: 0 → 0

## Impact
- Total Impact: 494 nodes

## Breaking changes
- **ManufacturerUri [i=6007]: Variable removed**
  - BP-Declaration-Removed-Optional: An optional InstanceDeclaration was removed (§3.2.16): a consumer that browsed the optional declaration now finds nothing, even though it was optional. Publish a new namespace instead (§11.3).
  - Affects 494 nodes
- **EnumValues [i=190]: ArrayDimensions: 4 → 5**
  - BP-ArrayDimensions-Immutable: ArrayDimensions shall not change (§3.2.12): consumers sized for the old array cannot read the new one.
  - Affects 453 nodes
- **Location [i=6029]: Modelling rule: Optional → Mandatory**
  - BP-ModellingRule-Tightening: A declaration that was Optional became Mandatory (§3.2.1): consumers must now expect the component to exist.
  - Affects 466 nodes
- **MachineryItemIdentificationType [i=1004]: - HasProperty -> ManufacturerUri (nsu=http://opcfoundation.org/UA/Machinery/;i=6007) : HasProperty**
  - BP-Reference-OptionalChild-Removed: An Optional component was detached (§3.2.16): detaching an InstanceDeclaration is a breaking change even where it was optional, because consumers that browsed it now find nothing.
  - Affects 493 nodes
- **ProcessIrregularity [i=62]: + NotApplicable = 4**
  - BP-EnumValue-Added: An enum value was added (§3.2.4): the value range of the type changes, generated code is incomplete, and a consumer that rejects unknown values refuses the new ones. Use a new namespace (§11.3).
  - Affects 453 nodes
- **ProductInstanceUri [i=6015]: DataType: String (i=12) → Double (i=11)**
  - BP-DataType-Immutable: The DataType of a published Variable or InstanceDeclaration shall not change (§3.2.12): readers decode the value with the old type.
  - Affects 466 nodes
