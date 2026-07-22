### Title
`ValidResourceBounds` Variant Re-Derived from Wire Values Causes Transaction Hash Divergence Between Gateway and P2P Sync Paths — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `ValidResourceBounds` enum has two variants — `L1Gas` and `AllResources` — that produce **different transaction hashes** via `get_tip_resource_bounds_hash`. The gateway path always assigns `AllResources` for new `InvokeV3` transactions, but the P2P protobuf deserialization path re-derives the variant from wire values: if `l2_gas == 0` and `l1_data_gas == 0`, it silently downgrades to `L1Gas`. A transaction submitted with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` receives hash H_all at the gateway but hash H_l1 ≠ H_all when reconstructed by a syncing peer, breaking hash canonicalization across the network.

---

### Finding Description

**Hash computation is variant-sensitive.** `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` hashes a different number of resource-bound elements depending on the variant:

```
L1Gas      → [tip, L1_GAS, L2_GAS]          (2 resource felts)
AllResources → [tip, L1_GAS, L2_GAS, L1_DATA_GAS]  (3 resource felts)
``` [1](#0-0) 

**Gateway path always assigns `AllResources`.** `InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` (not `ValidResourceBounds`) and its `InvokeTransactionV3Trait` implementation hard-wraps it as `ValidResourceBounds::AllResources(...)`: [2](#0-1) 

The hash is computed from this `AllResources` variant and stored in `InternalRpcTransaction.tx_hash`. [3](#0-2) 

**P2P protobuf path re-derives the variant.** `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` inspects the wire values and downgrades to `L1Gas` when both `l2_gas` and `l1_data_gas` are zero: [4](#0-3) 

`InvokeTransactionV3Trait` for `InvokeTransactionV3` returns the stored variant directly (not always `AllResources`): [5](#0-4) 

**Concrete divergence.** For a transaction with `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`:

| Path | Variant used | Resources hashed | Hash |
|---|---|---|---|
| Gateway (`InternalRpcInvokeTransactionV3`) | `AllResources` | L1_GAS, L2_GAS, L1_DATA_GAS | H_all |
| P2P sync (`InvokeTransactionV3` from protobuf) | `L1Gas` | L1_GAS, L2_GAS | H_l1 |

H_all ≠ H_l1 because `get_tip_resource_bounds_hash` chains a different number of elements into the Poseidon hash. [6](#0-5) 

---

### Impact Explanation

**High — Transaction conversion or signature/hash logic binds the wrong hash.**

A syncing peer reconstructs the transaction from protobuf, recomputes H_l1, and compares it against the block-committed H_all. The hashes differ, causing the peer to reject the block or store the transaction under the wrong hash. This breaks P2P sync for any block containing an `InvokeV3` transaction with `AllResources` where `l2_gas=0` and `l1_data_gas=0`.

Additionally, RPC fee estimation and simulation that re-execute the P2P-synced transaction would use `GasVectorComputationMode::NoL2Gas` instead of `GasVectorComputationMode::All`, producing wrong fee estimates. [7](#0-6) 

---

### Likelihood Explanation

Any user can submit an `InvokeV3` transaction via the RPC gateway with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway accepts it (no validation rejects zero `l2_gas`/`l1_data_gas` for `AllResources`), computes H_all, and includes it in a block. Every peer that syncs this block via P2P will compute H_l1 ≠ H_all and fail. This is triggerable by any unprivileged user with a single transaction submission. [8](#0-7) 

---

### Recommendation

The `ValidResourceBounds` variant must be preserved across serialization boundaries rather than re-derived from zero-checks on wire values. Options:

1. **Encode the variant explicitly in protobuf**: Add a boolean or enum field to `ResourceBounds` proto that records whether the transaction was `L1Gas` or `AllResources`, and use it during deserialization instead of the zero-check heuristic.

2. **Require non-zero `l1_data_gas` for `AllResources` at the gateway**: Reject `AllResources` transactions where both `l2_gas` and `l1_data_gas` are zero at the gateway stateless validator, so the ambiguous case never enters the system.

3. **Normalize at hash computation**: Change `get_tip_resource_bounds_hash` to always include `L1_DATA_GAS` for V3 transactions regardless of variant, making the hash invariant to the `L1Gas`/`AllResources` distinction when `l1_data_gas=0`. [9](#0-8) 

---

### Proof of Concept

1. Submit an `InvokeV3` transaction via the gateway with:
   ```
   resource_bounds: AllResourceBounds {
       l1_gas: { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas: { max_amount: 0, max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0, max_price_per_unit: 0 },
   }
   ```

2. The gateway converts this to `InternalRpcInvokeTransactionV3` with `resource_bounds: AllResourceBounds { ... }`. The `resource_bounds()` method returns `ValidResourceBounds::AllResources(...)`. `get_tip_resource_bounds_hash` hashes `[tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt]` → H_all.

3. The transaction is included in a block with hash H_all.

4. A peer receives the block via P2P. The protobuf `ResourceBounds` has `l2_gas=0` and `l1_data_gas=0`. `ValidResourceBounds::try_from(...)` returns `ValidResourceBounds::L1Gas(l1_gas)`. `get_tip_resource_bounds_hash` hashes `[tip, L1_GAS_felt, L2_GAS_felt]` (no L1_DATA_GAS) → H_l1.

5. H_all ≠ H_l1. The peer's hash validation fails. The block is rejected or the transaction is stored under the wrong hash, breaking sync. [10](#0-9) [11](#0-10)

### Citations

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

**File:** crates/starknet_api/src/transaction_hash.rs (L407-410)
```rust
impl InvokeTransactionV3Trait for InvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        self.resource_bounds
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
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

**File:** crates/starknet_api/src/transaction/fields.rs (L416-421)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
    }
```
