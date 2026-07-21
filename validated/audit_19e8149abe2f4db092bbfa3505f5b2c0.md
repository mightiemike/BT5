### Title
Protobuf `ValidResourceBounds` Deserialization Silently Collapses `AllResources` to `L1Gas`, Producing a Wrong Transaction Hash Preimage - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf-to-Rust converter for `ValidResourceBounds` (used in the P2P state-sync path) applies a value-based heuristic to decide which enum variant to reconstruct. When an `AllResources` transaction carries zero-valued L2 and L1DataGas bounds, the converter silently produces `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound felts for each variant, the transaction hash recomputed from the deserialized body diverges from the hash that was originally signed and stored on the sending node.

### Finding Description

**Root cause — wrong variant selection in protobuf deserialization**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs`:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

A post-0.13.3 transaction that legitimately uses `AllResources` with zero L2 and L1DataGas bounds (e.g., a user who only cares about L1 gas and sets the others to zero) is indistinguishable from a pre-0.13.3 `L1Gas` transaction at the wire level. The converter therefore reconstructs the wrong variant.

The codebase itself acknowledges this ambiguity in the consensus-path test:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
``` [2](#0-1) 

**Hash divergence — different number of felts hashed per variant**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` produces a Poseidon hash over a different number of elements depending on the variant:

- `ValidResourceBounds::L1Gas` → hashes `[tip, pack(L1_GAS, bounds), pack(L2_GAS, zero)]` — **3 elements**
- `ValidResourceBounds::AllResources` → hashes `[tip, pack(L1_GAS, bounds), pack(L2_GAS, zero), pack(L1_DATA_GAS, zero)]` — **4 elements**

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

The Cairo OS `hash_fee_fields` always asserts `n_resource_bounds = 3` and hashes all three resource bounds unconditionally:

```cairo
with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
    assert n_resource_bounds = 3;
}
``` [4](#0-3) 

This means the OS always computes the hash with 4 elements (tip + 3 resources), while the Rust `L1Gas` path computes it with 3 elements. The two hashes are structurally different Poseidon outputs and will never collide.

**Execution-mode divergence — wrong `GasVectorComputationMode`**

`ValidResourceBounds::L1Gas` maps to `GasVectorComputationMode::NoL2Gas`, while `AllResources` maps to `GasVectorComputationMode::All`:

```rust
pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
    match self {
        Self::AllResources(_) => GasVectorComputationMode::All,
        Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
    }
}
``` [5](#0-4) 

A transaction deserialized as `L1Gas` will skip L2 gas accounting entirely during re-execution or proving, producing a different gas vector and potentially a different execution outcome than the original.

### Impact Explanation

When a synced node receives a post-0.13.3 transaction with zero L2/L1DataGas bounds over P2P, it stores the body with the wrong variant (`L1Gas`). Any subsequent operation that recomputes the transaction hash from the stored body — including OS/prover execution, which calls `hash_fee_fields` with the body's resource bounds — will produce a hash that differs from the originally signed and stored hash. This constitutes:

- **Wrong hash bound to the executable payload**: the hash the OS computes from the deserialized body does not match the hash the signer committed to, breaking the signature-to-execution binding.
- **Wrong execution result**: the gas vector computation mode changes from `All` to `NoL2Gas`, altering fee accounting and potentially the revert/success outcome.

### Likelihood Explanation

Any post-0.13.3 transaction where the user sets both `l2_gas` and `l1_data_gas` to fully-zero `ResourceBounds` (zero `max_amount` and zero `max_price_per_unit`) triggers this path. This is a realistic configuration for users who only intend to pay L1 gas. The condition is checked on every P2P-synced V3 transaction.

### Recommendation

The variant selection must be based on the transaction's protocol version or an explicit wire-level discriminator, not on the runtime values of the bounds. One approach: add a boolean flag to the protobuf `ResourceBounds` message (e.g., `bool has_all_resources = 4`) that is set by the serializer and read by the deserializer. Alternatively, always deserialize as `AllResources` for any transaction that carries a non-`None` `l1_data_gas` field, regardless of its value:

```rust
Ok(if value.l1_data_gas.is_none() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This preserves backward compatibility (pre-0.13.3 peers omit `l1_data_gas`) while correctly distinguishing post-0.13.3 transactions that happen to carry zero-valued bounds.

### Proof of Concept

1. Construct a post-0.13.3 `InvokeTransactionV3` with `ValidResourceBounds::AllResources(AllResourceBounds { l1_gas: <nonzero>, l2_gas: ResourceBounds::default(), l1_data_gas: ResourceBounds::default() })`.
2. Compute its hash via `get_invoke_transaction_v3_hash` → call it **H_original** (uses 4-element Poseidon: tip + L1_GAS + L2_GAS + L1_DATA_GAS).
3. Serialize to `protobuf::ResourceBounds` via `From<ValidResourceBounds>` — `l1_data_gas` is set to `ResourceBounds::default().into()` (all zeros).
4. Deserialize via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` — because `l1_data_gas.is_zero() && l2_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`.
5. Recompute the hash from the deserialized body → call it **H_deserialized** (uses 3-element Poseidon: tip + L1_GAS + L2_GAS).
6. Assert `H_original != H_deserialized` — the two Poseidon hashes over inputs of different lengths are structurally distinct. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-44)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L123-125)
```text
    with_attr error_message("Invalid number of resource bounds: {n_resource_bounds}.") {
        assert n_resource_bounds = 3;
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

**File:** crates/apollo_protobuf/src/proto/p2p/proto/transaction.proto (L13-19)
```text
message ResourceBounds {
    ResourceLimits l1_gas = 1;
    // This can be None only in transactions that don't support l2 gas.
    // Starting from 0.14.0, MempoolTransaction and ConsensusTransaction shouldn't have None here.
    optional ResourceLimits l1_data_gas = 2;
    ResourceLimits l2_gas = 3;
}
```
