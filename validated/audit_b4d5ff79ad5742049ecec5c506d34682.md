### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Causing Transaction Hash Mismatch Across the P2P Sync Boundary - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `transaction.rs` silently collapses `ValidResourceBounds::AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` into `ValidResourceBounds::L1Gas(X)` after a protobuf round-trip. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound felts for each variant (3 for `AllResources`, 2 for `L1Gas`), the transaction hash computed by the originating sequencer and the hash recomputed by a syncing node diverge. This is the direct sequencer analog of the nominator-slashing accounting bug: a value (the resource-bounds variant) is recorded under one representation at commitment time, silently reduced to a different representation at the serialization boundary, and the internal accounting (the stored transaction hash) is never reconciled with the new representation.

---

### Finding Description

**Serialization boundary — the root cause**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436:

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else { … };
        let Some(l2_gas) = value.l2_gas else { … };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // ← silently zero
        …
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)                      // ← variant changed
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
``` [1](#0-0) 

When `l1_data_gas` is absent from the wire message (or present but zero) **and** `l2_gas` is also zero, the converter returns `L1Gas` regardless of whether the sender originally held `AllResources`.

**Hash domain — why the variant change matters**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` lines 188–211 hashes a different number of felts depending on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                        // 2 felts total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts total
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
``` [2](#0-1) 

- `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` → hash over `[tip, L1_GAS‖X, L2_GAS‖0, L1_DATA_GAS‖0]`
- `L1Gas(X)` → hash over `[tip, L1_GAS‖X, L2_GAS‖0]`

These produce **different Poseidon digests** even when the numeric values of all three bounds are identical.

**How the originating sequencer commits the hash**

`InternalRpcInvokeTransactionV3` always stores `resource_bounds: AllResourceBounds` and always wraps it as `ValidResourceBounds::AllResources` when computing the hash:

```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)   // always AllResources
    }
    …
}
``` [3](#0-2) 

The hash `H_all` is computed with 3 resource felts and stored in `InternalRpcTransaction.tx_hash`. [4](#0-3) 

When the transaction is later converted to the storage `InvokeTransactionV3`, it retains `ValidResourceBounds::AllResources`:

```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            …
        }
    }
}
``` [5](#0-4) 

**Serialization of `AllResources` to protobuf**

The `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer faithfully emits `l1_data_gas: Some(0)`:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),   // Some(0) when zero
    },
``` [6](#0-5) 

**Deserialization on the receiving node**

The receiving node calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Because `l1_data_gas.is_zero() && l2_gas.is_zero()`, it returns `L1Gas(X)`. The receiving node then recomputes the hash with only 2 resource felts, producing `H_l1 ≠ H_all`. Any hash-verification step (e.g., `validate_transaction_hash`) will fail, causing the block to be rejected. [7](#0-6) 

---

### Impact Explanation

A syncing or validating node that receives a block containing a V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will:

1. Deserialize the transaction as `L1Gas(X)` (wrong variant).
2. Recompute the transaction hash with 2 resource felts instead of 3.
3. Obtain a hash that does not match the hash committed by the sequencer.
4. Reject the block as having an invalid transaction hash.

This causes a **chain split**: the sequencer and any node that accepted the transaction agree on `H_all`; every node that re-derives the hash from the protobuf wire format computes `H_l1`. The impact matches **"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

Any user can submit a valid V3 invoke transaction with zero `l2_gas` and zero `l1_data_gas` (the gateway's stateless validator explicitly accepts such bounds, as shown in the `valid_l1_gas` test case). The sequencer will include it in a block. Every downstream node that syncs the block via the protobuf P2P path will hit the downgrade. No special privilege is required; a single such transaction in any block triggers the divergence.

---

### Recommendation

Remove the variant-downgrade logic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. The wire format already carries all three fields explicitly; the receiver should reconstruct the exact variant the sender serialized. One correct approach:

```rust
// Always reconstruct AllResources when all three fields are present on the wire.
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

The `L1Gas` variant should only be produced when the wire message genuinely omits `l2_gas` (i.e., for pre-0.13.3 transactions that never had the field). The existing `TODO(Shahak)` comment acknowledges this debt; it should be resolved by making `l1_data_gas` mandatory and removing the downgrade path entirely.

---

### Proof of Concept

```
1. Craft a V3 invoke transaction with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit via RPC. The gateway accepts it.
   The converter computes:
     H_all = poseidon(tip,
                      concat(L1_GAS, 1000, 1),
                      concat(L2_GAS, 0, 0),
                      concat(L1_DATA_GAS, 0, 0))
   and stores H_all as tx_hash.

3. The sequencer includes the transaction in block B.
   Block B is stored with H_all as the transaction hash.

4. A syncing node receives block B via the P2P protobuf path.
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds fires:
     l1_data_gas.is_zero() && l2_gas.is_zero()  →  L1Gas(l1_gas)

5. The syncing node recomputes:
     H_l1 = poseidon(tip,
                     concat(L1_GAS, 1000, 1),
                     concat(L2_GAS, 0, 0))
   H_l1 ≠ H_all.

6. validate_transaction_hash returns false.
   The syncing node rejects block B.
   The sequencer and the syncing node are now on divergent views of the chain.
```

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

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-694)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```
