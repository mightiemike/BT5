### Title
`ValidResourceBounds` Protobuf Round-Trip Collapses `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently collapses `AllResources { l1_gas: X, l2_gas: ZERO, l1_data_gas: ZERO }` into `L1Gas(X)`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource elements** for each variant, the two representations produce **different transaction hashes** for the same economic payload. A V3 transaction submitted via RPC with zero L2 and L1-data-gas bounds is accepted and hashed under the `AllResources` path (3 resources), but after any protobuf round-trip (P2P block sync, mempool propagation) the same transaction is reconstructed under the `L1Gas` path (2 resources), yielding a divergent hash.

---

### Finding Description

**Step 1 – The hash function branches on variant, not on values.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the Poseidon pre-image differently for each variant:

```
L1Gas(X)          → [tip, l1_packed, l2_packed(0)]          // 3 elements
AllResources{X,0,0} → [tip, l1_packed, l2_packed(0), l1data_packed(0)]  // 4 elements
``` [1](#0-0) 

Because Poseidon is length-sensitive, these two inputs produce **different digests** even when all numeric values are identical.

**Step 2 – The protobuf serializer emits identical bytes for both variants.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` serializes `L1Gas(X)` by filling `l1_data_gas` with `ResourceBounds::default()` (all zeros). `AllResources{X, 0, 0}` also emits all-zero `l1_data_gas`. The wire bytes are byte-for-byte identical. [2](#0-1) 

**Step 3 – The protobuf deserializer always reconstructs `L1Gas` when both L2 and L1-data-gas are zero.**

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(...)
})
``` [3](#0-2) 

So `AllResources{X, 0, 0}` → protobuf → `L1Gas(X)`. The variant is permanently changed.

**Step 4 – The gateway always stores the `AllResources` variant for new V3 transactions.**

`RpcInvokeTransactionV3` carries `AllResourceBounds` (never `ValidResourceBounds`). The converter wraps it unconditionally as `ValidResourceBounds::AllResources(...)`, so a user who submits a V3 transaction with zero L2/L1-data-gas bounds gets hash H_A computed over 4 Poseidon elements. [4](#0-3) [5](#0-4) 

**Step 5 – After any protobuf round-trip the hash is recomputed as H_B ≠ H_A.**

`validate_transaction_hash` and any re-execution path that calls `calculate_transaction_hash` on the deserialized `InvokeTransactionV3` will now use the `L1Gas` branch (2 resources), producing H_B. The stored/signed hash is H_A. The two diverge. [6](#0-5) 

---

### Impact Explanation

A syncing node that receives a block via P2P deserializes each `InvokeTransactionV3` through the protobuf path. For any transaction whose original resource bounds had zero L2 gas and zero L1-data gas, the deserialized `ValidResourceBounds` variant is `L1Gas`, not `AllResources`. Any subsequent call to `calculate_transaction_hash` (for signature verification, block-hash recomputation, or re-execution) produces a hash that does not match the hash the sequencer computed and signed over. This causes:

- **Signature verification failure** for a legitimately signed transaction (the account signed over H_A; the verifier checks H_B).
- **Block hash divergence** between the sequencing node and syncing nodes, since the transaction hash feeds into the block hash.
- **Wrong execution receipt** if the transaction is re-executed under the wrong hash identity.

This matches the allowed impact: *"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload"* (High) and *"Wrong state, receipt, event … from blockifier/syscall/execution logic for accepted input"* (Critical).

---

### Likelihood Explanation

Any unprivileged user can trigger this by submitting a standard V3 `starknet_addInvokeTransaction` RPC call with `resource_bounds.l2_gas = {max_amount: 0, max_price_per_unit: 0}` and `resource_bounds.l1_data_gas = {max_amount: 0, max_price_per_unit: 0}`. This is a structurally valid transaction that passes all gateway checks. The divergence is automatic and deterministic on every P2P sync of that block.

---

### Recommendation

Fix the protobuf deserializer to preserve the `AllResources` variant whenever the original message explicitly carries an `l1_data_gas` field, regardless of its value:

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
// Use Option presence, not zero-value, to distinguish variants.
match value.l1_data_gas {
    None => ValidResourceBounds::L1Gas(l1_gas),
    Some(raw) => {
        let l1_data_gas: ResourceBounds = raw.try_into()?;
        ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
    }
}
```

Alternatively, canonicalize `AllResources{X, 0, 0}` to `L1Gas(X)` **before** computing the transaction hash in the gateway, so that the hash is always computed from the canonical variant.

---

### Proof of Concept

```
1. Submit via RPC:
   starknet_addInvokeTransaction {
     version: "0x3",
     resource_bounds: {
       L1_GAS:      { max_amount: "0x100", max_price_per_unit: "0x1" },
       L2_GAS:      { max_amount: "0x0",   max_price_per_unit: "0x0" },
       L1_DATA_GAS: { max_amount: "0x0",   max_price_per_unit: "0x0" }
     },
     ...
   }

2. Gateway stores the transaction with:
   ValidResourceBounds::AllResources { l1_gas: X, l2_gas: ZERO, l1_data_gas: ZERO }
   Hash H_A = Poseidon([INVOKE, 3, sender, Poseidon([tip, l1_packed, l2_packed(0), l1data_packed(0)]), ...])
   (4-element resource sub-hash)

3. Block is produced and propagated via P2P protobuf sync.

4. Syncing node deserializes the transaction:
   protobuf::ResourceBounds { l1_gas: X, l2_gas: ZERO, l1_data_gas: ZERO }
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → ValidResourceBounds::L1Gas(X)

5. Syncing node recomputes hash:
   Hash H_B = Poseidon([INVOKE, 3, sender, Poseidon([tip, l1_packed, l2_packed(0)]), ...])
   (3-element resource sub-hash)

6. H_A ≠ H_B.
   Signature verification fails. Block hash diverges. State is inconsistent.
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L165-185)
```rust
/// Validates the hash of a starknet transaction.
/// For transactions on testnet or those with a low block_number, we validate the
/// transaction hash against all potential historical hash computations. For recent
/// transactions on mainnet, the hash is validated by calculating the precise hash
/// based on the transaction version.
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
```rust
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
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
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-583)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L338-392)
```rust
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
