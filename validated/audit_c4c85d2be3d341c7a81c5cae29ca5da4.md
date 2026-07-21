### Title
`ValidResourceBounds::AllResources` with zero L2/L1DataGas silently collapses to `L1Gas` in P2P protobuf deserialization, producing a divergent transaction hash and execution mode - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation in the P2P transaction converter silently collapses an `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero into the `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of elements for each variant, the transaction hash computed from the deserialized object differs from the hash the user signed. Simultaneously, the execution mode switches from `GasVectorComputationMode::All` to `NoL2Gas`, changing which resource bounds are enforced during execution.

### Finding Description

**Root cause — protobuf deserialization collapses the variant:** [1](#0-0) 

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← variant changed
} else {
    ValidResourceBounds::AllResources(...)
})
```

Any `AllResources` transaction whose `l2_gas` and `l1_data_gas` fields are zero (a valid post-0.13.3 transaction that only specifies L1 gas) is silently re-typed as `L1Gas` after a protobuf round-trip.

**Hash domain divergence — different element counts:**

`get_tip_resource_bounds_hash` branches on the variant: [2](#0-1) 

- `L1Gas` → hashes **3 elements**: `[tip, l1_packed, l2_zero_packed]`
- `AllResources` → hashes **4 elements**: `[tip, l1_packed, l2_zero_packed, l1_data_zero_packed]`

`l2_zero_packed` and `l1_data_zero_packed` are distinct non-zero field elements because `get_concat_resource` embeds the resource-name bytes (`L2_GAS` vs `L1_DATA_GAS`) into the packed felt: [3](#0-2) 

Therefore `Poseidon([tip, l1, l2_zero])` ≠ `Poseidon([tip, l1, l2_zero, l1_data_zero])`. The hash the user signed (computed at the gateway as `AllResources`) differs from the hash recomputed from the deserialized object (computed as `L1Gas`).

**Execution-mode divergence:**

`ValidResourceBounds::get_gas_vector_computation_mode` returns `All` for `AllResources` and `NoL2Gas` for `L1Gas`: [4](#0-3) 

`GasVectorComputationMode::All` enforces per-resource bounds (L1, L2, L1DataGas separately). `NoL2Gas` converts everything to a single discounted L1 gas figure: [5](#0-4) 

A transaction with `AllResources` and `l2_gas.max_amount = 0` that consumes any L2 gas is **reverted** on the proposer (bounds exceeded). After protobuf round-trip the same transaction becomes `L1Gas`; L2 gas is folded into the L1 gas total and the zero L2 bound is never checked, so the transaction **succeeds** on a validator that re-executes from the deserialized form.

**Gateway always produces `AllResources`:**

`RpcTransaction` carries `AllResourceBounds` directly: [6](#0-5) 

The gateway's stateless validator accepts any `AllResources` transaction whose total max fee is non-zero, so a transaction with only `l1_gas > 0` (and zero `l2_gas`, zero `l1_data_gas`) passes validation and is stored with the 4-element hash. The collapse only occurs on the P2P deserialization path.

### Impact Explanation

Two concrete impacts:

1. **Wrong transaction hash bound to the executable object.** Any node that reconstructs the transaction from protobuf and recomputes the hash (e.g., for signature verification or mempool deduplication) obtains a hash that does not match the user's signature. The transaction is either silently accepted under the wrong identity or incorrectly rejected.

2. **Wrong execution result / state divergence.** A proposer that holds the transaction as `AllResources` reverts it when it consumes L2 gas beyond the zero bound. A validator that received the same transaction via P2P and reconstructed it as `L1Gas` executes it successfully. The two nodes compute different state roots, causing consensus failure or an incorrect receipt being committed.

Both map to: *"Wrong state, receipt, event … or revert result from blockifier/syscall/execution logic for accepted input"* and *"Transaction conversion or signature/hash logic binds the wrong … hash, type, or executable payload."*

### Likelihood Explanation

The trigger is unprivileged: any user can submit a V3 `AllResources` transaction with `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0`. The gateway accepts it (l1_gas is non-zero, so `max_possible_fee > 0`). The collapse occurs on every P2P round-trip of such a transaction. The execution divergence is triggered whenever the transaction's Cairo code consumes any L2 gas — a common occurrence for Cairo 1 contracts.

### Recommendation

The protobuf deserializer must preserve the original variant. The presence of the `l1_data_gas` field in the wire message (even if its value is zero) should be the discriminant, not the numeric value of the bounds:

```rust
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    // Genuine pre-0.13.3 message: no data-gas field at all.
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    // Post-0.13.3 message: field present (possibly zero) → AllResources.
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

Remove the `TODO` comment and enforce the assertion once 0.13.2 support is dropped.

### Proof of Concept

1. User submits an invoke V3 transaction with `AllResourceBounds { l1_gas: {max_amount:1000, max_price:1}, l2_gas: {0,0}, l1_data_gas: {0,0} }`.
2. Gateway computes `H1 = Poseidon([tip, l1_packed, l2_zero_packed, l1_data_zero_packed])` (4-element hash) and stores `InternalRpcTransaction { tx, tx_hash: H1 }`.
3. Transaction is serialized to protobuf for P2P propagation. The `From<ValidResourceBounds> for protobuf::ResourceBounds` serializer emits `l1_data_gas = ResourceBounds::default()` (zero).
4. Receiving validator deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → reconstructed as `ValidResourceBounds::L1Gas(l1_gas)`.
5. Validator recomputes hash: `H2 = Poseidon([tip, l1_packed, l2_zero_packed])` (3-element hash). `H2 ≠ H1`.
6. If the validator verifies the signature against `H2`, verification fails (user signed `H1`). If the validator executes with `L1Gas` variant, `GasVectorComputationMode::NoL2Gas` is used; any L

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

**File:** crates/starknet_api/src/transaction_hash.rs (L216-226)
```rust
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
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

**File:** crates/blockifier/src/fee/fee_utils.rs (L40-62)
```rust
impl GasVectorToL1GasForFee for GasVector {
    fn to_l1_gas_for_fee(
        &self,
        gas_prices: &GasPriceVector,
        versioned_constants: &VersionedConstants,
    ) -> GasAmount {
        // Discounted gas converts data gas to L1 gas. Add L2 gas using conversion ratio.
        let discounted_l1_gas = to_discounted_l1_gas(
            gas_prices.l1_gas_price,
            gas_prices.l1_data_gas_price.into(),
            self.l1_gas,
            self.l1_data_gas,
        );
        discounted_l1_gas
            .checked_add(versioned_constants.sierra_gas_to_l1_gas_amount_round_up(self.l2_gas))
            .unwrap_or_else(|| {
                panic!(
                    "L1 gas amount overflowed: addition of converted L2 gas ({}) to discounted \
                     gas ({}) resulted in overflow.",
                    self.l2_gas, discounted_l1_gas
                );
            })
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L167-175)
```rust
impl RpcTransaction {
    implement_ref_getters!(
        (nonce, Nonce),
        (resource_bounds, AllResourceBounds),
        (signature, TransactionSignature),
        (tip, Tip),
        (nonce_data_availability_mode, DataAvailabilityMode),
        (fee_data_availability_mode, DataAvailabilityMode)
    );
```
