### Title
`ValidResourceBounds` variant silently mutates from `AllResources` to `L1Gas` across P2P protobuf boundary, causing valid transactions to be rejected by peers - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserialization of `ResourceBounds` into `ValidResourceBounds` uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. When an `AllResources` transaction has both `l2_gas` and `l1_data_gas` set to zero (a valid no-fee-enforcement configuration), the deserialized variant silently becomes `L1Gas`. This breaks the downstream conversion to `RpcInvokeTransactionV3`, which explicitly rejects the `L1Gas` variant, causing the transaction to be rejected by any peer that receives it over P2P — even though the originating gateway accepted it and computed a valid hash.

---

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:** [1](#0-0) 

The conversion uses `l1_data_gas.is_zero() && l2_gas.is_zero()` as the sole discriminant between `L1Gas` and `AllResources`. There is no field in the protobuf wire format that encodes which Rust variant was originally used. A transaction originally created as `AllResources { l1_gas: X, l2_gas: {0,0}, l1_data_gas: {0,0} }` is indistinguishable on the wire from a `L1Gas(X)` transaction.

**The test explicitly acknowledges this:** [2](#0-1) 

The comment reads: *"If all the fields of `AllResources` are 0 upon serialization, then the deserialized value will be interpreted as the `L1Gas` variant."* The test workaround sets `l2_gas.max_amount = GasAmount(1)` to avoid the problem, but this is not enforced in production.

**The downstream conversion fails hard:** [3](#0-2) 

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` explicitly returns `Err(StarknetApiError::OutOfRange)` when `resource_bounds` is `ValidResourceBounds::L1Gas`. This is the conversion used when a peer receives a mempool or consensus transaction over P2P.

**The hash domain diverges between originator and receiver:**

The originating gateway always computes the hash using the `AllResources` path (3 resource entries: L1Gas, L2Gas, L1DataGas): [4](#0-3) 

The `L1Gas` path hashes only 2 resource entries (omits `l1_data_gas`). Even if the peer did not fail on conversion, it would compute a different `tx_hash` than the originator.

**The gateway always uses `AllResources` for new transactions:** [5](#0-4) 

`InternalRpcInvokeTransactionV3.resource_bounds` is typed as `AllResourceBounds` (not `ValidResourceBounds`), so the gateway always computes the hash with the `AllResources` path regardless of whether the bounds are zero.

**The conversion path at the gateway:** [6](#0-5) 

`convert_rpc_tx_to_internal` recalculates `tx_hash` from the internal representation. The gateway's hash uses `AllResources`. The peer's hash (if it could even reach this point) would use `L1Gas`.

---

### Impact Explanation

**High. Mempool/gateway/RPC admission rejects valid transactions before sequencing.**

A user submitting a V3 invoke transaction with `AllResourceBounds { l1_gas: X, l2_gas: {0,0}, l1_data_gas: {0,0} }` (a legitimate no-fee-enforcement configuration) will have their transaction:

1. Accepted and hashed by the gateway using the `AllResources` path.
2. Propagated over P2P (mempool or consensus).
3. **Rejected by every peer** because `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` produces `L1Gas`, and `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` returns an error for `L1Gas`.

In the consensus path, if a proposer includes such a transaction in a block proposal, every validator will fail to deserialize it and reject the proposal, potentially causing consensus liveness failures.

---

### Likelihood Explanation

The trigger is any V3 invoke transaction with zero `l2_gas` and zero `l1_data_gas` bounds. This is a valid and reachable configuration — `AllResourceBounds::new_unlimited_gas_no_fee_enforcement()` itself uses zero `l1_gas` and zero `l1_data_gas` (though it sets a non-zero `l2_gas.max_amount` to avoid this exact issue). Any user or system that constructs `AllResourceBounds` with all-zero L2/L1DataGas bounds will trigger this. The codebase's own test infrastructure explicitly works around it, confirming the condition is reachable.

---

### Recommendation

The protobuf `ResourceBounds` message should encode the variant explicitly, e.g., by adding a boolean `is_all_resources` field, or by treating the presence of `l1_data_gas` as the discriminant (currently it is `optional` and defaults to zero, which is ambiguous). Alternatively, the `TryFrom` implementation should always produce `AllResources` when `l1_data_gas` is present in the wire message (even if zero), and only produce `L1Gas` when `l1_data_gas` is absent (`None`):

```rust
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The serialization side (`From<ValidResourceBounds> for protobuf::ResourceBounds`) already sets `l1_data_gas: Some(...)` for both variants, so the `None` vs `Some(zero)` distinction is currently lost. The serializer for `L1Gas` should omit `l1_data_gas` (set it to `None`) to preserve round-trip fidelity.

---

### Proof of Concept

1. Submit a V3 invoke transaction to the gateway with `resource_bounds = AllResourceBounds { l1_gas: {max_amount: 1000, max_price: 1}, l2_gas: {0, 0}, l1_data_gas: {0, 0} }`.
2. The gateway accepts it and computes `tx_hash = H_AllResources` (3-resource hash).
3. The transaction is propagated over P2P as protobuf `InvokeV3` with `resource_bounds = {l1_gas: {1000,1}, l2_gas: {0,0}, l1_data_gas: {0,0}}`.
4. A peer deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas({1000,1})`.
5. Peer calls `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` → returns `Err(StarknetApiError::OutOfRange { string: "resource_bounds" })`.
6. Transaction is rejected by the peer. It is never sequenced despite being accepted by the gateway.

The existing test at `crates/apollo_protobuf/src/converters/consensus_test.rs` line 43 (`resource_bounds.l2_gas.max_amount = GasAmount(1)`) is a direct acknowledgment that this path is broken for all-zero bounds.

### Citations

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-611)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L615-628)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct InternalRpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub proof_facts: ProofFacts,
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-392)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```
