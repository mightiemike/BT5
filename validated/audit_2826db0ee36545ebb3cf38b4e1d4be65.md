### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserialization of `ValidResourceBounds` silently converts `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` to `L1Gas(X)`. Because `get_tip_resource_bounds_hash` includes a `concat(L1_DATA_GAS, 0)` element in the Poseidon preimage for `AllResources` but omits it for `L1Gas`, the transaction hash computed from the deserialized representation differs from the hash computed at submission time. Nodes that receive the transaction via P2P block sync therefore hold a different `resource_bounds` variant than the originating node, producing an inconsistent hash and wrong fee-computation mode for the same transaction.

---

### Finding Description

**Step 1 – Submission (gateway path)**

A user submits an Invoke V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway converts it to `InternalRpcInvokeTransactionV3` (which always stores `AllResourceBounds`) and computes hash **H1** via `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash(ValidResourceBounds::AllResources(X, 0, 0))`.

Inside `get_tip_resource_bounds_hash` the `AllResources` branch appends `concat(L1_DATA_GAS, 0)` to the Poseidon input:

```
H1 = Poseidon(tip, concat(L1_GAS, X), concat(L2_GAS, 0), concat(L1_DATA_GAS, 0))
``` [1](#0-0) 

**Step 2 – P2P block sync (protobuf path)**

When a syncing node receives the block, `InvokeTransactionV3` (which carries `ValidResourceBounds`) is deserialized from protobuf via `ValidResourceBounds::try_from(protobuf::ResourceBounds)`:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← silently drops AllResources
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [2](#0-1) 

Because `l2_gas = 0` and `l1_data_gas = 0`, the deserialized value is `L1Gas(X)` — a different enum variant.

**Step 3 – Hash divergence**

If the syncing node recomputes the hash from the deserialized data (e.g., for `validate_transaction_hash`, RPC simulation, or fee estimation), it calls `get_tip_resource_bounds_hash(ValidResourceBounds::L1Gas(X))`, which takes the `L1Gas` branch and omits `concat(L1_DATA_GAS, 0)`:

```
H2 = Poseidon(tip, concat(L1_GAS, X), concat(L2_GAS, 0))
```

`H1 ≠ H2` — the Poseidon inputs have different lengths. [3](#0-2) 

**Step 4 – Wrong fee-computation mode**

`ValidResourceBounds::L1Gas` maps to `GasVectorComputationMode::NoL2Gas`, while `AllResources` maps to `GasVectorComputationMode::All`. Fee estimation and simulation on the syncing node therefore use the wrong computation mode for this transaction. [4](#0-3) 

**Root cause summary**

The protobuf serializer for `ValidResourceBounds::AllResources` emits `l1_data_gas = 0` when the field is zero, and the deserializer interprets `l1_data_gas.is_zero() && l2_gas.is_zero()` as the legacy `L1Gas` variant. There is no version tag or type discriminator in the wire format to distinguish a modern `AllResources` transaction with zero data-gas from a legacy `L1Gas` transaction. [5](#0-4) 

---

### Impact Explanation

**High. Transaction conversion or signature/hash logic binds the wrong hash or executable payload.**

- A syncing node stores `resource_bounds = L1Gas(X)` but `tx_hash = H1` (transmitted). Any subsequent call to `validate_transaction_hash` or hash recomputation produces `H2 ≠ H1`, causing the node to treat a valid transaction as having an invalid hash.
- RPC responses from the syncing node return `resource_bounds: {l1_gas: X}` (two-resource map) for a hash that was computed over a three-resource preimage — an authoritative-looking wrong value.
- Fee estimation and tracing on the syncing node use `NoL2Gas` mode instead of `All` mode, producing wrong gas/fee results for the transaction.

---

### Likelihood Explanation

**Low–Medium.** The trigger requires an Invoke V3 transaction with `AllResourceBounds` where both `l2_gas` and `l1_data_gas` are zero. The gateway accepts such transactions (the stateless validator only requires at least one non-zero resource bound, and `l1_gas` alone satisfies that). Any user can craft this transaction intentionally or accidentally (e.g., a transaction that only consumes L1 gas). The divergence is then deterministic for every syncing node that processes the block via P2P sync.

---

### Recommendation

1. **Fix the deserializer**: In `ValidResourceBounds::try_from(protobuf::ResourceBounds)`, always produce `AllResources` when all three fields are present in the wire message, regardless of whether `l2_gas` and `l1_data_gas` are zero. The `L1Gas` branch should only be taken when `l1_data_gas` is absent (`None`) in the protobuf message — which is the actual legacy indicator.

2. **Add a discriminator**: Alternatively, add an explicit `resource_bounds_version` field to `protobuf::ResourceBounds` so the deserializer can distinguish legacy `L1Gas` from modern `AllResources` with zero data-gas.

3. **Enforce invariant in `InvokeTransactionV3`**: Add a post-deserialization check that rejects `ValidResourceBounds::L1Gas` for V3 transactions (since V3 always uses `AllResources`).

---

### Proof of Concept

```
1. Submit Invoke V3 to gateway:
   resource_bounds = { l1_gas: {max_amount: 1000, max_price_per_unit: 1},
                       l2_gas: {max_amount: 0,    max_price_per_unit: 0},
                       l1_data_gas: {max_amount: 0, max_price_per_unit: 0} }

2. Gateway computes H1 = Poseidon(tip,
       concat(L1_GAS, 1000, 1),
       concat(L2_GAS, 0, 0),
       concat(L1_DATA_GAS, 0, 0))   ← 4 elements

3. Transaction included in block N.

4. Syncing node receives block N via P2P sync.
   protobuf::ResourceBounds { l1_gas: (1000,1), l2_gas: (0,0), l1_data_gas: (0,0) }
   → ValidResourceBounds::try_from: l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → produces ValidResourceBounds::L1Gas({max_amount:1000, max_price_per_unit:1})

5. Syncing node recomputes hash:
   H2 = Poseidon(tip,
       concat(L1_GAS, 1000, 1),
       concat(L2_GAS, 0, 0))        ← 3 elements

6. H1 ≠ H2  →  validate_transaction_hash fails / RPC returns wrong resource_bounds.
```

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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
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

**File:** crates/starknet_api/src/transaction/fields.rs (L416-420)
```rust
    pub fn get_gas_vector_computation_mode(&self) -> GasVectorComputationMode {
        match self {
            Self::AllResources(_) => GasVectorComputationMode::All,
            Self::L1Gas(_) => GasVectorComputationMode::NoL2Gas,
        }
```
