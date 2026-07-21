### Title
`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` Silently Coerces `AllResources` to `L1Gas` When l2_gas and l1_data_gas Are Zero, Breaking Protobuf Round-Trip for Valid Transactions - (File: crates/apollo_protobuf/src/converters/transaction.rs)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. When a valid `AllResources` transaction carries zero l2_gas and zero l1_data_gas, the deserializer silently produces `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`. The downstream conversion `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` then hard-rejects any non-`AllResources` variant, causing the transaction to be dropped entirely. This breaks the protobuf round-trip for both the P2P mempool path (`MempoolTransaction`) and the consensus proposal path (`ConsensusTransaction`), and the codebase's own test suite explicitly works around the issue rather than fixing it.

### Finding Description

In `crates/apollo_protobuf/src/converters/transaction.rs`, the conversion from `protobuf::ResourceBounds` to `ValidResourceBounds` applies a content-based heuristic:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

This heuristic is a backward-compatibility shim for pre-0.13.3 transactions that only carried L1 gas. However, it also fires for any modern `AllResources` transaction whose l2_gas and l1_data_gas happen to be zero — a perfectly valid configuration where the user only pays for L1 gas.

The resulting `L1Gas` variant then flows into `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`:

```rust
Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
``` [2](#0-1) 

The inner `try_into()` is `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`, which hard-rejects any non-`AllResources` variant:

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => { return Err(StarknetApiError::OutOfRange { ... }); }
},
``` [3](#0-2) 

Both the `MempoolTransaction` P2P path and the `ConsensusTransaction` P2P path serialize invoke transactions as `InvokeV3WithProof` and deserialize through this same chain: [4](#0-3) [5](#0-4) 

The codebase's own consensus test explicitly acknowledges the breakage and works around it by injecting a non-zero l2_gas value before serialization:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
resource_bounds.l2_gas.max_amount = GasAmount(1);
``` [6](#0-5) 

There is also a hash-domain consequence. `get_tip_resource_bounds_hash` produces a different digest for `L1Gas` (hashes tip + l1_gas + l2_gas, two resource felts) versus `AllResources` (hashes tip + l1_gas + l2_gas + l1_data_gas, three resource felts): [7](#0-6) 

So even if the rejection were bypassed, the recalculated hash would diverge from the hash the signer committed to.

### Impact Explanation

**Consensus proposal rejection (High):** A proposer builds a block from the mempool using `InternalRpcTransaction` (no protobuf involved). If the block contains a valid `AllResources` invoke with l2_gas=0 and l1_data_gas=0, the proposer serializes it to `InvokeV3WithProof` and broadcasts the proposal. Every validator that deserializes the proposal hits `DEPRECATED_RESOURCE_BOUNDS_ERROR` and rejects the entire proposal. The proposer's round fails, and the attacker can repeat this for every subsequent round in which the transaction remains in the mempool.

**P2P mempool propagation rejection (High):** The same transaction fails to propagate between nodes via `MempoolTransaction` protobuf messages, so the transaction is siloed to the node that received it from the gateway.

**Wrong transaction hash on state sync:** The `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` path used during state sync also calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, producing `L1Gas`. The hash recomputed from the synced `L1Gas` transaction omits the l1_data_gas felt, yielding a different value than the hash the original signer produced over `AllResources`. [8](#0-7) 

### Likelihood Explanation

The gateway's stateless validator explicitly accepts `AllResources` transactions where only l1_gas is non-zero (l2_gas=0, l1_data_gas=0): [9](#0-8) 

Any user who submits such a transaction — a normal, signed, fee-paying invoke — triggers the issue. No privileged access is required. The attacker only needs to know the current proposer's mempool is non-empty with such a transaction.

### Recommendation

Replace the zero-value heuristic with an explicit discriminant. The protobuf `ResourceBounds` message should carry a boolean or enum field that encodes whether the sender intended `L1Gas` or `AllResources`, mirroring the approach used by the JSON serializer (`ValidResourceBounds::Serialize` includes `L1DataGas` in the map only for `AllResources`, making the variant unambiguous on deserialization). Until the wire format is updated, the deserializer should default to `AllResources` when `l1_data_gas` is absent (preserving the 0.13.2 shim) but never silently downgrade a message that explicitly carries a zero-valued `l1_data_gas` field. [10](#0-9) 

### Proof of Concept

1. Construct a valid `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: NON_ZERO, l2_gas: ZERO, l1_data_gas: ZERO }`.
2. Serialize it: `protobuf::MempoolTransaction::from(RpcTransaction::Invoke(...))` → produces `InvokeV3WithProof` with `invoke.resource_bounds.l2_gas = zero` and `invoke.resource_bounds.l1_data_gas = zero`.
3. Deserialize: `RpcTransaction::try_from(protobuf::MempoolTransaction)` → `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` → `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` returns `L1Gas` → `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` returns `Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)`.
4. The same failure occurs for `ConsensusTransaction` via `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction`.
5. Submit this transaction to the gateway (it passes all stateless and stateful checks), wait for it to be included in a proposal, and observe that every validator rejects the proposal with a conversion error.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-435)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
        })
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1052)
```rust
impl TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ConsensusTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("ConsensusTransaction::txn"))?;
        let txn = match txn {
            protobuf::consensus_transaction::Txn::DeclareV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::DeployAccountV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::L1Handler(txn) => {
                ConsensusTransaction::L1Handler(txn.try_into()?)
            }
        };
        Ok(txn)
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-131)
```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Proof::from(std::mem::take(&mut value.proof));

        let snapi_invoke: InvokeTransactionV3 = value
            .invoke
            .ok_or(ProtobufConversionError::MissingField {
                field_description: "InvokeV3WithProof::invoke",
            })?
            .try_into()?;

        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L590-597)
```rust
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-47)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
            }
        },
        ConsensusTransaction::L1Handler(_) => {}
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

**File:** crates/starknet_api/src/transaction/fields.rs (L551-572)
```rust
impl Serialize for ValidResourceBounds {
    fn serialize<S>(&self, s: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let map = match self {
            ValidResourceBounds::L1Gas(l1_gas) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, ResourceBounds::default()),
            ]),
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, *l2_gas),
                (Resource::L1DataGas, *l1_data_gas),
            ]),
        };
        DeprecatedResourceBoundsMapping(map).serialize(s)
    }
```
