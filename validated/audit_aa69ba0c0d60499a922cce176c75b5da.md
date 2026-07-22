### Title
Consensus Proposal Deserialization Fails for V3 Transactions with Zero L2/L1DataGas Bounds Due to `ValidResourceBounds` Type Coercion in Protobuf Converter — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

A V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and placed in the mempool. When the proposer serializes a block proposal containing this transaction and sends it to validators via P2P, the validators fail to deserialize the `ConsensusTransaction`. The root cause is a heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` that coerces `AllResources(l1_gas, l2_gas=0, l1_data_gas=0)` into `L1Gas(l1_gas)` on deserialization, which then fails the `AllResources`-only check in the RPC transaction converter. The test suite explicitly acknowledges and works around this bug but does not fix it.

---

### Finding Description

**Root cause — `crates/apollo_protobuf/src/converters/transaction.rs`:** [1](#0-0) 

The deserialization heuristic at line 431 classifies any protobuf `ResourceBounds` with `l1_data_gas = 0` and `l2_gas = 0` as `ValidResourceBounds::L1Gas`, regardless of whether the originating transaction was an `AllResources` V3 transaction.

**Serialization path (proposer side):**

`InternalRpcInvokeTransactionV3` stores `AllResourceBounds` and implements `InvokeTransactionV3Trait::resource_bounds()` by wrapping it as `ValidResourceBounds::AllResources(self.resource_bounds)`. [2](#0-1) 

When the proposer converts `InternalConsensusTransaction` → `ConsensusTransaction` → `protobuf::ConsensusTransaction`, the `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` is serialized via `From<ValidResourceBounds> for protobuf::ResourceBounds`: [3](#0-2) 

This produces a protobuf with all three fields present but `l2_gas` and `l1_data_gas` set to zero.

**Deserialization path (validator side):**

On the receiving node, `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` calls: [4](#0-3) 

For an Invoke V3, this calls `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`: [5](#0-4) 

The comment at line 129 states: *"This conversion can fail only if the resource_bounds are not AllResources."* The `snapi_invoke.try_into()` call converts `InvokeTransactionV3` (which now holds `ValidResourceBounds::L1Gas` due to the heuristic) to `RpcInvokeTransactionV3` (which requires `AllResourceBounds`). This conversion **fails** and returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`, propagating up through the `?` operator and causing the entire `ConsensusTransaction` deserialization to fail.

**Test acknowledgment of the bug:**

The test file explicitly works around this issue rather than fixing it: [6](#0-5) 

The workaround forces `l2_gas.max_amount = GasAmount(1)` to prevent the coercion, confirming the production code path is broken.

---

### Impact Explanation

When a proposer includes a valid V3 transaction with `l2_gas = 0` and `l1_data_gas = 0` in a block proposal, every validator that receives the proposal via P2P will fail to deserialize the `ConsensusTransaction`. The `?` propagation in `TryFrom<protobuf::ConsensusTransaction>` causes the entire transaction batch deserialization to fail. Validators cannot process the proposal, causing consensus failure or silent transaction exclusion from the committed block. This matches: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.**

The same bug also affects mempool P2P propagation: such a transaction cannot be forwarded to peer nodes via `protobuf::MempoolTransaction`, matching **High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

---

### Likelihood Explanation

Any unprivileged user can submit a V3 transaction with only `l1_gas > 0` and both `l2_gas = 0` and `l1_data_gas = 0`. The gateway stateless validator explicitly allows transactions with a single non-zero resource bound: [7](#0-6) 

This is a common pattern for wallets that have not yet adopted the full three-resource-bound model. No special privileges or malicious intent are required; a standard wallet submission triggers the bug.

---

### Recommendation

Replace the zero-value heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` with an explicit discriminator field in the protobuf schema (e.g., a boolean `is_all_resources` or an enum tag), so that the variant is preserved across serialization boundaries regardless of field values. Until the schema is updated, the converter should default to `AllResources` when `l1_data_gas` is present in the protobuf message (even if zero), reserving `L1Gas` only for messages that omit `l1_data_gas` entirely (the pre-0.13.3 wire format). [8](#0-7) 

---

### Proof of Concept

```rust
// Construct a valid V3 invoke transaction with only l1_gas set (l2_gas=0, l1_data_gas=0).
// This is accepted by the gateway stateless validator.
let resource_bounds = AllResourceBounds {
    l1_gas: ResourceBounds { max_amount: GasAmount(1000), max_price_per_unit: GasPrice(1) },
    l2_gas: ResourceBounds::default(),      // zero — triggers the bug
    l1_data_gas: ResourceBounds::default(), // zero — triggers the bug
};
let rpc_invoke = RpcInvokeTransactionV3 { resource_bounds, /* ... other fields ... */ };
let consensus_tx = ConsensusTransaction::RpcTransaction(
    RpcTransaction::Invoke(RpcInvokeTransaction::V3(rpc_invoke))
);

// Step 1: Proposer serializes the consensus transaction to protobuf.
// AllResources(l1_gas=1000, l2_gas=0, l1_data_gas=0) → protobuf with all three fields = 0/0.
let proto: protobuf::ConsensusTransaction = consensus_tx.into();

// Step 2: Validator deserializes the protobuf.
// TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
//   l1_data_gas.is_zero() && l2_gas.is_zero() → L1Gas(l1_gas=1000)  ← WRONG TYPE
// TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3:
//   resource_bounds is L1Gas, not AllResources → DEPRECATED_RESOURCE_BOUNDS_ERROR
let result = ConsensusTransaction::try_from(proto);
assert!(result.is_err()); // Validator rejects the entire proposal batch.
```

The `add_gas_values_to_transaction` workaround in `consensus_test.rs` (line 43: `resource_bounds.l2_gas.max_amount = GasAmount(1)`) confirms that setting any non-zero value prevents the coercion and makes the round-trip succeed, proving the exact trigger condition.

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L479-488)
```rust
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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
