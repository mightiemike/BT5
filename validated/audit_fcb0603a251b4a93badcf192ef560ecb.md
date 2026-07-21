### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash Across the P2P Boundary - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter classifies any incoming protobuf message as `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero — even when the originating node serialized the transaction as `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a **different number of resource fields** depending on the variant (`L1Gas` → 2 fields; `AllResources` → 3 fields), a transaction whose resource bounds happen to have `l2_gas = 0` and `l1_data_gas = 0` will produce hash `H_AllResources` on the submitting node and hash `H_L1Gas ≠ H_AllResources` on any node that reconstructs it from protobuf. This is a direct serialization/hash-domain inconsistency: two representations of the same logical transaction bind to different canonical hashes.

### Finding Description

**Step 1 — Originating node computes hash as `AllResources`.**

`RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` both carry `resource_bounds: AllResourceBounds`. When the gateway converts the transaction to its internal form, the hash is computed via:

```
get_invoke_transaction_v3_hash
  → get_tip_resource_bounds_hash(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }), tip)
```

`get_tip_resource_bounds_hash` for `AllResources` appends **three** packed resource felts (L1, L2, L1DataGas) to the Poseidon chain: [1](#0-0) 

**Step 2 — Serialization preserves all three fields.**

`From<AllResourceBounds> for protobuf::ResourceBounds` routes through `ValidResourceBounds::AllResources(value).into()`, which always emits all three fields: [2](#0-1) [3](#0-2) 

**Step 3 — Deserialization silently downgrades to `L1Gas`.**

On the receiving node, `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` checks:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← chosen even for AllResources origin
} else {
    ValidResourceBounds::AllResources(...)
})
``` [4](#0-3) 

When `l2_gas = 0` and `l1_data_gas = 0`, the reconstructed variant is `L1Gas`, regardless of what the sender intended.

**Step 4 — Hash recomputed as `L1Gas` produces a different value.**

`get_tip_resource_bounds_hash` for `L1Gas` appends only **two** packed resource felts (L1, L2=0), omitting the L1DataGas field entirely: [5](#0-4) 

The Poseidon hash over `[tip, L1, L2]` ≠ Poseidon hash over `[tip, L1, L2, L1DataGas]`, even when L2 and L1DataGas are both zero, because the input length differs. The stored `tx_hash` in `InternalRpcTransaction` therefore diverges from the hash any peer computes after deserialization: [6](#0-5) 

The same divergence applies to `InvokeTransactionV3` (used in storage/sync), which carries `resource_bounds: ValidResourceBounds` and is subject to the same protobuf round-trip: [7](#0-6) 

### Impact Explanation

Any transaction with `AllResourceBounds { l1_gas: X (non-zero), l2_gas: 0, l1_data_gas: 0 }` passes gateway validation (non-zero max fee) and is assigned hash `H_AllResources`. After P2P propagation, every receiving node reconstructs the transaction with `ValidResourceBounds::L1Gas` and computes `H_L1Gas ≠ H_AllResources`. Depending on whether the receiving node trusts the sender's hash or recomputes it:

- **Recomputes:** The transaction is rejected as having an invalid hash — a valid, gateway-accepted transaction is silently dropped by all P2P peers.
- **Trusts sender hash:** The transaction is stored with hash `H_AllResources` but its internal `ValidResourceBounds` is `L1Gas`; any subsequent local hash verification (e.g., during block execution or receipt generation) will produce `H_L1Gas`, creating a persistent hash/state inconsistency.

Both outcomes match **High: Transaction conversion or signature/hash logic binds the wrong hash or executable payload**.

### Likelihood Explanation

The trigger condition — `AllResourceBounds` with `l2_gas = 0` and `l1_data_gas = 0` — is reachable by any unprivileged user submitting a V3 transaction that only specifies L1 gas (a common pattern for users migrating from pre-0.13.3 behavior). The gateway's `validate_resource_bounds` only requires that `max_possible_fee > 0`, which is satisfied by a non-zero L1 gas bound alone: [8](#0-7) 

No privileged access is required.

### Recommendation

**Short term:** In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, do not use the zero-value heuristic to select the variant. Instead, require an explicit version tag or a dedicated boolean field in the protobuf schema to distinguish `L1Gas` from `AllResources`. Until the schema is updated, default to `AllResources` when `l1_data_gas` is absent (backward-compat case) rather than downgrading based on value.

**Long term:** Add a round-trip property test asserting that for any `ValidResourceBounds` value, `serialize → deserialize` is the identity function, and that the transaction hash is invariant across the protobuf boundary. The TODO comment at line 426 (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) should be resolved by removing the `unwrap_or_default` fallback and enforcing the field's presence. [9](#0-8) 

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds::default(),   // zero
         l1_data_gas: ResourceBounds::default(),   // zero
     }

2. Submit to gateway → passes validate_resource_bounds (max_possible_fee = 1000 > 0).
   Gateway computes H_AllResources = Poseidon(INVOKE, ver, addr,
       Poseidon(tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0), pack(L1_DATA_GAS,0,0)),
       ...).

3. Serialize resource_bounds to protobuf:
     { l1_gas: Some({1000,1}), l2_gas: Some({0,0}), l1_data_gas: Some({0,0}) }

4. Deserialize on receiving node:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas({1000,1})

5. Receiving node recomputes hash H_L1Gas = Poseidon(INVOKE, ver, addr,
       Poseidon(tip, pack(L1_GAS,1000,1), pack(L2_GAS,0,0)),   // only 2 resources
       ...).

6. H_AllResources ≠ H_L1Gas → transaction rejected or stored with inconsistent hash.
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L197-210)
```rust
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
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L226-230)
```rust
impl From<AllResourceBounds> for protobuf::ResourceBounds {
    fn from(value: AllResourceBounds) -> Self {
        ValidResourceBounds::AllResources(value).into()
    }
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L143-147)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash, SizeOf)]
pub struct InternalRpcTransaction {
    pub tx: InternalRpcTransactionWithoutTxHash,
    pub tx_hash: TransactionHash,
}
```

**File:** crates/starknet_api/src/transaction.rs (L663-688)
```rust
/// An invoke V3 transaction.
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct InvokeTransactionV3 {
    pub resource_bounds: ValidResourceBounds,
    pub tip: Tip,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
}

impl TransactionHasher for InvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L64-69)
```rust
        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }
```
